"""
conftest.py — Shared fixtures for sage tests.

Provides isolated nook and knowledge graph instances so tests never
touch the user's real data or leak temp files on failure.

HOME is redirected to a temp directory at module load time — before any
sage imports — so that module-level initialisations (e.g.
``_kg = KnowledgeGraph()`` in mcp_server) write to a throwaway location
instead of the real user profile.

Real-environment guard (ADR-0095):
  At module load we fingerprint the operator's real crontab (pre-shim) and
  the file-set of the real ~/.sage store.  After the suite completes, the
  session-scoped ``_guard_real_env`` fixture re-checks both fingerprints and
  calls ``pytest.fail`` if anything mutated — naming ADR-0095 so the failure
  is immediately actionable.

  Additionally, a no-op ``crontab`` shim is prepended to PATH (POSIX only)
  before any subprocess — including install.sh invocations — runs.  The shim
  absorbs ``crontab <file>`` / ``crontab -r`` calls silently, so the real
  crontab is never touched by the suite.  Tests that need to assert real
  crontab content must use an explicit fixture that removes the shim from PATH
  (document the override, citing ADR-0095).
"""

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

# ── eval_baseline skip-by-default ─────────────────────────────────────────
# Tests marked @pytest.mark.eval_baseline write the git-tracked baseline
# artifact (tests/eval/tier0_baseline.json) and must NOT run on every
# normal pytest invocation.  They are skipped unless the caller either
# sets RUN_EVAL_BASELINE=1 in the environment or passes -m eval_baseline
# explicitly.  (FIX 5: prevents baseline noise in routine CI runs.)
_RUN_EVAL_BASELINE = os.environ.get("RUN_EVAL_BASELINE", "").strip() == "1"

# ── eval (WI-7b) skip-by-default ──────────────────────────────────────────
# Tests marked @pytest.mark.eval run the full non-regression eval harness
# (WI-7b: fixed question set + store-growth + decay/consolidation + recall
# non-regression check).  They are skipped unless the caller sets RUN_EVAL=1
# or passes -m eval explicitly.
_RUN_EVAL = os.environ.get("RUN_EVAL", "").strip() == "1"

# ── Wing registry test config ──────────────────────────────────────────
# Phase 4 wires require_registered_wing into every drawer-write path so
# typo'd wings can't silently create phantom data on production nooks.
# The test suite uses ad-hoc wing names left over from earlier
# (``project``, ``notes``, single-letter ``a``/``b``, …); rather than
# bypass the gate with a permissive lambda, point the gate at a
# test-scoped wing_config.json that REGISTERS every wing the tests use.
# Mirrors how a production user would `sage wing add` each wing
# they actually mine into.
_TEST_WING_CONFIG = Path(__file__).parent / "fixtures" / "wing_config.json"
if _TEST_WING_CONFIG.is_file():
    os.environ["SAGE_WING_CONFIG"] = str(_TEST_WING_CONFIG)

# ── Isolate HOME before any sage imports ──────────────────────────
_original_env = {}
_session_tmp = tempfile.mkdtemp(prefix="sage_session_")

for _var in ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
    _original_env[_var] = os.environ.get(_var)

# ── C2: Crontab shim (ADR-0095) — POSIX only, installed BEFORE HOME redirect
# so the real crontab binary path is resolved from the pristine PATH.
# The shim dir is inside the session temp tree so it is cleaned up with it.
# Any test that legitimately needs the real crontab behaviour must override
# PATH in its own fixture (cite ADR-0095 in that override).
_CRONTAB_SHIM_DIR: Path | None = None
_REAL_CRONTAB_BIN: str | None = None  # absolute path resolved pre-shim

if os.name == "posix":
    _REAL_CRONTAB_BIN = shutil.which("crontab")
    if _REAL_CRONTAB_BIN:
        # Create the shim directory and write a no-op crontab shim script.
        _shim_dir = Path(_session_tmp) / "_shims"
        _shim_dir.mkdir(parents=True, exist_ok=True)
        _shim_script = _shim_dir / "crontab"
        _shim_script.write_text(
            "#!/bin/sh\n"
            "# sage test-suite no-op crontab shim (ADR-0095)\n"
            "# -l → print empty crontab; any write/remove arg → absorb silently.\n"
            'if [ "$1" = "-l" ]; then\n'
            "  exit 0\n"
            "fi\n"
            "# Consume stdin (in case caller pipes via `crontab -`)\n"
            "cat > /dev/null\n"
            "exit 0\n"
        )
        _shim_script.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
        _CRONTAB_SHIM_DIR = _shim_dir
        # Prepend the shim dir to PATH so every subprocess (including install.sh)
        # resolves our shim instead of the real crontab binary.
        os.environ["PATH"] = str(_shim_dir) + ":" + os.environ.get("PATH", "")

