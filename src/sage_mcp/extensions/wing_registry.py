"""Wing registry — explicit wing taxonomy for sage.

Any string used to become a wing on first write; a typo like
"Acme-Ops-V3" instead of "Acme-Ops.V3" would silently create a phantom
wing and search would never find the drawer again. sage rejects
unregistered wings on write so the typo surfaces immediately.

The wing config lives at ``~/.sage/wing_config.json`` (the
user's install). When that file is missing, this module falls back to
the template shipped at the repo root (``wing_config.json``) so the
single-user install can start working without a separate copy step.

Diary writes (``tool_diary_write``) are exempt — they target the
per-agent convention ``wing_<agent_name>`` which is part of the diary
design, not a typo. Callers that should be subject to the check go
through :func:`require_registered_wing`; callers in the diary lane
simply skip the call.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional


def _atomic_write_0600(path: Path, text: str) -> None:
    """Atomically write ``text`` to ``path`` as an owner-only (0600) file.

    The privacy-sensitive wing registry stores absolute repo paths. The temp
    file is created via ``tempfile.mkstemp`` (owner-only at creation, unique
    name → no symlink/pre-existing-temp race) and written through its fd, so the
    content is never momentarily world-readable; the atomic ``os.replace`` means
    a crash mid-write leaves no partial file at the canonical path. The parent
    dir is hardened to 0700. POSIX-only hardening degrades gracefully elsewhere.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except (OSError, NotImplementedError):
        pass
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            os.chmod(tmp, 0o600)  # mkstemp is already 0600; explicit for clarity
        except (OSError, NotImplementedError):
            pass
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


logger = logging.getLogger("nook_mcp")

VALID_WING_TYPES = frozenset({"dev", "project", "knowledge", "ops", "meta", "personal"})

# Wings that look like ``wing_<agent_name>`` are diary auto-generated and
# always allowed. Matching is anchored at the start so a registered wing
# named exactly ``wing_X`` (unusual but possible) still passes validation
# via the explicit list — auto-allow is only the fallback path.
_DIARY_WING_PREFIX = "wing_"

# Module-level cache + lock. The config is small (one JSON file) so the
# cost is reading it once per process; the lock prevents two concurrent
# reads from clobbering each other on first load.
_cache_lock = threading.Lock()
_cached_config: Optional[dict] = None
_cached_config_path: Optional[Path] = None


class WingNotRegisteredError(ValueError):
    """Raised when a write targets a wing not present in wing_config.json."""

    def __init__(self, wing: str):
        self.wing = wing
        super().__init__(
            f"Wing '{wing}' is not registered. "
            f"Run 'sage wing add {wing} --type <dev|project|knowledge|ops|meta|personal>' first."
        )


def _resolve_config_path() -> Path:
    """Pick the wing_config.json the current process should read.

    Precedence:
      1. ``$SAGE_WING_CONFIG`` env var (test override).
      2. ``~/.sage/wing_config.json`` (the canonical install path).
      3. Repo-root template (``wing_config.json`` next to ``pyproject.toml``)
         as a developer-mode fallback so the registry works in a fresh
         clone before ``sage init`` has been run.
    """
    env_path = os.environ.get("SAGE_WING_CONFIG")
    if env_path:
        return Path(env_path).expanduser()
    install_path = Path.home() / ".sage" / "wing_config.json"
    if install_path.is_file():
        return install_path
    # Walk up from this module's directory to find the repo root template.
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "wing_config.json"
        if candidate.is_file():
            return candidate
    # Nothing found — return the canonical install path; load() will raise.
    return install_path


def _resolve_write_path() -> Path:
    """Pick the wing_config.json to WRITE to.

    Writes always target the canonical user-scope install path
    (``~/.sage/wing_config.json``), never the repo-root developer template —
    so a fresh-clone ``sage bootstrap`` registers user wings under ``~/.sage``
    instead of dirtying the cloned source tree (the repo-root entry in
    ``_resolve_config_path`` is a READ-only fallback). ``$SAGE_WING_CONFIG`` is
    honored first (test override). On the first write, if the user-scope file
    does not exist, it is seeded from the read-resolved template (the repo-root
    ``wing_config.json``) so it starts from the canonical wing-type taxonomy.
    """
    env_path = os.environ.get("SAGE_WING_CONFIG")
    if env_path:
        return Path(env_path).expanduser()
    install_path = Path.home() / ".sage" / "wing_config.json"
    if not install_path.is_file():
        read_path = _resolve_config_path()
        if read_path.is_file() and read_path != install_path:
            template = json.loads(read_path.read_text(encoding="utf-8"))
            # Seed the taxonomy + ONLY framework-internal wings (those whose path
            # lives under ``~/.sage`` — e.g. the Personal user-facts wing and the
            # telemetry wing). NEVER seed maintainer-specific project/dev wings
            # (paths under ``~/dev`` etc.): their presence in a fresh user config
            # leaks names and skips the user's own repos on slug collision. The
            # user registers their own project/dev wings via ``sage bootstrap``.
            sage_root = str(Path.home() / ".sage")
            template["wings"] = {
                slug: entry
                for slug, entry in (template.get("wings") or {}).items()
                if str(Path(str(entry.get("path", ""))).expanduser()).startswith(sage_root)
            }
            # Privacy-sensitive seed: atomic owner-only write (0700 dir / 0600
            # file, no world-readable window, crash-safe — see _atomic_write_0600).
            _atomic_write_0600(install_path, json.dumps(template, indent=2) + "\n")
    return install_path