# ── C1: Fingerprint the real environment BEFORE HOME redirect ──────────────
# Real crontab text (captured via the absolute binary path, bypassing the shim).
_REAL_CRONTAB_BEFORE: bytes | None = None
if os.name == "posix" and _REAL_CRONTAB_BIN:
    try:
        _result = subprocess.run(
            [_REAL_CRONTAB_BIN, "-l"],
            capture_output=True,
            timeout=5,
        )
        # exit 1 + "no crontab" stderr is normal for a user with no crontab — treat
        # that the same as empty (b"") so we don't false-positive on a clean machine.
        _REAL_CRONTAB_BEFORE = _result.stdout
    except Exception:
        pass  # crontab absent or broken — guard skips gracefully

# Real ~/.sage store fingerprint — FULL recursive tree snapshot with per-class
# compare semantics (PR #42 review P2-2/P2-3: the old 7-file allow-list missed
# every other store artifact, and an absent real ~/.sage was not tracked at
# all, so a test CREATING the real store passed silently).
#
# Path classes (fnmatch on relpath, first match wins):
#   CHURN  — legit concurrent sessions create/mutate these constantly
#            (telemetry, WAL, locks, hook state, session markers, nook
#            segment churn): tolerate everything.
#   LIVE_DB — long-lived stores a concurrent session may legitimately grow
#            or rewrite (chroma sqlite, live KG, hallways, tunnels, entity
#            registries): creation/deletion FAIL; in-place truncation below
#            10% of the snapshot FAILS (wipe signature, PMA-2); growth or
#            mtime-only mutation tolerated.
#   STATIC — install/incident-only files (config, identity, wing config,
#            people map): ANY change fails — creation,
#            deletion, or size+mtime mutation.
#   UNKNOWN — any path in neither the snapshot nor a glob class: creation
#            fails (STATIC semantics by default).
_SAGE_CHURN_GLOBS = (
    "telemetry",
    "telemetry/*",
    "wal",
    "wal/*",
    "locks",
    "locks/*",
    "hook_state",
    "hook_state/*",
    "state",
    "state/*",
    "nook",  # segment dirs, HNSW churn; chroma.sqlite3 carved out below
    "nook/*",
    "nook.backup",
    "nook.backup/*",
    "quarantine-*/*",
    "quarantine-*",
    "current_wing",
    "last_keeper_dispatch",
    "tier0-last-hash*",
    "autonomy-run.json",
    "autonomy-reporting-eval.log",
)
# SQLite stores a concurrent legit session may grow/rewrite: size-based
# truncation heuristic only (content churns by design).
_SAGE_LIVE_DB_PATHS = (
    "nook/chroma.sqlite3",
    "knowledge_graph.sqlite3",
)
# Small JSON memory stores: content-hash compared (PR #46 review — a
# similar-size overwrite must not pass as "live growth"). A hash mismatch
# fails loudly; the failure message names the concurrent-session alternate
# cause and the re-run remedy.
_SAGE_JSON_STORE_PATHS = (
    "hallways.json",
    "tunnels.json",
    "known_entities.json",
    "entity_registry.json",
)
_SAGE_TREE_CAP = 20000  # fail-open above this many entries (warn loudly)

_REAL_SAGE_DIR: Path | None = None
_REAL_SAGE_EXISTED_BEFORE: bool = False
# relpath → (kind, sig_a, sig_b); None means the guard is disabled (cap hit).
#   kind "file": (size, mtime) · "hash": (sha256-hex, 0.0)
#   kind "dir":  (0.0, 0.0)    · "link": (readlink-target, 0.0)
_REAL_SAGE_TREE_BEFORE: "dict[str, tuple] | None" = None


def _classify_sage_path(relpath: str) -> str:
    """Classify a ~/.sage-relative path: 'live_db', 'json_store', 'churn', 'static'."""
    import fnmatch as _fnmatch

    if relpath in _SAGE_LIVE_DB_PATHS:
        return "live_db"
    if relpath in _SAGE_JSON_STORE_PATHS:
        return "json_store"
    for g in _SAGE_CHURN_GLOBS:
        if _fnmatch.fnmatch(relpath, g):
            return "churn"
    return "static"


def _sage_tree_entry(root: Path, p: Path) -> "tuple[str, tuple] | None":
    """One snapshot entry: (relpath, (kind, sig_a, sig_b)) or None on lstat error."""
    try:
        st = p.lstat()
    except OSError:
        return None
    rel = str(p.relative_to(root)).replace(os.sep, "/")
    import stat as _stat

    if _stat.S_ISLNK(st.st_mode):
        try:
            target = os.readlink(p)
        except OSError:
            target = "<unreadable>"
        return rel, ("link", target, 0.0)
    if _stat.S_ISDIR(st.st_mode):
        return rel, ("dir", 0.0, 0.0)
    if _classify_sage_path(rel) == "json_store":
        import hashlib as _hashlib

        try:
            digest = _hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            digest = "<unreadable>"
        return rel, ("hash", digest, 0.0)
    return rel, ("file", float(st.st_size), float(st.st_mtime))


def _snapshot_sage_tree(root: Path) -> "dict[str, tuple] | None":
    """Recursive lstat snapshot of the real store — files AND directories.

    Directories are recorded (PR #46 review: empty-dir creation/deletion and
    dir-symlink retargets must be visible), symlinks are recorded by their
    own target (never followed: followlinks=False + lstat, so a link inside
    ~/.sage can never pull an external tree into the diff). Returns None
    (guard disabled, loud warning) above _SAGE_TREE_CAP entries.
    """
    snap: dict[str, tuple] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            entry = _sage_tree_entry(root, Path(dirpath) / name)
            if entry is None:
                continue
            snap[entry[0]] = entry[1]
            if len(snap) >= _SAGE_TREE_CAP:
                import warnings

                warnings.warn(
                    f"real ~/.sage reached the {_SAGE_TREE_CAP}-entry cap — "
                    "ADR-0095 store guard DISABLED for this run",
                    stacklevel=2,
                )
                return None
    return snap


_real_home_for_guard = _original_env.get("HOME") or _original_env.get("USERPROFILE")
if _real_home_for_guard:
    _REAL_SAGE_DIR = Path(_real_home_for_guard) / ".sage"
    _REAL_SAGE_EXISTED_BEFORE = _REAL_SAGE_DIR.exists()
    if _REAL_SAGE_EXISTED_BEFORE:
        _REAL_SAGE_TREE_BEFORE = _snapshot_sage_tree(_REAL_SAGE_DIR)
    else:
        # Absent store IS a tracked state (P2-2): a suite that creates the
        # real ~/.sage must fail, not pass silently.
        _REAL_SAGE_TREE_BEFORE = {}

# ── Apply HOME redirect (now safe — fingerprints captured above) ───────────
os.environ["HOME"] = _session_tmp
os.environ["USERPROFILE"] = _session_tmp
os.environ["HOMEDRIVE"] = os.path.splitdrive(_session_tmp)[0] or "C:"
os.environ["HOMEPATH"] = os.path.splitdrive(_session_tmp)[1] or _session_tmp

# Now it is safe to import sage_mcp modules that trigger initialisation.
import chromadb  # noqa: E402
import pytest  # noqa: E402

from sage_mcp.config import SageConfig  # noqa: E402
from sage_mcp.knowledge_graph import KnowledgeGraph  # noqa: E402

# Redirect ChromaDB's ONNX model cache back to the real user's cache so tests
# don't re-download the 79 MB model on every run. The HOME redirect above
# would otherwise point ONNXMiniLM_L6_V2.DOWNLOAD_PATH at the empty temp dir.
try:
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import (  # noqa: E402
        ONNXMiniLM_L6_V2,
    )

    _real_home = _original_env.get("USERPROFILE") or _original_env.get("HOME")
    if _real_home:
        _real_cache = Path(_real_home) / ".cache" / "chroma" / "onnx_models" / "all-MiniLM-L6-v2"
        if _real_cache.exists():
            ONNXMiniLM_L6_V2.DOWNLOAD_PATH = _real_cache