def load_config(force_reload: bool = False) -> dict:
    """Return the parsed wing_config.json, with module-level caching.

    Pass ``force_reload=True`` to bypass the cache (used by tests and by
    the ``sage wing add`` CLI subcommand after it writes a new
    entry).
    """
    global _cached_config, _cached_config_path
    with _cache_lock:
        if _cached_config is not None and not force_reload:
            return _cached_config
        path = _resolve_config_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"wing_config.json not found at {path}. "
                "Run 'sage init' to create one from the template."
            ) from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"wing_config.json at {path} is not valid JSON: {exc}") from exc
        _cached_config = data
        _cached_config_path = path
        return data


def _invalidate_cache() -> None:
    """Drop the cached config so the next load reads fresh from disk."""
    global _cached_config, _cached_config_path
    with _cache_lock:
        _cached_config = None
        _cached_config_path = None


def registered_wings() -> dict:
    """Return the ``wings`` mapping from the config (read-only view)."""
    return dict(load_config().get("wings") or {})


def is_registered(wing: str) -> bool:
    """Return True if ``wing`` is in the registry OR matches diary pattern.

    The diary fall-through is intentional: ``tool_diary_write`` auto-
    generates ``wing_<agent_name>`` and is exempted from the gate by
    skipping the explicit ``require_registered_wing`` call anyway, but
    this function is also used by audit / inspect tools that should not
    flag diary wings as suspicious.
    """
    if not wing:
        return False
    if wing.startswith(_DIARY_WING_PREFIX):
        return True
    try:
        wings = registered_wings()
    except FileNotFoundError:
        # Config absent — fail open (don't block writes during bootstrap).
        # The CLI surface still surfaces the missing-config error to the
        # user when they run `sage wing list` or similar.
        logger.debug("wing_config.json absent; treating wing as registered")
        return True
    return wing in wings


def require_registered_wing(wing: str) -> None:
    """Raise :class:`WingNotRegisteredError` if ``wing`` isn't registered.

    No-op when the config file is missing (bootstrap path); the surface
    error there belongs to ``sage init`` / ``sage wing``,
    not to every drawer write.
    """
    if is_registered(wing):
        return
    raise WingNotRegisteredError(wing)


def add_wing(slug: str, wing_type: str, path: Optional[str] = None) -> dict:
    """Append a new wing to wing_config.json on disk.

    Raises ``ValueError`` for an invalid slug, invalid type, duplicate slug, or
    when the config file is unreadable. Returns the full updated config dict.
    The on-disk file is rewritten atomically (write to temp then rename).
    """
    # Validate the slug at the registration boundary (E2E M1/F3): the CLI and
    # batch callers reach this path, and the slug becomes a wing_config.json key
    # and (via path-keyed lookups) a filesystem-adjacent identifier. sanitize_name
    # rejects path traversal (``..`` / ``/`` / ``\``), null bytes, and out-of-set
    # characters, so a hostile slug fails loud here instead of silently polluting
    # the registry. (The MCP drawer-write path already sanitizes; this closes the
    # CLI/MCP inconsistency.)
    from ..config import sanitize_name

    slug = sanitize_name(slug, "wing slug")
    if wing_type not in VALID_WING_TYPES:
        raise ValueError(
            f"Invalid wing type {wing_type!r}. Valid types: {', '.join(sorted(VALID_WING_TYPES))}"
        )
    cfg_path = _resolve_write_path()
    if not cfg_path.is_file():
        raise FileNotFoundError(f"wing_config.json not found at {cfg_path}. Run 'sage init' first.")
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    wings = data.setdefault("wings", {})
    if slug in wings:
        raise ValueError(f"Wing {slug!r} is already registered.")
    entry: dict = {"type": wing_type}
    if path:
        entry["path"] = path
    wings[slug] = entry
    # Atomic owner-only write: the temp is created 0600 BEFORE any content is
    # written (no world-readable window) and atomically replaced (no partial file
    # on crash) — see _atomic_write_0600.
    _atomic_write_0600(cfg_path, json.dumps(data, indent=2) + "\n")
    _invalidate_cache()
    return data