except ImportError:
    pass


def pytest_collection_modifyitems(config, items):
    """Skip eval_baseline and eval tests unless explicitly enabled."""
    expr = config.option.markexpr if hasattr(config.option, "markexpr") else ""

    # ── eval_baseline gate ────────────────────────────────────────────────
    # Skipped unless RUN_EVAL_BASELINE=1 or -m eval_baseline (or broader expr).
    if not (_RUN_EVAL_BASELINE or "eval_baseline" in expr):
        skip_baseline = pytest.mark.skip(
            reason="eval_baseline test skipped in normal runs; "
            "set RUN_EVAL_BASELINE=1 or use -m eval_baseline to run"
        )
        for item in items:
            if item.get_closest_marker("eval_baseline"):
                item.add_marker(skip_baseline)

    # ── eval (WI-7b) gate ────────────────────────────────────────────────
    # Skipped unless RUN_EVAL=1 or -m eval (or broader expr).
    # Use an exact word-boundary check (\beval\b) so that a markexpr of
    # "eval_baseline" does NOT accidentally enable the `eval` gate — the
    # substring "eval" in "eval_baseline" would match the old `"eval" in expr`
    # check and expose eval tests in eval_baseline-only runs.
    import re as _re

    _eval_marker_active = bool(_re.search(r"\beval\b", expr))
    if not (_RUN_EVAL or _eval_marker_active):
        skip_eval = pytest.mark.skip(
            reason="eval (WI-7b) test skipped in normal runs; set RUN_EVAL=1 or use -m eval to run"
        )
        for item in items:
            if item.get_closest_marker("eval"):
                item.add_marker(skip_eval)


@pytest.fixture(autouse=True)
def _reset_mcp_cache():
    """Reset cached MCP state between tests without importing mcp_server.

    If sage_mcp.mcp_server is already imported, close/clear its KG cache and
    Chroma client cache. If it has not been imported, leave it unloaded so
    fork/spawn-based tests do not inherit extra Chroma/SQLite state.
    """

    def _clear_cache():
        try:
            import sys

            mcp_server = sys.modules.get("sage_mcp.mcp_server")
            if mcp_server is not None:
                for kg in list(getattr(mcp_server, "_kg_by_path", {}).values()):
                    close = getattr(kg, "close", None)
                    if close is not None:
                        try:
                            close()
                        except Exception:
                            pass

                if hasattr(mcp_server, "_kg_by_path"):
                    mcp_server._kg_by_path.clear()

                mcp_server._client_cache = None
                mcp_server._collection_cache = None
        except AttributeError:
            pass

        try:
            # Reset the per-process quarantine gate so tests don't leak
            # state through ChromaBackend._quarantined_paths.
            from sage_mcp.backends.chroma import ChromaBackend

            ChromaBackend._quarantined_paths.clear()
        except (ImportError, AttributeError):
            pass

    _clear_cache()
    yield
    _clear_cache()


@pytest.fixture(scope="session", autouse=True)
def _isolate_home():
    """Ensure HOME points to a temp dir for the entire test session.

    The env vars were already set at module level (above) so that
    module-level initialisations are captured.  This fixture simply
    restores the originals on teardown and cleans up the temp dir.
    """
    yield
    for var, orig in _original_env.items():
        if orig is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = orig
    shutil.rmtree(_session_tmp, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _guard_real_env():
    """Post-suite guard: fail if the real crontab or real ~/.sage changed (ADR-0095).

    Runs AFTER the full session completes (yield is at the top).  On mismatch,
    calls pytest.fail with a human-readable diff naming ADR-0095 so the
    failure is immediately actionable.

    Skipped silently on non-POSIX (crontab guard) or when the real home is
    unresolvable (both guards).  The crontab check uses the absolute binary path
    captured before the shim was installed — it reads the REAL crontab, not the
    shim.
    """
    yield  # suite runs here

    # ── Crontab guard ──────────────────────────────────────────────────────
    if os.name == "posix" and _REAL_CRONTAB_BIN and _REAL_CRONTAB_BEFORE is not None:
        try:
            after_result = subprocess.run(
                [_REAL_CRONTAB_BIN, "-l"],
                capture_output=True,
                timeout=5,
            )
            after_bytes = after_result.stdout
            if after_bytes != _REAL_CRONTAB_BEFORE:
                before_text = _REAL_CRONTAB_BEFORE.decode(errors="replace")
                after_text = after_bytes.decode(errors="replace")
                pytest.fail(
                    "ADR-0095 VIOLATION: real crontab was mutated by the test suite.\n"
                    f"BEFORE:\n{before_text!r}\n"
                    f"AFTER:\n{after_text!r}\n"
                    "Check for a test that ran `crontab` without going through the PATH shim.",
                    pytrace=False,
                )
        except Exception:
            pass  # fail-open: crontab check is best-effort

    # ── ~/.sage store guard ────────────────────────────────────────────────
    if _REAL_SAGE_DIR is None or _REAL_SAGE_TREE_BEFORE is None:
        return  # unresolvable home, or guard disabled at the tree cap

    # Whole-dir matrix (P2-2): absent→present = FAIL; absent→absent = clean.
    if not _REAL_SAGE_EXISTED_BEFORE:
        if _REAL_SAGE_DIR.exists():
            created = sorted(str(p.relative_to(_REAL_SAGE_DIR)) for p in _REAL_SAGE_DIR.rglob("*"))[
                :40
            ]
            pytest.fail(
                "ADR-0095 VIOLATION: real ~/.sage did not exist before the suite "
                "and was CREATED during it.\n"
                "Created tree (first 40 entries):\n  " + "\n  ".join(created) + "\n"
                "Check for a test that escaped the redirected HOME — or, if you ran "
                "`sage init` in another session mid-suite, re-run the suite.",
                pytrace=False,
            )
        return

    after = _snapshot_sage_tree(_REAL_SAGE_DIR)
    if after is None:
        return  # cap hit post-suite — already warned

    diffs: list[str] = []
    before_paths = set(_REAL_SAGE_TREE_BEFORE)
    after_paths = set(after)

    for rel in sorted(after_paths - before_paths):
        cls = _classify_sage_path(rel)
        if cls != "churn":
            diffs.append(f"  CREATED [{cls}] {rel}")
    for rel in sorted(before_paths - after_paths):
        cls = _classify_sage_path(rel)
        if cls != "churn":
            diffs.append(f"  DELETED [{cls}] {rel}")
    for rel in sorted(before_paths & after_paths):
        cls = _classify_sage_path(rel)
        if cls == "churn":
            continue
        b_kind, b_a, b_b = _REAL_SAGE_TREE_BEFORE[rel]
        a_kind, a_a, a_b = after[rel]
        if b_kind != a_kind:
            # file→dir, dir→link, etc. — always a violation outside churn.
            diffs.append(f"  KIND-CHANGED [{cls}] {rel} ({b_kind} → {a_kind})")
            continue
        if b_kind == "link":
            if a_a != b_a:
                diffs.append(f"  RETARGETED [{cls}] {rel} ({b_a!r} → {a_a!r})")
        elif b_kind == "dir":
            pass  # existence already compared via the created/deleted sets
        elif b_kind == "hash":
            # JSON memory stores: content-hash equality (PR #46 review — a
            # similar-size overwrite is corruption, not growth).
            if a_a != b_a:
                diffs.append(
                    f"  CONTENT-CHANGED [json_store] {rel} (sha256 {b_a[:12]}… → {a_a[:12]}…)"
                )
        elif cls == "live_db":
            # Growth/mtime mutation tolerated; truncation below 10% of the
            # snapshot is the wipe signature (PMA-2).
            if b_a > 0 and a_a < b_a * 0.10:
                diffs.append(
                    f"  TRUNCATED [live_db] {rel} "
                    f"({int(b_a)} → {int(a_a)} bytes; <10% of pre-suite size)"
                )
        else:  # static file
            if (a_a, a_b) != (b_a, b_b):
                diffs.append(
                    f"  MUTATED [static] {rel} (size {int(b_a)}→{int(a_a)}, mtime {b_b}→{a_b})"
                )

    if diffs:
        diff_text = "\n".join(diffs)
        pytest.fail(
            "ADR-0095 VIOLATION: real ~/.sage store was mutated by the test suite.\n"
            f"Changed files:\n{diff_text}\n"
            "Check for a test that writes to the real HOME instead of the session "
            "temp dir. (json_store/static hits can also come from a CONCURRENT sage "
            "session writing during this run — if that was you, re-run the suite.)",
            pytrace=False,
        )


@pytest.fixture
def tmp_dir():
    """Create and auto-cleanup a temporary directory."""
    d = tempfile.mkdtemp(prefix="sage_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def nook_path(tmp_dir):
    """Path to an empty nook directory inside tmp_dir."""
    p = os.path.join(tmp_dir, "nook")
    os.makedirs(p)
    return p


@pytest.fixture
def config(tmp_dir, nook_path):
    """A SageConfig pointing at the temp nook."""
    cfg_dir = os.path.join(tmp_dir, "config")
    os.makedirs(cfg_dir)
    import json

    with open(os.path.join(cfg_dir, "config.json"), "w") as f:
        json.dump({"nook_path": nook_path}, f)
    return SageConfig(config_dir=cfg_dir)


@pytest.fixture
def collection(nook_path):
    """A ChromaDB collection pre-seeded in the temp nook."""
    client = chromadb.PersistentClient(path=nook_path)
    col = client.get_or_create_collection("nook_drawers", metadata={"hnsw:space": "cosine"})
    yield col
    client.delete_collection("nook_drawers")
    del client


@pytest.fixture
def seeded_collection(collection):
    """Collection with a handful of representative drawers."""
    collection.add(
        ids=[
            "drawer_proj_backend_aaa",
            "drawer_proj_backend_bbb",
            "drawer_proj_frontend_ccc",
            "drawer_notes_planning_ddd",
        ],
        documents=[
            "The authentication module uses JWT tokens for session management. "
            "Tokens expire after 24 hours. Refresh tokens are stored in HttpOnly cookies.",
            "Database migrations are handled by Alembic. We use PostgreSQL 15 "
            "with connection pooling via pgbouncer.",
            "The React frontend uses TanStack Query for server state management. "
            "All API calls go through a centralized fetch wrapper.",
            "Sprint planning: migrate auth to passkeys by Q3. "
            "Evaluate ChromaDB alternatives for vector search.",
        ],
        metadatas=[
            {
                "wing": "project",
                "room": "backend",
                "source_file": "auth.py",
                "chunk_index": 0,
                "added_by": "miner",
                "filed_at": "2026-01-01T00:00:00",
            },
            {
                "wing": "project",
                "room": "backend",
                "source_file": "db.py",
                "chunk_index": 0,
                "added_by": "miner",
                "filed_at": "2026-01-02T00:00:00",
            },
            {
                "wing": "project",
                "room": "frontend",
                "source_file": "App.tsx",
                "chunk_index": 0,
                "added_by": "miner",
                "filed_at": "2026-01-03T00:00:00",
            },
            {
                "wing": "notes",
                "room": "planning",
                "source_file": "sprint.md",
                "chunk_index": 0,
                "added_by": "miner",
                "filed_at": "2026-01-04T00:00:00",
            },
        ],
    )
    return collection


@pytest.fixture
def kg(tmp_dir):
    """An isolated KnowledgeGraph using a temp SQLite file."""
    db_path = os.path.join(tmp_dir, "test_kg.sqlite3")
    graph = KnowledgeGraph(db_path=db_path)
    yield graph
    graph.close()


@pytest.fixture
def seeded_kg(kg):
    """KnowledgeGraph pre-loaded with sample triples."""
    kg.add_entity("Alice", entity_type="person")
    kg.add_entity("Max", entity_type="person")
    kg.add_entity("swimming", entity_type="activity")
    kg.add_entity("chess", entity_type="activity")

    kg.add_triple("Alice", "parent_of", "Max", valid_from="2015-04-01")
    kg.add_triple("Max", "does", "swimming", valid_from="2025-01-01")
    kg.add_triple("Max", "does", "chess", valid_from="2024-06-01")
    kg.add_triple("Alice", "works_at", "Acme Corp", valid_from="2020-01-01", valid_to="2024-12-31")
    kg.add_triple("Alice", "works_at", "NewCo", valid_from="2025-01-01")

    return kg
