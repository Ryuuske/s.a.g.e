#!/usr/bin/env python3
"""
sage — Give your AI a memory. No API key required.

Three ways to ingest:
  Projects:      sage mine ~/projects/my_app                  (code, docs, notes)
  Conversations: sage mine <convo-dir> --mode convos          (Claude Code / Claude.ai / Codex exports)
  Documents:     sage mine <docs-dir> --mode extract          (PDF, DOCX, PPTX, XLSX, RTF, EPUB — requires sage[extract])

Same nook. Same search. Different ingest strategies.

Commands:
    sage init <dir>                  Detect rooms from folder structure
    sage split <dir>                 Split concatenated mega-files into per-session files
    sage mine <dir>                  Mine project files (default)
    sage mine <dir> --mode convos    Mine conversation exports
    sage mine <dir> --mode extract   Mine binary office documents (PDF/DOCX/etc.)
    sage search "query"              Find anything, exact words
    sage mcp                         Show MCP setup command
    sage wake-up                     Show L0 + L1 wake-up context
    sage wake-up --wing my_app       Wake-up for a specific project
    sage status                      Show what's been filed

Examples:
    sage init ~/projects/my_app
    sage mine ~/projects/my_app
    sage mine ~/.claude/projects/-Users-you-Projects-my_app --mode convos --wing my_app
    sage search "why did we switch to GraphQL"
    sage search "pricing discussion" --wing my_app --room costs
"""

import os
import sys
import shlex
import argparse
from dataclasses import dataclass
from pathlib import Path

from .config import SageConfig
from .corpus_origin import detect_origin_heuristic, detect_origin_llm
from .llm_client import LLMError, get_provider
from .version import __version__


_SAGE_PROJECT_FILES = ("sage.yaml", "entities.json")

# Pass 0 corpus-origin sampling caps. Tier 1 reads FULL file content (no
# front-bias sampling) but bounds total memory on enormous corpora. Tier 2
# trims to a smaller view because LLM context windows are finite.
_PASS_ZERO_MAX_FILES = 30
_PASS_ZERO_PER_FILE_CAP = 100_000  # 100KB per file is generous for prose
_PASS_ZERO_TOTAL_CAP = 5_000_000  # 5MB total ceiling — bounds memory
_PASS_ZERO_LLM_PER_SAMPLE = 2_000  # for Tier 2 LLM call only
_PASS_ZERO_LLM_MAX_SAMPLES = 20  # caps the LLM-tier sample count


def _gather_origin_samples(project_dir) -> list:
    """Collect Tier-1 samples for corpus-origin detection.

    Reads FULL file content (capped at ``_PASS_ZERO_PER_FILE_CAP`` per file
    and ``_PASS_ZERO_TOTAL_CAP`` overall). No front-bias sampling — AI
    signal that lives past the first N chars of a file must still trip
    detection, so we read the whole file up to the cap.

    Skips sage's own per-project artifacts (``entities.json``,
    ``sage.yaml``) so a re-run of ``sage init`` produces the
    same classification result it did on the first run. Without this
    filter, the first run writes entities.json into the corpus, the
    second run picks it up as a sample, and the Tier-1 density math
    drifts (different total_chars). That makes init non-idempotent.

    Returns a list of strings (one per readable file). Empty list when
    the project has no readable text.
    """
    from .entity_detector import scan_for_detection

    files = scan_for_detection(project_dir, max_files=_PASS_ZERO_MAX_FILES)
    samples: list = []
    total_chars = 0
    for filepath in files:
        if filepath.name in _SAGE_PROJECT_FILES:
            continue
        if total_chars >= _PASS_ZERO_TOTAL_CAP:
            break
        try:
            with open(filepath, encoding="utf-8", errors="replace") as f:
                content = f.read(_PASS_ZERO_PER_FILE_CAP)
        except OSError:
            continue
        if not content:
            continue
        samples.append(content)
        total_chars += len(content)
    return samples


def _trim_samples_for_llm(samples: list) -> list:
    """Reduce Tier-1 full-content samples to LLM-friendly size.

    Tier 2 hits an LLM with a finite context window — we trim each sample
    to ``_PASS_ZERO_LLM_PER_SAMPLE`` chars and cap the overall sample
    count at ``_PASS_ZERO_LLM_MAX_SAMPLES``.
    """
    return [s[:_PASS_ZERO_LLM_PER_SAMPLE] for s in samples[:_PASS_ZERO_LLM_MAX_SAMPLES]]


def _run_pass_zero(project_dir, nook_dir, llm_provider) -> dict:
    """Pass 0: detect whether the corpus is AI-dialogue and persist the
    result to ``<nook>/.sage/origin.json``.

    Returns the wrapped result dict (same shape as origin.json) on success,
    or ``None`` when there are no readable samples to detect from. The
    return value is what cmd_init forwards to ``discover_entities`` via
    the ``corpus_origin`` kwarg.

    File-write failures (e.g. read-only nook) are caught and reported on
    stderr; init never blocks on them.
    """
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    samples = _gather_origin_samples(project_dir)
    if not samples:
        print("  Skipping corpus-origin detection — no readable samples.")
        return None

    # Tier 1 — always runs. Cheap regex grep, no API.
    result = detect_origin_heuristic(samples)

    # Tier 2 — runs only when an LLM provider is available. The provider
    # contract is best-effort: corpus_origin internally falls back to a
    # conservative default on transport/parse failure, so we don't need a
    # try/except here, but we still keep one for any unforeseen exception.
    #
    # MERGE-FIELDS, NOT REPLACE: Tier 2's persona/user/platform extraction
    # is the whole reason to run it, but a weak local model (e.g. Ollama
    # gemma4:e4b) can return a wrong likely_ai_dialogue/confidence call
    # that overrides a confident heuristic answer. Per review of
    # PR #1211: keep the heuristic's likely_ai_dialogue + confidence
    # (don't let a weak LLM flip a confident regex answer), and merge in
    # LLM's persona-related fields + combined evidence.
    if llm_provider is not None:
        try:
            llm_result = detect_origin_llm(_trim_samples_for_llm(samples), llm_provider)
            # Heuristic owns: likely_ai_dialogue, confidence (do NOT touch).
            # LLM contributes: primary_platform, user_name, agent_persona_names
            # (heuristic doesn't extract any of these).
            if llm_result.primary_platform:
                result.primary_platform = llm_result.primary_platform
            if llm_result.user_name:
                result.user_name = llm_result.user_name
            if llm_result.agent_persona_names:
                result.agent_persona_names = list(llm_result.agent_persona_names)
            # Combine evidence — keep both signal trails for the audit record,
            # prefixed so the on-disk origin.json says which tier produced
            # each entry. Idempotent: re-prefixing an already-tagged entry
            # is a no-op.
            tier1_prefix = "Tier-1 heuristic: "
            tier2_prefix = "Tier-2 LLM: "
            heuristic_evidence = [
                s if s.startswith(tier1_prefix) else f"{tier1_prefix}{s}"
                for s in (str(e) for e in result.evidence)
            ]
            llm_evidence = [
                s if s.startswith(tier2_prefix) else f"{tier2_prefix}{s}"
                for s in (str(e) for e in llm_result.evidence)
            ]
            result.evidence = heuristic_evidence + llm_evidence
        except Exception as exc:  # noqa: BLE001 — never block init on LLM failure
            print(f"  LLM corpus-origin tier failed ({exc}); using heuristic only.")

    wrapped = {
        "schema_version": 1,
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "result": result.to_dict(),
    }

    origin_path = Path(nook_dir).expanduser() / ".sage" / "origin.json"
    try:
        origin_path.parent.mkdir(parents=True, exist_ok=True)
        with open(origin_path, "w", encoding="utf-8") as f:
            json.dump(wrapped, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        print(f"  Could not write {origin_path}: {exc}", file=sys.stderr)
        # Return the wrapped dict anyway so the in-memory pipeline still
        # benefits from the detection result this run.
        return wrapped

    # Banner — one line, two-space indent matching existing init style.
    res = result
    if res.likely_ai_dialogue:
        platform = res.primary_platform or "AI dialogue (platform unidentified)"
        user = res.user_name or "—"
        agents = ", ".join(res.agent_persona_names) if res.agent_persona_names else "—"
        print(f"  Detected: {platform} (user: {user}, agents: {agents})")
    else:
        print(f"  Corpus origin: not AI-dialogue (confidence: {res.confidence:.2f})")

    return wrapped


def _ensure_nook_files_gitignored(project_dir) -> bool:
    """If project_dir is a git repo, ensure sage's per-project files
    are listed in .gitignore so they don't get committed by accident.

    Returns True if .gitignore was updated, False otherwise. Issue #185:
    `sage init` writes sage.yaml + entities.json into the
    project root, where they previously had no protection against being
    staged into git.
    """
    from pathlib import Path

    project_path = Path(project_dir).expanduser().resolve()
    if not (project_path / ".git").exists():
        return False
    gitignore = project_path / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    existing_lines = {line.strip() for line in existing.splitlines()}
    missing = [p for p in _SAGE_PROJECT_FILES if p not in existing_lines]
    if not missing:
        return False
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    block = prefix + "\n# sage per-project files (issue #185)\n" + "\n".join(missing) + "\n"
    with open(gitignore, "a") as f:
        f.write(block)
    print(f"  Added {', '.join(missing)} to {gitignore.name}")
    return True


def cmd_init(args):
    import json
    from pathlib import Path
    from .entity_detector import confirm_entities
    from .project_scanner import discover_entities
    from .room_detector_local import detect_rooms_local

    # Honor --nook (issue #1313): without this, init silently ignored the
    # flag and always used ~/.sage. Mirror the env-var pattern used by
    # mcp_server.py so every downstream read of ``cfg.nook_path`` (Pass 0,
    # cfg.init(), the post-init mine) routes to the user-specified location.
    if getattr(args, "nook", None):
        os.environ["SAGE_NOOK_PATH"] = os.path.abspath(os.path.expanduser(args.nook))

    cfg = SageConfig()

    # Resolve entity-detection languages: --lang overrides config.
    lang_arg = getattr(args, "lang", None)
    if lang_arg:
        languages = [s.strip() for s in lang_arg.split(",") if s.strip()] or ["en"]
        cfg.set_entity_languages(languages)
    else:
        languages = cfg.entity_languages
    languages_tuple = tuple(languages)

    # --llm is ON by default. --no-llm is the explicit opt-out. Provider
    # precedence is unchanged (Ollama localhost first, then openai-compat,
    # then anthropic). Never block init on a missing LLM: when no provider
    # responds, print a one-line message pointing at --no-llm and fall
    # through to heuristics-only.
    llm_provider = None
    if not getattr(args, "no_llm", False):
        provider_name = getattr(args, "llm_provider", "ollama") or "ollama"
        provider_model = getattr(args, "llm_model", "gemma4:e4b") or "gemma4:e4b"
        try:
            candidate = get_provider(
                name=provider_name,
                model=provider_model,
                endpoint=getattr(args, "llm_endpoint", None),
                api_key=getattr(args, "llm_api_key", None),
            )
            ok, msg = candidate.check_available()
            if ok:
                llm_provider = candidate
                print(f"  LLM enabled: {provider_name}/{provider_model}")
                # Privacy warning (issue #24): if the configured endpoint
                # sends data off the user's machine/network, surface that
                # before init proceeds. URL-based — Ollama on localhost,
                # LM Studio on LAN, etc. won't trigger; Anthropic /
                # cloud OpenAI-compat / any non-local endpoint will.
                if candidate.is_external_service:
                    print(
                        f"  ⚠ {provider_name} is an EXTERNAL API. Your folder "
                        f"content will be sent to the provider during init. "
                        f"sage does not control how the provider logs, "
                        f"retains, or uses your data. Pass --no-llm to keep "
                        f"init fully local."
                    )
                    # Consent gate (issue #26): block init when the api_key
                    # was acquired via env-fallback (stray credential in
                    # shell env). Explicit --llm-api-key (api_key_source ==
                    # "flag") means the user already opted in.
                    # --accept-external-llm bypasses for CI / non-interactive.
                    api_key_source = getattr(candidate, "api_key_source", None)
                    accept_flag = getattr(args, "accept_external_llm", False)
                    if api_key_source == "env" and not accept_flag:
                        try:
                            answer = (
                                input(
                                    "  Your API key was loaded from the environment "
                                    "(not passed via --llm-api-key). Continue with "
                                    "external LLM? [y/N] "
                                )
                                .strip()
                                .lower()
                            )
                        except EOFError:
                            answer = ""
                        if answer != "y":
                            print(
                                "  Declined — falling back to heuristics-only. "
                                "Pass --llm-api-key explicitly or "
                                "--accept-external-llm to skip this prompt."
                            )
                            llm_provider = None
            else:
                print(
                    f"  No LLM provider reachable ({msg}). "
                    f"Running heuristics-only — pass --no-llm to silence this."
                )
        except LLMError as e:
            print(
                f"  LLM init failed ({e}). Running heuristics-only — pass --no-llm to silence this."
            )

    # Pass 0: detect whether the corpus is AI-dialogue. Writes
    # <nook>/.sage/origin.json and supplies corpus context to the
    # entity classifier so it can correctly handle agent persona names
    # (e.g. "Echo", "Sparrow") without misclassifying them as people.
    corpus_origin = _run_pass_zero(
        project_dir=args.dir,
        nook_dir=cfg.nook_path,
        llm_provider=llm_provider,
    )

    # Pass 1: discover entities — manifests + git authors first, prose detection
    # as supplement for names mentioned only in docs/notes. Optional phase-2
    # LLM refinement runs inside discover_entities when llm_provider is given.
    print(f"\n  Scanning for entities in: {args.dir}")
    if languages_tuple != ("en",):
        print(f"  Languages: {', '.join(languages_tuple)}")
    detected = discover_entities(
        args.dir,
        languages=languages_tuple,
        llm_provider=llm_provider,
        corpus_origin=corpus_origin,
    )
    total = (
        len(detected["people"])
        + len(detected["projects"])
        + len(detected.get("topics", []))
        + len(detected["uncertain"])
    )
    if total > 0:
        confirmed = confirm_entities(detected, yes=getattr(args, "yes", False))
        # Save confirmed entities to <project>/entities.json (per-project
        # audit trail — user can inspect or hand-edit) AND merge into the
        # global registry the miner reads at mine time. Topics are kept
        # separately so the miner can later compute cross-wing tunnels
        # from shared topics (see nook_graph.compute_topic_tunnels).
        if confirmed["people"] or confirmed["projects"] or confirmed.get("topics"):
            project_path = Path(args.dir).expanduser().resolve()
            entities_path = project_path / "entities.json"
            with open(entities_path, "w", encoding="utf-8") as f:
                json.dump(confirmed, f, indent=2, ensure_ascii=False)
            print(f"  Entities saved: {entities_path}")

            from .config import normalize_wing_name
            from .miner import add_to_known_entities

            # Match the slug ``room_detector_local`` writes into
            # ``sage.yaml`` so the miner's tunnel lookup hits the
            # same key in ``topics_by_wing`` at mine time (issue #1194 —
            # without this, hyphenated dirnames silently lose tunnels).
            wing = normalize_wing_name(project_path.name)
            registry_path = add_to_known_entities(confirmed, wing=wing)
            print(f"  Registry updated: {registry_path}")
    else:
        print("  No entities detected — proceeding with directory-based rooms.")

    # Pass 2: detect rooms from folder structure
    detect_rooms_local(project_dir=args.dir, yes=getattr(args, "yes", False))
    cfg.init()

    # Pass 3: protect git repos from accidentally committing per-project files
    _ensure_nook_files_gitignored(args.dir)

    # Pass 4: offer to run mine immediately. The directory just had its
    # rooms + entities set up, so 99% of users will mine next anyway —
    # asking here removes the "remember to type the next command" friction.
    # `--auto-mine` skips the prompt and mines automatically; `--yes` is
    # SCOPED to entity auto-accept and does NOT imply mining.
    _maybe_run_mine_after_init(args, cfg)


def _format_size_mb(num_bytes: int) -> str:
    """Render a byte count as a human-readable size for the mine estimate.

    < 1 MB rounds up to ``<1 MB`` so users never see a misleading ``0 MB``
    on small projects. Otherwise reports an integer megabyte count.
    """
    if num_bytes <= 0:
        return "<1 MB"
    mb = num_bytes / (1024 * 1024)
    if mb < 1:
        return "<1 MB"
    return f"{mb:.0f} MB"


def _maybe_run_mine_after_init(args, cfg) -> None:
    """Prompt the user to mine the directory just initialised, or auto-mine
    when ``--auto-mine`` was passed. Extracted so the prompt path is
    unit-testable.

    Behaviour matrix:

    - default (no flags) — prompt, default Yes, mine in-process if accepted
    - ``--yes`` — entity auto-accept only; STILL prompts for the mine step
    - ``--auto-mine`` — skip the mine prompt and mine directly
    - ``--yes --auto-mine`` — fully non-interactive

    Mine errors are surfaced (not swallowed): a failing mine exits with a
    non-zero status via :func:`sys.exit` so downstream scripts can see it.
    The pre-scan that produces the file-count estimate is reused as the
    mine input so we never walk the corpus twice.
    """
    from .miner import mine, scan_project

    project_dir = args.dir
    auto_mine = bool(getattr(args, "auto_mine", False))

    # Single corpus walk: this scan feeds BOTH the "what would be mined"
    # estimate the user sees in the prompt AND the file list mine() will
    # process. We pass the result into mine() via the `files` kwarg so it
    # doesn't re-walk the tree.
    try:
        scanned_files = scan_project(project_dir)
        file_count = len(scanned_files)
        total_bytes = 0
        for fp in scanned_files:
            try:
                total_bytes += fp.stat().st_size
            except OSError:
                # Skip files that vanished between scan and stat — mine()
                # will skip them too.
                continue
        size_str = _format_size_mb(total_bytes)
    except Exception:
        scanned_files = None
        file_count = None
        size_str = None

    # Show the scope estimate BEFORE the prompt so the user knows what
    # they are agreeing to. On a real corpus mine takes minutes; hitting
    # Enter on a default-Y prompt with no size cue is a footgun.
    if isinstance(file_count, int):
        if size_str:
            print(f"  ~{file_count} files (~{size_str}) would be mined into this nook.\n")
        else:
            print(f"  ~{file_count} files would be mined into this nook.\n")

    if not auto_mine:
        try:
            answer = input("  Mine this directory now? [Y/n] ").strip().lower()
        except EOFError:
            # Non-interactive stdin (e.g. piped) — treat like decline so
            # we don't block. User can re-run with --auto-mine to opt in.
            answer = "n"
        if answer not in ("", "y", "yes"):
            print(f"\n  Skipped. Run `sage mine {shlex.quote(project_dir)}` when ready.")
            return

    nook_path = cfg.nook_path
    try:
        mine(
            project_dir=project_dir,
            nook_path=nook_path,
            files=scanned_files,
        )
    except KeyboardInterrupt:
        # mine() handles its own SIGINT summary + sys.exit(130); re-raise
        # any KeyboardInterrupt that escapes (shouldn't happen) so the
        # shell still sees a clean interrupt rather than a swallowed one.
        raise
    except Exception as e:
        print(f"\n  ERROR: mine failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_mine(args):
    nook_path = os.path.expanduser(args.nook) if args.nook else SageConfig().nook_path

    # Refuse a typo'd target directory up front. Without this guard
    # os.walk() silently yields zero entries and the CLI reports "Files: 0"
    # as if the directory was empty — the operator only finds out later.
    # Use isdir, not exists: a regular file passes exists() but os.walk on
    # a file also yields zero entries, so the silent-zero bug reproduces.
    # (Pass 3 Cat 15 F4; Pass 4 Cat 19 F4)
    target_dir = os.path.expanduser(args.dir) if args.dir else None
    if target_dir and not os.path.isdir(target_dir):
        if os.path.exists(target_dir):
            msg = f"source path is not a directory: {args.dir}"
        else:
            msg = f"source directory does not exist: {args.dir}"
        print(f"sage mine: {msg}", file=sys.stderr)
        sys.exit(1)

    include_ignored = []
    for raw in args.include_ignored or []:
        include_ignored.extend(part.strip() for part in raw.split(",") if part.strip())

    # sage --agents handling. Accept either repeated `--agents X`
    # flags or a single comma-separated value (`--agents X,Y,Z`). Empty
    # list means "no agents tag" — drawers are filed with agents=[].
    agents_arg = getattr(args, "agents", None) or []
    agents_list: list = []
    for raw in agents_arg:
        agents_list.extend(part.strip() for part in raw.split(",") if part.strip())
    agents_list = list(dict.fromkeys(agents_list))  # preserve order, dedupe

    # --redetect-origin re-runs corpus_origin on the current corpus state
    # and overwrites <nook>/.sage/origin.json before mining proceeds.
    # Heuristic-only by design — full LLM detection lives on `sage init`.
    if getattr(args, "redetect_origin", False):
        _run_pass_zero(
            project_dir=args.dir,
            nook_dir=nook_path,
            llm_provider=None,
        )

    from .nook import MineAlreadyRunning, MineValidationError

    try:
        if args.mode == "convos":
            from .convo_miner import mine_convos

            mine_convos(
                convo_dir=args.dir,
                nook_path=nook_path,
                wing=args.wing,
                agent=args.agent,
                limit=args.limit,
                dry_run=args.dry_run,
                extract_mode=args.extract,
                agents=agents_list,
            )
        elif args.mode == "extract":
            from .format_miner import mine_formats

            mine_formats(
                format_dir=args.dir,
                nook_path=nook_path,
                wing=args.wing,
                agent=args.agent,
                limit=args.limit,
                dry_run=args.dry_run,
                agents=agents_list,
            )
        else:
            from .miner import mine

            mine(
                project_dir=args.dir,
                nook_path=nook_path,
                wing_override=args.wing,
                agent=args.agent,
                limit=args.limit,
                dry_run=args.dry_run,
                respect_gitignore=not args.no_gitignore,
                include_ignored=include_ignored,
                max_chunks_per_file=getattr(args, "max_chunks_per_file", None),
                agents=agents_list,
            )
    except MineAlreadyRunning as exc:
        # A live MCP server or another mine is already writing to this
        # nook. Surface the holder identity so the operator knows what
        # to wait for (or stop), and exit non-zero so wrappers like
        # nohup / scripts can detect the contention.
        print(f"sage: {exc}", file=sys.stderr)
        sys.exit(1)
    except MineValidationError as exc:
        # PRAGMA quick_check on chroma.sqlite3 returned errors at end of mine.
        # The corruption may pre-date the mine; we surface it here so automation
        # cannot proceed against a half-broken nook. Reuse cmd_repair's
        # recovery banner so the operator sees one consistent message regardless
        # of which command surfaces it.
        from .repair import print_sqlite_integrity_abort

        print_sqlite_integrity_abort(exc.nook_path, exc.errors)
        print(
            "\n  PRAGMA quick_check after this mine reported errors (the corruption\n"
            "  may pre-date the mine itself). Drawers may still be intact for direct\n"
            "  lookup; wing-filtered or full-text search will fail until the FTS5\n"
            "  index is rebuilt. `sage repair --yes` rebuilds the FTS5 virtual\n"
            "  table automatically (step 6 of the recovery above).",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:
        # Unregistered-wing rejection is a ValueError subclass raised by
        # extensions.wing_registry.require_registered_wing. Surface the
        # actionable message verbatim instead of letting the traceback
        # escape — the exception itself already includes the
        # `sage wing add ...` hint. (Pass 3 Cat 15 F4)
        from .extensions.wing_registry import WingNotRegisteredError

        if isinstance(exc, WingNotRegisteredError):
            print(f"sage mine: {exc}", file=sys.stderr)
            sys.exit(1)
        raise


def cmd_sweep(args):
    """Sweep a transcript file or directory.

    The sweeper deduplicates against its own prior writes via
    deterministic drawer IDs + a timestamp cursor. It does NOT currently
    coordinate with the file-level miners (miner.py / convo_miner.py) —
    those produce char-chunked drawers without compatible message
    metadata, so running both miners may store overlapping content under
    different IDs.
    """
    from .sweeper import sweep, sweep_directory

    nook_path = os.path.expanduser(args.nook) if args.nook else SageConfig().nook_path
    target = os.path.expanduser(args.target)

    if os.path.isfile(target):
        result = sweep(target, nook_path)
        print(
            f"  Swept {target}: +{result['drawers_added']} new, "
            f"{result['drawers_already_present']} already present, "
            f"{result['drawers_skipped']} skipped (< cursor)."
        )
    elif os.path.isdir(target):
        result = sweep_directory(target, nook_path)
        print(
            f"  Swept {result['files_succeeded']}/{result['files_attempted']} "
            f"files from {target}: +{result['drawers_added']} new, "
            f"{result['drawers_already_present']} already present, "
            f"{result['drawers_skipped']} skipped (< cursor)."
        )
        failures = result.get("failures") or []
        if failures:
            print(
                f"  WARNING: {len(failures)} file(s) failed to sweep - see stderr / logs for details.",
                file=sys.stderr,
            )
            sys.exit(2)
    else:
        print(f"  ERROR: Not a file or directory: {target}", file=sys.stderr)
        sys.exit(1)


def cmd_consolidate(args):
    """`sage consolidate` — decay + consolidation passes (WI-6).

    Subcommands:
      report   — dry-run: show what would change, nothing written (default)
      run      — apply strength updates to the nook

    SAFE BY DEFAULT: ``report`` only reads the nook; ``run`` writes
    updated ``strength`` metadata to drawers but NEVER deletes any drawer.
    The no-delete floor is a structural invariant — recovery is always
    possible by re-ranking from provenance (confidence / filed_at).

    Examples:
        sage consolidate report
        sage consolidate run
        sage consolidate run --wing my_project
        sage consolidate run --skip-consolidation  # decay only
    """
    from .consolidation import (
        decay_pass,
        consolidation_pass,
        summarise_decay_results,
        summarise_consolidation_results,
    )

    nook_path = os.path.expanduser(args.nook) if args.nook else SageConfig().nook_path
    sub = getattr(args, "consolidate_command", None) or "report"
    apply = sub == "run"
    wing = getattr(args, "wing", None)
    skip_consolidation = getattr(args, "skip_consolidation", False)
    skip_decay = getattr(args, "skip_decay", False)

    from .nook import _open_collection_or_explain

    col = _open_collection_or_explain(nook_path)
    if col is None:
        sys.exit(1)

    mode_label = "APPLY" if apply else "DRY-RUN"
    print(f"\n  sage consolidate [{mode_label}]")
    if wing:
        print(f"  Scoped to wing: {wing}")
    if not apply:
        print("  (No changes will be written. Use `consolidate run` to apply.)")
    print()

    # ── Decay pass ────────────────────────────────────────────────────────
    if not skip_decay:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        decay_results = decay_pass(col, now=now, wing=wing, dry_run=not apply)
        dsummary = summarise_decay_results(decay_results)
        print("  DECAY PASS")
        print(f"    Drawers examined : {dsummary['total']}")
        print(f"    Skipped (core)   : {dsummary['skipped_core']}")
        print(f"    Skipped (no ts)  : {dsummary['skipped_no_timestamp']}")
        print(f"    Decayed          : {dsummary['decayed']}")
        print(f"    No change        : {dsummary['no_change']}")
        if apply:
            print(f"    Written          : {dsummary['written']}")
        if dsummary["avg_old_strength"] is not None:
            print(f"    Avg old strength : {dsummary['avg_old_strength']}")
            print(f"    Avg new strength : {dsummary['avg_new_strength']}")
        print()
        if getattr(args, "verbose", False):
            for r in decay_results:
                if r.reason == "decayed":
                    print(
                        f"    [{r.wing}/{r.room}] {r.drawer_id[:32]}  "
                        f"{r.old_strength:.4f} → {r.new_strength:.4f}  "
                        f"(age={r.days_since_used:.1f}d, conf={r.confidence:.2f})"
                    )

    # ── Consolidation pass ────────────────────────────────────────────────
    if not skip_consolidation:
        c_results = consolidation_pass(col, wing=wing, dry_run=not apply)
        csummary = summarise_consolidation_results(c_results)
        print("  CONSOLIDATION PASS")
        print(f"    Drawers examined : {csummary['total']}")
        print(f"    Skipped (core)   : {csummary['skipped_core']}")
        print(f"    Canonical        : {csummary['canonical']}")
        print(f"    Demoted          : {csummary['demoted']}")
        if apply:
            print(f"    Written          : {csummary['written']}")
        print()
        if getattr(args, "verbose", False):
            for r in c_results:
                if r.reason == "demoted:near_duplicate":
                    print(
                        f"    [{r.wing}/{r.room}] {r.drawer_id[:32]}  "
                        f"demoted {r.old_strength:.4f} → {r.new_strength:.4f}  "
                        f"canonical={r.canonical_id[:32] if r.canonical_id else '?'}"
                    )

    if not apply:
        print("  Re-run with `consolidate run` to apply strength changes.")
    else:
        print("  Done. Drawers re-ranked; none deleted.")
    print()


def cmd_sync(args):
    """Prune drawers whose source files are gitignored, deleted, or moved (#1252)."""
    from .mcp_server import _wal_log
    from .nook import MineAlreadyRunning
    from .sync import sync_nook

    nook_path = os.path.expanduser(args.nook) if args.nook else SageConfig().nook_path

    if not os.path.isdir(nook_path):
        print(f"\n  No nook found at {nook_path}")
        return
    if not os.path.isfile(os.path.join(nook_path, "chroma.sqlite3")):
        print(f"\n  Nook dir at {nook_path} exists but has no chroma.sqlite3 yet.")
        print("  Run: sage mine <dir>")
        return

    project_dirs = []
    if args.dir:
        project_dirs.append(os.path.expanduser(args.dir))
    project_dirs.extend(os.path.expanduser(r) for r in args.root)
    project_dirs = project_dirs or None

    print(f"\n{'=' * 55}")
    print("  sage Sync — Gitignore-aware drawer prune")
    print(f"{'=' * 55}")
    print(f"  Nook:   {nook_path}")
    if args.wing:
        print(f"  Wing:     {args.wing}")
    if project_dirs:
        for p in project_dirs:
            print(f"  Project:  {p}")
    if args.dry_run:
        print("  Mode:     DRY RUN (no deletions)")
    else:
        print("  Mode:     APPLY (deleting drawers)")
    print(f"{'-' * 55}\n")

    try:
        report = sync_nook(
            nook_path=nook_path,
            project_dirs=project_dirs,
            wing=args.wing,
            dry_run=args.dry_run,
            wal_log=_wal_log,
        )
    except MineAlreadyRunning as exc:
        print(f"sage: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"sage: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(f"sage: sync failed: {exc}", file=sys.stderr)
        sys.exit(1)

    removed_suffix = "(would remove)" if args.dry_run else "(removed)"
    print(f"  Scanned:        {report['scanned']}")
    print(f"  Kept:           {report['kept']}")
    print(f"  Gitignored:     {report['gitignored']}  {removed_suffix}")
    print(f"  Missing:        {report['missing']}  {removed_suffix}")
    print(f"  No source:      {report['no_source']}  (kept)")
    print(f"  Out of scope:   {report['out_of_scope']}  (kept)")

    by_source = report.get("by_source") or {}
    if by_source:
        top = sorted(by_source.items(), key=lambda kv: -kv[1])[:5]
        label = "Top sources to remove" if args.dry_run else "Top sources removed"
        print(f"\n  {label}:")
        for src, n in top:
            print(f"    {src}  ({n})")

    if args.dry_run:
        if report["gitignored"] + report["missing"] > 0:
            print("\n  Re-run with --apply to commit these deletions.")
    else:
        print(
            f"\n  Removed {report['removed_drawers']} drawers, {report['removed_closets']} closets."
        )

    print(f"\n{'=' * 55}\n")


def _exit_code_for(result):
    """Map a one-channel ops error result to a CLI exit code (ADR-0073 fork 4).

    Validation errors exit 2 (matching ``cmd_sync``'s bad-input convention);
    backend/index errors exit 1.
    """
    return 2 if result.get("error_kind") == "validation" else 1


def cmd_search(args):
    from . import ops
    from .cli_format import render_full

    cfg = SageConfig()
    nook_path = os.path.expanduser(args.nook) if args.nook else cfg.nook_path
    agents = [args.agent] if getattr(args, "agent", None) else None
    result = ops.search(
        query=args.query,
        nook_path=nook_path,
        collection_name=cfg.collection_name,
        wing=args.wing,
        room=args.room,
        n_results=args.results,
        agents=agents,
    )
    if "error" in result:
        hint = result.get("hint")
        print(f"search: {result['error']}" + (f"\n  {hint}" if hint else ""), file=sys.stderr)
        sys.exit(_exit_code_for(result))
    render_full(result, query=args.query, wing=args.wing, room=args.room)


def cmd_recall(args):
    """Agent-keyed search: find drawers tagged with one or more agent names.

    Output is intentionally terse — drawer ID, first 200 chars of content,
    and the agents list per result — so it can be eyeballed quickly during
    development and parsed by scripts later.
    """
    from . import ops
    from .cli_format import render_terse

    cfg = SageConfig()
    nook_path = os.path.expanduser(args.nook) if args.nook else cfg.nook_path
    agents = [args.agent] if args.agent else None
    result = ops.search(
        query=args.query,
        nook_path=nook_path,
        collection_name=cfg.collection_name,
        wing=args.wing,
        n_results=args.results,
        agents=agents,
    )
    if "error" in result:
        print(f"recall: {result['error']}", file=sys.stderr)
        sys.exit(_exit_code_for(result))
    render_terse(result, query=args.query, agent=args.agent, wing=args.wing)


def cmd_wing(args):
    """`sage wing` — list registered wings or add a new one.

    Subcommands:
      list                 — print all registered wings with type + path
      add <slug> --type T  — register a new wing (type: dev|project|knowledge|ops|meta)
    """
    from .extensions import wing_registry

    sub = getattr(args, "wing_command", None)
    if sub == "list":
        try:
            cfg = wing_registry.load_config()
        except FileNotFoundError as exc:
            print(f"wing: {exc}", file=sys.stderr)
            sys.exit(1)
        wings = cfg.get("wings") or {}
        if not wings:
            print("  No wings registered.")
            return
        # Group by type, render as a table.
        by_type: dict = {}
        for slug, entry in wings.items():
            by_type.setdefault(entry.get("type", "unknown"), []).append((slug, entry))
        print(f"\n  Registered wings ({len(wings)} total)")
        for wtype in sorted(by_type):
            print(f"\n  [{wtype}]")
            for slug, entry in sorted(by_type[wtype]):
                path = entry.get("path", "")
                extras = []
                if entry.get("obsidian_aware"):
                    extras.append("obsidian-aware")
                extras_str = f"  ({', '.join(extras)})" if extras else ""
                if path:
                    print(f"    {slug:24} {path}{extras_str}")
                else:
                    print(f"    {slug:24} (no path){extras_str}")
        print()
        return

    if sub == "add":
        try:
            wing_registry.add_wing(args.slug, args.type, path=args.path)
        except (FileNotFoundError, ValueError) as exc:
            print(f"wing add: {exc}", file=sys.stderr)
            sys.exit(1)
        location = args.path or "(no path)"
        print(f"\n  Registered wing {args.slug!r} (type={args.type}, path={location}).")
        return

    print("wing: subcommand required (list | add).", file=sys.stderr)
    sys.exit(2)


def cmd_registry(args):
    """`sage registry` — build or search the skill/agent/script registry.

    Subcommands:
      build            — scan agents/, skills/, scripts/ and print the entry count
      search <query>   — keyword search; prints matching entries as a table
    """
    from . import ops
    from .extensions.skill_registry import build_registry

    sub = getattr(args, "registry_command", None)

    # Resolve repo root: --registry-root flag → auto-detect from package location
    # (the single resolver shared with the MCP registry surface, ops.resolve_repo_root).
    repo_root = getattr(args, "registry_root", None) or ops.resolve_repo_root()
    if not repo_root:
        print("registry: could not detect repo root. Pass --root /path/to/repo.", file=sys.stderr)
        sys.exit(1)

    force_rebuild = getattr(args, "force_rebuild", False)
    # force_rebuild=True is passed directly to build_registry which handles
    # cache bypass internally; a separate _invalidate_cache call on a fresh
    # CLI process would be a no-op and is omitted to keep invalidation coherent.

    if sub == "build":
        try:
            # `registry build` always forces a rebuild so the output reflects
            # the current on-disk state, not a stale in-process snapshot.
            entries = build_registry(repo_root, force_rebuild=True)
        except Exception as exc:
            print(f"registry build: {exc}", file=sys.stderr)
            sys.exit(1)
        by_kind: dict = {}
        for e in entries:
            by_kind.setdefault(e["kind"], 0)
            by_kind[e["kind"]] += 1
        print(f"\n  Registry built from: {repo_root}")
        print(f"  Total entries: {len(entries)}")
        for kind in sorted(by_kind):
            print(f"    {kind}: {by_kind[kind]}")
        print()
        return

    if sub == "search":
        query = getattr(args, "query", "") or ""
        kind_filter = getattr(args, "kind", None)
        limit = max(1, min(getattr(args, "limit", 10) or 10, 50))
        result = ops.registry_search(
            query=query,
            kind=kind_filter,
            limit=limit,
            repo_root=repo_root,
            force_rebuild=force_rebuild,
        )
        if "error" in result:
            print(f"registry search: {result['error']}", file=sys.stderr)
            sys.exit(_exit_code_for(result))
        results = result["results"]
        if not results:
            print(f"\n  No results for query {query!r} (kind={kind_filter or 'any'}).\n")
            return
        print(
            f"\n  Registry search: {query!r}  (kind={kind_filter or 'any'}, {len(results)} result(s))"
        )
        print(f"  {'KIND':<8}  {'NAME':<32}  {'ONE_LINE'}")
        print(f"  {'-' * 8}  {'-' * 32}  {'-' * 50}")
        for e in results:
            print(f"  {e['kind']:<8}  {e['name']:<32}  {e['one_line'][:60]}")
        print()
        return

    print("registry: subcommand required (build | search).", file=sys.stderr)
    sys.exit(2)


def cmd_audit(args):
    """`sage audit` — operational audits over the nook.

    Subcommands:
      dispatch-reliability  — count hook-emergency drawers vs Keeper-
                              curated drawers; high hook count = the
                              orchestrator forgot to dispatch the Keeper
                              from its CLAUDE.md spine.
    """
    from . import mcp_server
    from datetime import datetime, timedelta, timezone

    # Set the env override BEFORE constructing SageConfig, otherwise
    # the config object reads the old default and --nook is silently
    # ignored. (Pass 3 Cat 15 F5)
    nook_path = os.path.expanduser(args.nook) if args.nook else SageConfig().nook_path
    if args.nook:
        os.environ["SAGE_NOOK_PATH"] = nook_path
    mcp_server._config = SageConfig()

    sub = getattr(args, "audit_command", None)
    if sub != "dispatch-reliability":
        print("audit: subcommand required (dispatch-reliability).", file=sys.stderr)
        sys.exit(2)

    days = max(1, int(getattr(args, "days", 7)))
    wing = getattr(args, "wing", None)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Walk the drawer collection via tool_list_drawers (paginated).
    hook_count = 0
    keeper_count = 0
    hook_drawers = []
    offset = 0
    page = 100
    while True:
        result = mcp_server.tool_list_drawers(wing=wing, limit=page, offset=offset)
        if "error" in result:
            print(f"audit: {result['error']}", file=sys.stderr)
            sys.exit(1)
        drawers = result.get("drawers") or []
        if not drawers:
            break
        for d in drawers:
            # tool_list_drawers surfaces agents already; just check the tag.
            agents = d.get("agents") or []
            # Filter on filed_at window — fetch full drawer for timestamp.
            row = mcp_server.tool_get_drawer(d.get("drawer_id", ""))
            filed_at_str = (row.get("metadata") or {}).get("filed_at", "")
            try:
                filed_at = datetime.fromisoformat(filed_at_str)
                if filed_at.tzinfo is None:
                    filed_at = filed_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if filed_at < cutoff:
                continue
            if "session-end-hook" in agents or "pre-compact-hook" in agents:
                hook_count += 1
                hook_drawers.append(
                    {
                        "drawer_id": d.get("drawer_id", "?"),
                        "wing": d.get("wing", "?"),
                        "room": d.get("room", "?"),
                        "filed_at": filed_at_str,
                        "tag": "session-end-hook"
                        if "session-end-hook" in agents
                        else "pre-compact-hook",
                    }
                )
            elif "aidev-keeper" in agents:
                keeper_count += 1
        if len(drawers) < page:
            break
        offset += len(drawers)

    total = hook_count + keeper_count
    print(
        f"\n  Dispatch reliability — last {days} day(s)" + (f", wing={wing}" if wing else "") + ":"
    )
    print(f"    Keeper-curated drawers:   {keeper_count}")
    print(f"    Hook-emergency drawers:      {hook_count}")
    if total > 0:
        ratio = hook_count / total * 100
        print(f"    Hook share:                  {ratio:.1f}%")
        if ratio > 25:
            print("\n  ⚠  Hook share above 25% — the orchestrator is likely")
            print("     forgetting to dispatch the Keeper. Review CLAUDE.md.")
        elif total >= 5 and ratio == 0:
            print("\n  ✓ Every session dispatched the Keeper.")
    else:
        print("\n  No relevant drawers in this window (nook may be fresh).")

    if hook_drawers and getattr(args, "verbose", False):
        print("\n  Recent hook-emergency drawers:")
        for h in hook_drawers[-10:]:
            print(f"    [{h['filed_at']}] {h['wing']}/{h['room']} ({h['tag']}) {h['drawer_id']}")


def cmd_tunnel(args):
    """`sage tunnel` — curate cross-wing tunnels.

    Tunnels are explicit, named links between a ``(source_wing, room)``
    and a ``(target_wing, room)``. They survive across mines and surface
    in cross-wing wake-up queries via the Keeper's ``follow`` step.

    Subcommands:
      list                                — print all tunnels, optionally filtered
      create <source_wing> <source_room> <target_wing> <target_room>
                                          — create a new tunnel
      delete <tunnel_id>                  — remove a tunnel by ID
      follow <wing> <room>                — show connected rooms in other wings
      find [--wing-a X] [--wing-b Y]      — find candidate tunnel-worthy bridges
    """
    from . import mcp_server

    # Same env-var-before-config ordering fix as cmd_audit. (Pass 3 Cat 15 F5)
    nook_path = os.path.expanduser(args.nook) if args.nook else SageConfig().nook_path
    if args.nook:
        os.environ["SAGE_NOOK_PATH"] = nook_path
    # Point the MCP server's config at the user's nook path for this
    # one-shot CLI invocation.
    mcp_server._config = SageConfig()

    sub = getattr(args, "tunnel_command", None)
    if sub == "list":
        result = mcp_server.tool_list_tunnels(wing=getattr(args, "wing", None))
        if isinstance(result, dict) and "error" in result:
            print(f"tunnel list: {result['error']}", file=sys.stderr)
            sys.exit(1)
        tunnels = result if isinstance(result, list) else (result.get("tunnels") or [])
        if not tunnels:
            print("\n  No tunnels registered." + (f" (wing={args.wing})" if args.wing else ""))
            return
        print(f"\n  {len(tunnels)} tunnel(s)" + (f" — wing={args.wing}" if args.wing else "") + ":")
        for t in tunnels:
            tid = t.get("id") or t.get("tunnel_id") or "(no-id)"
            src_obj = t.get("source") or {}
            dst_obj = t.get("target") or {}
            src = f"{src_obj.get('wing', '?')}/{src_obj.get('room', '?')}"
            dst = f"{dst_obj.get('wing', '?')}/{dst_obj.get('room', '?')}"
            label = t.get("label", "")
            print(f"    {tid}: {src} → {dst}" + (f"  ({label})" if label else ""))
        return

    if sub == "create":
        # Wing registration gate runs inside tool_create_tunnel via the
        # graph layer; surface a friendly error here too.
        result = mcp_server.tool_create_tunnel(
            source_wing=args.source_wing,
            source_room=args.source_room,
            target_wing=args.target_wing,
            target_room=args.target_room,
            label=getattr(args, "label", None) or "",
        )
        # tool_create_tunnel returns either {"error": ...} on failure or the
        # stored tunnel dict (with key "id") on success. Don't gate on a
        # non-existent "success" key. (Pass 3 Cat 15 F1)
        if isinstance(result, dict) and "error" in result:
            print(f"tunnel create: {result['error']}", file=sys.stderr)
            sys.exit(1)
        tunnel_id = (
            (result.get("id") or result.get("tunnel_id")) if isinstance(result, dict) else None
        ) or "(no id)"
        print(
            f"\n  Created tunnel {tunnel_id}: "
            f"{args.source_wing}/{args.source_room} → {args.target_wing}/{args.target_room}"
        )
        return

    if sub == "delete":
        result = mcp_server.tool_delete_tunnel(args.tunnel_id)
        if isinstance(result, dict) and "error" in result:
            print(f"tunnel delete: {result['error']}", file=sys.stderr)
            sys.exit(1)
        print(f"\n  Deleted tunnel {args.tunnel_id}.")
        return

    if sub == "follow":
        result = mcp_server.tool_follow_tunnels(wing=args.wing, room=args.room)
        # follow_tunnels returns a list of connection dicts on success, or a
        # {"error": ...} dict on validation / no-nook. (Pass 3 Cat 15 F2)
        if isinstance(result, dict) and "error" in result:
            print(f"tunnel follow: {result['error']}", file=sys.stderr)
            sys.exit(1)
        followed = result if isinstance(result, list) else []
        if not followed:
            print(f"\n  No connected rooms from {args.wing}/{args.room}.")
            return
        print(f"\n  Connected rooms from {args.wing}/{args.room}:")
        for entry in followed:
            print(f"    {entry}")
        return

    if sub == "find":
        result = mcp_server.tool_find_tunnels(
            wing_a=getattr(args, "wing_a", None), wing_b=getattr(args, "wing_b", None)
        )
        # find_tunnels returns a list of bridge dicts on success, or a
        # {"error": ...} dict on validation / no-nook. (Pass 3 Cat 15 F3)
        if isinstance(result, dict) and "error" in result:
            print(f"tunnel find: {result['error']}", file=sys.stderr)
            sys.exit(1)
        candidates = result if isinstance(result, list) else []
        if not candidates:
            print("\n  No candidate tunnel-worthy bridges found.")
            return
        print(f"\n  {len(candidates)} candidate bridge(s):")
        for entry in candidates:
            print(f"    {entry}")
        return

    print("tunnel: subcommand required (list | create | delete | follow | find).", file=sys.stderr)
    sys.exit(2)


def cmd_verdict(args):
    """`sage verdict` — parse and log an auditor verdict reply.

    The orchestrator runs ``sage verdict log < reply.txt`` (or
    ``--file PATH``) after each auditor dispatch. Parses the structured
    ``@@VERDICT BEGIN`` block per ``docs/specs/verdict-schema.md``, logs
    one row to ``~/.sage/telemetry/turns.jsonl``, prints a short
    summary, and exits nonzero on parser error or HOLD/ABORT verdict.

    Subcommands:
      log    — parse + log + summarize one auditor reply
    """
    from . import telemetry, verdict_parser

    sub = getattr(args, "verdict_command", None)
    if sub != "log":
        print("verdict: subcommand required (log).", file=sys.stderr)
        sys.exit(2)

    src = getattr(args, "file", None)
    if src:
        try:
            with open(os.path.expanduser(src), encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            print(f"verdict: cannot read {src}: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        text = sys.stdin.read()

    v = verdict_parser.parse_verdict(text)

    phase = getattr(args, "phase", "audit")
    mode = getattr(args, "mode", "aidev")
    wing = getattr(args, "wing", None)
    turn_id = getattr(args, "turn_id", None)

    # Only log a telemetry row for a successfully-parsed verdict. A parse
    # failure (no @@VERDICT block, unknown fields, consistency error) has no
    # meaningful governance signal to record — logging a degraded row
    # (agent='?', verdict=None) would contaminate the audit-quality stream.
    # HOLD/ABORT are VALID verdicts and ARE still logged (their nonzero exit is
    # a signal, not a parse failure).
    tid = (
        telemetry.log_from_verdict(v, phase=phase, mode=mode, wing=wing, turn_id=turn_id)
        if v.valid
        else None
    )

    print("\n  Verdict parsed:")
    print(f"    valid:       {v.valid}")
    print(f"    verdict:     {v.verdict or '(none)'}")
    print(f"    lane:        {v.lane or '(none)'}")
    print(f"    findings:    {len(v.findings)} ({len(v.blocking_findings)} blocking ≥80)")
    if v.parser_errors:
        print("    parser errors:")
        for e in v.parser_errors:
            print(f"      - {e}")
    if tid:
        print(f"    telemetry:   turn_id={tid}")
    elif not v.valid:
        print("    telemetry:   (skipped — invalid verdict not logged)")
    else:
        print("    telemetry:   (log write failed)")

    # Exit nonzero so the orchestrator's downstream shell logic can
    # branch on parser error or HOLD/ABORT without inspecting stdout.
    if not v.valid or v.verdict in {"HOLD", "ABORT"}:
        sys.exit(1)


def cmd_wakeup(args):
    """Show Tier-0 block: L0 (identity) + L1 (essential story) + compact registry."""
    from .layers import MemoryStack, TIER0_TOKEN_BUDGET

    nook_path = os.path.expanduser(args.nook) if args.nook else SageConfig().nook_path
    stack = MemoryStack(nook_path=nook_path)

    # Tier-0 path: assemble identity + L1 + compact registry section.
    # repo_root auto-detection handled inside assemble_tier0.
    block = stack.assemble_tier0(wing=args.wing)

    budget_note = "" if block.within_budget else f"  *** OVER BUDGET ({TIER0_TOKEN_BUDGET} tok) ***"
    print(
        f"Tier-0 block (~{block.token_count} tokens"
        f", budget={block.budget}"
        f", registry={block.registry_count} entries"
        f"){budget_note}:"
    )
    print("=" * 50)
    print(block.text)


def cmd_split(args):
    """Split concatenated transcript mega-files into per-session files."""
    from .split_mega_files import main as split_main
    import sys

    # Rebuild argv for split_mega_files argparse
    # Expand ~ and resolve to absolute path so split_mega_files sees a real path
    argv = ["--source", str(Path(args.dir).expanduser().resolve())]
    if args.output_dir:
        argv += ["--output-dir", args.output_dir]
    if args.dry_run:
        argv.append("--dry-run")
    if args.min_sessions != 2:
        argv += ["--min-sessions", str(args.min_sessions)]

    old_argv = sys.argv
    sys.argv = ["sage split"] + argv
    try:
        split_main()
    finally:
        sys.argv = old_argv


def cmd_migrate(args):
    """Migrate nook from a different ChromaDB version."""
    from .migrate import migrate

    nook_path = os.path.expanduser(args.nook) if args.nook else SageConfig().nook_path
    # migrate() returns False on missing nook, lock conflict, insufficient
    # disk space, or user-declined confirmation. Propagate that as exit 1 so
    # CI/cron scripts can distinguish "did nothing" from "succeeded".
    # (Pass 3 Cat 15 F1)
    ok = migrate(
        nook_path=nook_path,
        dry_run=args.dry_run,
        confirm=getattr(args, "yes", False),
    )
    if not ok:
        sys.exit(1)


def cmd_status(args):
    from .miner import status

    nook_path = os.path.expanduser(args.nook) if args.nook else SageConfig().nook_path
    status(nook_path=nook_path)


def cmd_dashboard(args):
    """Render the governance (verdict-log) + Nook store-health dashboard."""
    from .dashboard import dashboard

    nook_path = os.path.expanduser(args.nook) if args.nook else None
    dashboard(nook_path=nook_path)


def cmd_repair_status(args):
    """Read-only HNSW capacity health check (#1222)."""
    from .repair import status as repair_status

    nook_path = os.path.expanduser(args.nook) if args.nook else SageConfig().nook_path
    repair_status(nook_path=nook_path)


def cmd_repair(args):
    """Rebuild nook vector index from SQLite metadata."""
    import shutil
    from .backends.chroma import ChromaBackend
    from .migrate import confirm_destructive_action, contains_nook_database
    from .repair import (
        RebuildCollectionError,
        TruncationDetected,
        _close_chroma_handles,
        _extract_drawers,
        _rebuild_collection_via_temp,
        check_extraction_safety,
        maybe_repair_poisoned_max_seq_id_before_rebuild,
        print_sqlite_integrity_abort,
        sqlite_integrity_errors,
    )

    config = SageConfig()
    collection_name = config.collection_name
    nook_path = os.path.abspath(os.path.expanduser(args.nook) if args.nook else config.nook_path)

    if getattr(args, "mode", "full") == "max-seq-id":
        from .repair import repair_max_seq_id

        repair_max_seq_id(
            nook_path,
            segment=getattr(args, "segment", None),
            from_sidecar=getattr(args, "from_sidecar", None),
            backup=getattr(args, "backup", True),
            dry_run=getattr(args, "dry_run", False),
            assume_yes=getattr(args, "yes", False),
        )
        return

    if getattr(args, "mode", "full") == "from-sqlite":
        from .migrate import confirm_destructive_action
        from .repair import RebuildPartialError, rebuild_from_sqlite

        source_path = getattr(args, "source", None)
        source_path = os.path.abspath(os.path.expanduser(source_path)) if source_path else nook_path
        archive_existing = getattr(args, "archive_existing", False)

        # Gate any path that touches the user's existing nook dir
        # behind confirm_destructive_action. The default "full" mode
        # already gates; from-sqlite needs the same protection because:
        # (a) --archive-existing renames the existing nook,
        # (b) --source PATH writes into --nook dir which the user
        #     may not realize is also a nook.
        # No prompt when source != dest AND dest does not exist (pure
        # extract-into-fresh-dir case is non-destructive to existing
        # nooks).
        is_destructive_to_dest = source_path == nook_path or os.path.exists(nook_path)
        if is_destructive_to_dest and not confirm_destructive_action(
            "Rebuild from SQLite", nook_path, assume_yes=getattr(args, "yes", False)
        ):
            return

        try:
            counts = rebuild_from_sqlite(
                source_nook=source_path,
                dest_nook=nook_path,
                archive_existing_dest=archive_existing,
            )
        except RebuildPartialError as exc:
            # The error itself was already printed by rebuild_from_sqlite
            # with recovery instructions; surface a non-zero exit so
            # scripts and CI gates see the failure.
            print(
                "\n  Rebuild partial — see message above. "
                f"Failed in collection: {exc.failed_collection}"
            )
            sys.exit(1)
        # An empty counts dict is rebuild_from_sqlite's documented signal
        # for a validation refusal (missing source, existing dest,
        # in-place without --archive-existing). The library already
        # printed an actionable message; exit non-zero so unattended
        # scripts/CI distinguish "invalid inputs" from a successful
        # rebuild that legitimately found zero rows (which still returns
        # a populated dict with 0-valued counts).
        if not counts:
            sys.exit(1)
        return

    db_path = os.path.join(nook_path, "chroma.sqlite3")

    if not os.path.isdir(nook_path):
        print(f"\n  No nook found at {nook_path}")
        return
    if not contains_nook_database(nook_path):
        print(f"\n No nook database found at {db_path}")
        return

    # Run the SQLite integrity preflight before any chromadb client open.
    # ChromaDB's rust binding raises pyo3_runtime.PanicException on a
    # malformed page, which is not a regular Exception subclass and
    # propagates past the try/except below — the user gets a 30-line
    # stack trace instead of the friendly abort message. Run quick_check
    # here so we can surface the clear recovery instructions and exit
    # cleanly before chromadb's compactor touches the disk.
    sqlite_errors = sqlite_integrity_errors(nook_path)
    if sqlite_errors:
        print_sqlite_integrity_abort(nook_path, sqlite_errors)
        sys.exit(1)

    preflight = maybe_repair_poisoned_max_seq_id_before_rebuild(
        nook_path,
        backup=getattr(args, "backup", True),
        dry_run=getattr(args, "dry_run", False),
        assume_yes=getattr(args, "yes", False),
    )
    if preflight is not None:
        return

    print(f"\n{'=' * 55}")
    print(" sage Repair")
    print(f"{'=' * 55}\n")
    print(f"  Nook: {nook_path}")

    backend = ChromaBackend()

    # Try to read existing drawers
    try:
        col = backend.get_collection(nook_path, collection_name)
        total = col.count()
        print(f"  Drawers found: {total}")
    except Exception as e:
        print(f"  Error reading nook: {e}")
        print("  Cannot recover — nook may need to be re-mined from source files.")
        return

    if total == 0:
        print("  Nothing to repair.")
        return

    if not confirm_destructive_action("Repair", nook_path, assume_yes=getattr(args, "yes", False)):
        return

    # Extract all drawers in batches
    print("\n  Extracting drawers...")
    batch_size = 5000
    all_ids, all_docs, all_metas = _extract_drawers(col, total, batch_size)
    print(f"  Extracted {len(all_ids)} drawers")

    # ── #1208 guard ──────────────────────────────────────────────────
    # Cross-check against the SQLite ground truth before doing anything
    # destructive. Catches the user-reported case where chromadb's
    # collection-layer get() silently caps at 10,000 rows even on much
    # larger nooks (e.g. after manual HNSW quarantine). Override with
    # --confirm-truncation-ok only after independently verifying the
    # extraction count is real.
    try:
        check_extraction_safety(
            nook_path,
            len(all_ids),
            confirm_truncation_ok=getattr(args, "confirm_truncation_ok", False),
            collection_name=collection_name,
        )
    except TruncationDetected as e:
        print(e.message)
        return

    nook_path = os.path.normpath(nook_path)
    backup_path = nook_path + ".backup"
    if os.path.exists(backup_path):
        if not contains_nook_database(backup_path):
            print(
                "  Backup validation failed: backup path exists but does not contain chroma.sqlite3. "
                f"Please remove or rename: {backup_path}"
            )
            return
        shutil.rmtree(backup_path)
    print(f"  Backing up to {backup_path}...")
    shutil.copytree(nook_path, backup_path)

    try:
        filed = _rebuild_collection_via_temp(
            backend,
            nook_path,
            all_ids,
            all_docs,
            all_metas,
            batch_size,
            collection_name=collection_name,
            progress=print,
        )
    except RebuildCollectionError as e:
        print(f"  Repair failed: {e}")
        if getattr(e, "live_replaced", False):
            print("  Live collection was already replaced; restoring from backup...")
            try:
                _close_chroma_handles(nook_path, backend=backend)
                if os.path.exists(nook_path):
                    shutil.rmtree(nook_path)
                shutil.copytree(backup_path, nook_path)
                print(f"  Restore complete from backup: {backup_path}")
            except Exception as restore_error:
                print(f"  Automatic restore failed: {restore_error}")
                print("  Manual recovery required:")
                print(f"    1. Remove or rename the broken directory: {nook_path}")
                print(f"    2. Restore the backup directory to: {nook_path}")
                print(f"       Backup location: {backup_path}")
        sys.exit(1)

    print(f"\n  Repair complete. {filed} drawers rebuilt.")
    print(f"  Backup saved at {backup_path}")
    print(f"\n{'=' * 55}\n")


def cmd_hook(args):
    """Run hook logic: reads JSON from stdin, outputs JSON to stdout."""
    from .hooks_cli import run_hook

    run_hook(hook_name=args.hook, harness=args.harness)


def cmd_instructions(args):
    """Output skill instructions to stdout."""
    from .instructions_cli import run_instructions

    run_instructions(name=args.name)


def cmd_mcp(args):
    """Show how to wire sage into MCP-capable hosts."""
    base_server_cmd = "sage-mcp"

    if args.nook:
        resolved_nook = str(Path(args.nook).expanduser())
        server_cmd = f"{base_server_cmd} --nook {shlex.quote(resolved_nook)}"
    else:
        server_cmd = base_server_cmd

    print("sage MCP quick setup:")
    print(f"  claude mcp add sage -- {server_cmd}")
    print(f"  codex mcp add sage -- {server_cmd}")
    print("\nRun the server directly:")
    print(f"  {server_cmd}")

    if not args.nook:
        print("\nOptional custom nook:")
        print(f"  claude mcp add sage -- {base_server_cmd} --nook /path/to/nook")
        print(f"  codex mcp add sage -- {base_server_cmd} --nook /path/to/nook")
        print(f"  {base_server_cmd} --nook /path/to/nook")


@dataclass
class DiscoveredRepo:
    """A candidate wing discovered during bootstrap discovery."""

    slug: str
    wing_type: str
    path: str


def discover_repos(roots: list[tuple[str, str]]) -> list:
    """Discover candidate wings from a list of root directories.

    Each root is treated as a *parent* whose immediate child directories
    are candidate wings.  Applies the CLAUDE.md §9 dev-layout convention
    when called with the defaults:

    - ``~/dev/github/<owner>/<repo>/`` → each ``<repo>`` dir, type ``dev``.
    - ``~/dev/projects/<name>/`` → each ``<name>`` dir, type ``project``.

    When ``--root`` overrides are supplied, every given path is treated as
    a parent of ``dev``-type candidates (simple, predictable contract for
    ad-hoc trees).

    Args:
        roots: List of ``(path, wing_type)`` tuples. Each tuple names a
               parent directory whose immediate children are candidate wings.

    Rules:
    - Only directories; hidden/dotfiles skipped.
    - Symlinked candidates are skipped (avoid minging out-of-tree targets).
    - Roots that are themselves git repos (contain ``.git``) are skipped with
      a warning — their subdirs (``src``, ``docs``, …) are not separate wings.
    - Slugs already registered are skipped (reported as "already registered").
    - Slug collision across roots → first root wins, warning printed.

    Returns a list of :class:`DiscoveredRepo`.
    """
    from .extensions.wing_registry import is_registered

    seen_slugs: dict = {}  # slug → DiscoveredRepo (first win)
    found: list = []
    warnings: list = []

    for root_path, wing_type in roots:
        root = Path(root_path).expanduser().resolve()

        if not root.is_dir():
            continue

        # FIX 5: skip roots that are themselves git repos.
        if (root / ".git").exists():
            print(
                f"  WARNING: root {root} is itself a git repo;"
                " skipping (its subdirs are not separate wings)"
            )
            continue

        for candidate in sorted(root.iterdir()):
            # FIX 3: skip symlinked candidates before is_dir() check.
            if candidate.is_symlink():
                continue
            if not candidate.is_dir():
                continue
            if candidate.name.startswith("."):
                continue
            slug = candidate.name
            if is_registered(slug):
                # Don't silently swallow a user repo that collides with a
                # FRAMEWORK-INTERNAL wing (Personal, telemetry — path under
                # ~/.sage). Warn with an actionable message so the user can
                # rename or register under a different slug; a collision with
                # an already-registered project/dev wing stays a silent skip
                # (it is genuinely already theirs).
                from .extensions.wing_registry import registered_wings

                _reg = registered_wings().get(slug, {})
                _reg_path = str(_reg.get("path", ""))
                if _reg_path and str(Path(_reg_path).expanduser()).startswith(
                    str(Path.home() / ".sage")
                ):
                    import shlex

                    _quoted = shlex.quote(str(candidate))
                    warnings.append(
                        f"  WARNING: repo '{slug}' at {candidate} collides with the"
                        f" framework-internal wing '{slug}' ({_reg_path}); the repo was"
                        f" NOT registered/mined. Rename the repo or register it under a"
                        f" different slug with its path: sage wing add <new-slug>"
                        f" --type {wing_type} --path {_quoted}"
                    )
                continue
            if slug in seen_slugs:
                warnings.append(
                    f"  WARNING: slug collision '{slug}' (skipping {candidate},"
                    f" already queued from {seen_slugs[slug].path})"
                )
                continue
            repo = DiscoveredRepo(slug=slug, wing_type=wing_type, path=str(candidate))
            seen_slugs[slug] = repo
            found.append(repo)

    for w in warnings:
        print(w)

    return found


def _default_bootstrap_roots() -> list:
    """Return the default root list following CLAUDE.md §9 dev-layout.

    Returns a list of (path, wing_type) tuples consumed by discover_repos.
    """
    home = Path.home()
    roots = []
    # ~/dev/github/<owner>/<repo>/ — two-level: walk github children as owners
    github_root = home / "dev" / "github"
    if github_root.is_dir():
        for owner_dir in sorted(github_root.iterdir()):
            if owner_dir.is_dir() and not owner_dir.name.startswith("."):
                roots.append((str(owner_dir), "dev"))
    # ~/dev/projects/<name>/
    projects_root = home / "dev" / "projects"
    if projects_root.is_dir():
        roots.append((str(projects_root), "project"))
    return roots


def _bootstrap_count_already_registered(roots: list) -> int:
    """Count how many candidate slugs in *roots* are already registered."""
    from .extensions.wing_registry import is_registered

    n: int = 0
    for root_path, _ in roots:
        root = Path(root_path).expanduser().resolve()
        if not root.is_dir():
            continue
        for candidate in root.iterdir():
            # FIX 5: mirror discover_repos symlink skip for count parity.
            if candidate.is_symlink():
                continue
            if candidate.is_dir() and not candidate.name.startswith("."):
                if is_registered(candidate.name):
                    n += 1
    return n


def _bootstrap_mine_candidates(
    discovered: list,
    yes: bool,
    nook_path: str,
) -> tuple:
    """Ask the user (if interactive) and mine the candidates.

    Returns (mine_ok, mine_errors) — two lists.
    mine_ok: list of slugs successfully mined.
    mine_errors: list of (slug, error_str) tuples.
    """
    from .miner import mine as _mine

    mine_candidates = [r for r in discovered if Path(r.path).is_dir()]
    if not mine_candidates:
        return [], []

    proceed = False
    if yes:
        proceed = True
    elif not sys.stdin.isatty():
        print(
            f"\n  Non-interactive stdin — skipping mine for {len(mine_candidates)} repo(s)."
            "  Re-run with --yes to mine without prompting."
        )
    else:
        try:
            ans = input(f"\n  Mine {len(mine_candidates)} repo(s) into the nook now? [y/N] ")
            proceed = ans.strip().lower() in ("y", "yes")
        except EOFError:
            print("\n  EOF on stdin — skipping mine.  Re-run with --yes to mine without prompting.")

    mine_ok: list = []
    mine_errors: list = []
    if proceed:
        for repo in mine_candidates:
            print(f"  mining {repo.slug}…")
            try:
                _mine(
                    project_dir=repo.path,
                    nook_path=nook_path,
                    wing_override=repo.slug,
                )
                mine_ok.append(repo.slug)
            except Exception as exc:  # noqa: BLE001
                mine_errors.append((repo.slug, str(exc)))
                print(f"  ERROR mining {repo.slug}: {exc}", file=sys.stderr)
    return mine_ok, mine_errors


def _bootstrap_build_registry() -> tuple:
    """Build the registry and return ``(count, ok)`` — count of entries and a success flag.

    Returns ``(count, True)`` on success, ``(0, False)`` on failure or when the
    repo root cannot be detected (so callers can propagate a nonzero exit code).
    """
    from .extensions.skill_registry import build_registry

    here = Path(__file__).resolve().parent
    repo_root = None
    for candidate in [here.parent.parent, here.parent, here]:
        if (candidate / "agents").is_dir() or (candidate / "skills").is_dir():
            repo_root = str(candidate)
            break
    if not repo_root:
        print("\n  Registry: could not detect repo root — skipped.")
        return 0, False
    try:
        entries = build_registry(repo_root, force_rebuild=True)
        count = len(entries)
        print(f"\n  Registry built: {count} entries.")
        return count, True
    except Exception as exc:  # noqa: BLE001
        print(f"\n  Registry build failed: {exc}", file=sys.stderr)
        return 0, False


def cmd_bootstrap(args):
    """`sage bootstrap` — discover repos, register wings, mine, build registry.

    Sequence:
      1. Verify wing_config.json exists and is valid JSON (fail loud otherwise —
         use 'sage init' to create it).
      2. Discover candidate wings under the configured roots.  Already-registered
         slugs are skipped by discover_repos; bootstrap registers + mines only
         repos that are NEW this run.  Re-mine already-registered wings with
         ``sage mine <dir>``.
      3. Print the plan (what would be registered + mined).
      4. --dry-run: print plan and exit without writing.
      5. Register each new wing (idempotent — already-registered skipped).
      6. Mine only successfully-registered wings (gated: prompts unless --yes or
         non-TTY).  Skip entirely with --no-mine.
      7. Build the skill/agent registry.
      8. Print a summary.
    """
    from .extensions.wing_registry import add_wing, is_registered, load_config

    # ── 1. Config-absent / invalid-JSON guard ───────────────────────────────
    try:
        load_config()
    except (FileNotFoundError, ValueError) as exc:
        print(
            f"wing_config.json is missing or invalid — run 'sage init' first"
            f" to create the nook, then re-run 'sage bootstrap'. ({exc})",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── 2. Build root list ──────────────────────────────────────────────────
    raw_roots = getattr(args, "bootstrap_roots", None) or []
    roots: list = (
        [(str(Path(r).expanduser()), "dev") for r in raw_roots]
        if raw_roots
        else _default_bootstrap_roots()
    )

    discovered = discover_repos(roots)
    n_new = len(discovered)
    n_already = _bootstrap_count_already_registered(roots)

    print(
        f"\n  Bootstrap plan: {n_new} new wing(s) to register,"
        f" {n_already} already registered (skipped)."
    )
    if n_new == 0:
        print("  Nothing to register.")

    if discovered:
        print("\n  Would register:")
        for repo in discovered:
            print(f"    {repo.slug:28}  type={repo.wing_type}  path={repo.path}")

    # ── 3. --dry-run exit ───────────────────────────────────────────────────
    if getattr(args, "dry_run", False):
        mine_candidates = [r for r in discovered if Path(r.path).is_dir()]
        if mine_candidates and not getattr(args, "no_mine", False):
            print(f"\n  Would mine {len(mine_candidates)} repo(s) into the nook.")
        print("\n  Dry-run — nothing written.\n")
        return

    # ── 4. Register ─────────────────────────────────────────────────────────
    registered_this_run: list = []
    failed_registrations: list = []  # (slug, error_str) for summary
    for repo in discovered:
        if is_registered(repo.slug):
            print(f"  skip (already registered): {repo.slug}")
            continue
        try:
            add_wing(repo.slug, repo.wing_type, path=repo.path)
            print(f"  registered: {repo.slug}  (type={repo.wing_type}, path={repo.path})")
            registered_this_run.append(repo)
        except ValueError as exc:
            # Duplicate slug race: treat as skip.
            print(f"  skip ({exc}): {repo.slug}")
        except OSError as exc:
            # FIX 4: catch OSError/FileNotFoundError from add_wing, continue batch.
            msg = f"  ERROR registering {repo.slug}: {exc}"
            print(msg, file=sys.stderr)
            failed_registrations.append((repo.slug, str(exc)))

    # ── 5. Mine (gated) ─────────────────────────────────────────────────────
    mine_ok: list = []
    mine_errors: list = []

    if getattr(args, "no_mine", False):
        print("\n  --no-mine set: skipping mining.")
    else:
        nook_path = (
            os.path.expanduser(args.nook) if getattr(args, "nook", None) else SageConfig().nook_path
        )
        # FIX 1: only mine repos that were successfully registered this run.
        # Repos whose add_wing call failed are in failed_registrations, not
        # registered_this_run, so passing registered_this_run here ensures
        # no orphan wing slugs are mined into an unregistered wing.
        mine_ok, mine_errors = _bootstrap_mine_candidates(
            registered_this_run,
            yes=getattr(args, "yes", False),
            nook_path=nook_path,
        )

    # ── 6. Build registry ───────────────────────────────────────────────────
    registry_count, registry_ok = _bootstrap_build_registry()

    # ── 7. Summary ──────────────────────────────────────────────────────────
    print("\n  Bootstrap summary:")
    print(f"    Wings registered this run : {len(registered_this_run)}")
    if failed_registrations:
        print(f"    Wings failed to register  : {len(failed_registrations)}")
        for slug, err in failed_registrations:
            print(f"      {slug}: {err}", file=sys.stderr)
    if mine_ok:
        print(f"    Repos mined (ok)          : {len(mine_ok)}")
    if mine_errors:
        print(f"    Repos mined (failed)      : {len(mine_errors)}")
        for slug, err in mine_errors:
            print(f"      {slug}: {err}", file=sys.stderr)
    if registry_count:
        print(f"    Registry entries          : {registry_count}")
    print()

    if mine_errors or not registry_ok:
        sys.exit(1)


def _cmd_export_framework(args):
    """`sage export framework <dest>` — clean-room allowlist export (ADR-0071)."""
    from .export import ExportPIIError, export

    repo_root_str = getattr(args, "export_root", None)
    if not repo_root_str:
        here = Path(__file__).resolve().parent
        for candidate in [here.parent.parent, here.parent, here]:
            if (candidate / "skills").is_dir() and (candidate / "src").is_dir():
                repo_root_str = str(candidate)
                break
    if not repo_root_str:
        print("export framework: could not detect repo root. Pass --root.", file=sys.stderr)
        sys.exit(1)
    dest = Path(getattr(args, "dest")).expanduser()
    try:
        shipped = export(
            repo_root_str,
            dest,
            run_pii_gate=not getattr(args, "no_pii_gate", False),
            init_git=not getattr(args, "no_git", False),
        )
    except ExportPIIError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print(f"export framework: {len(shipped)} files → {dest}")


def cmd_export(args):
    """`sage export` — validate and export skills as portable bundles.

    Subcommands:
      skill <name>          — validate + export one skill by name
      skill --all           — validate + export every skill; print per-skill summary
    """
    sub = getattr(args, "export_command", None)

    if sub == "framework":
        _cmd_export_framework(args)
        return

    from .skill_exporter import ExportResult, SkillExportError, validate_skill, export_skill

    if sub != "skill":
        print("export: subcommand required (skill | framework).", file=sys.stderr)
        sys.exit(2)

    # Resolve repo root (mirrors cmd_registry resolution pattern).
    repo_root_str = getattr(args, "export_root", None)
    if not repo_root_str:
        here = Path(__file__).resolve().parent
        for candidate in [here.parent.parent, here.parent, here]:
            if (candidate / "skills").is_dir():
                repo_root_str = str(candidate)
                break
    if not repo_root_str:
        print("export: could not detect repo root. Pass --root /path/to/repo.", file=sys.stderr)
        sys.exit(1)

    repo_root = Path(repo_root_str)
    skills_dir = repo_root / "skills"

    dest_raw = getattr(args, "dest", None)
    dest_dir = Path(dest_raw).expanduser() if dest_raw else Path.cwd() / "exported-skills"
    force = getattr(args, "force", False)

    skill_name_arg: str | None = getattr(args, "skill_name", None)
    export_all: bool = getattr(args, "all", False)

    if not export_all and not skill_name_arg:
        print("export skill: provide a skill name or --all.", file=sys.stderr)
        sys.exit(2)

    if export_all:
        # Export every skill directory under skills/.
        if not skills_dir.is_dir():
            print(f"export: skills/ directory not found under {repo_root}", file=sys.stderr)
            sys.exit(1)

        skill_dirs = sorted(
            d for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").exists()
        )
        if not skill_dirs:
            print("export: no skills found.", file=sys.stderr)
            sys.exit(1)

        any_fail = False
        any_oserror = False
        for skill_dir in skill_dirs:
            issues = validate_skill(skill_dir)
            if issues and not force:
                any_fail = True
                print(f"  FAIL  {skill_dir.name}")
                for issue in issues:
                    print(f"        {issue}")
            else:
                try:
                    result: ExportResult = export_skill(skill_dir, dest_dir, force=force)
                    if issues:
                        print(
                            f"  FORCED {result.skill_name}  ({len(issues)} issue(s))"
                            f"  → {result.dest_bundle}"
                        )
                    else:
                        print(f"  PASS  {result.skill_name}  → {result.dest_bundle}")
                except (SkillExportError, OSError, ValueError) as exc:
                    any_oserror = True
                    print(f"  FAIL  {skill_dir.name}: {exc}")

        if any_oserror or (any_fail and not force):
            sys.exit(1)
        return

    # Single-skill export.
    skill_dir = skills_dir / skill_name_arg
    if not skill_dir.is_dir():
        print(f"export: skill '{skill_name_arg}' not found at {skill_dir}", file=sys.stderr)
        sys.exit(1)

    issues = validate_skill(skill_dir)
    if issues and not force:
        print(f"Skill '{skill_name_arg}' failed agentskills.io conformance:")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)

    try:
        result = export_skill(skill_dir, dest_dir, force=force)
    except (SkillExportError, OSError, ValueError) as exc:
        print(f"export: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Exported '{result.skill_name}' → {result.dest_bundle}")
    print(f"  Files written: {len(result.written_files)}")


def _reconfigure_stdio_utf8_on_windows():
    """Decode stdio as UTF-8 on Windows for the primary `sage` CLI.

    Thin wrapper around the shared helper in ``sage._stdio``. The CLI
    overrides stdout/stderr to ``replace`` because ``sage search``
    prints verbatim drawer text that may carry surrogate halves
    round-tripped from filenames -- ``strict`` would crash mid-print and
    lose the rest of the search result block. stdin keeps the default
    ``surrogateescape`` so a redirected non-UTF-8 file does not kill the
    read on the first bad byte.
    """
    from ._stdio import reconfigure_stdio_utf8_on_windows

    reconfigure_stdio_utf8_on_windows(stdout_errors="replace", stderr_errors="replace")


def main():
    """CLI entry point for the ``sage`` console script.

    Side effect: pops ``PYTHONPATH`` from ``os.environ`` (see #1423) so
    any subprocess this CLI spawns inherits a clean env. Host applications
    that call ``main()`` programmatically should be aware that the parent
    process loses ``PYTHONPATH`` as well. Library imports
    (``import sage_mcp.searcher`` from a host app) do NOT trigger this
    side effect; only the CLI/MCP entry points pop the env var.
    """
    # Drop leaked PYTHONPATH so any subprocess the CLI spawns (mine workers,
    # repair tooling) starts with a clean env. The sys.path filter in
    # sage/__init__.py already protects this process from the same
    # ABI mismatch; here we extend the protection to children.
    os.environ.pop("PYTHONPATH", None)

    _reconfigure_stdio_utf8_on_windows()

    version_label = f"sage {__version__}"
    parser = argparse.ArgumentParser(
        description="sage — Give your AI a memory. No API key required.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"{version_label}\n\n{__doc__}",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=version_label,
        help="Show version and exit",
    )
    parser.add_argument(
        "--nook",
        default=None,
        help="Where the nook lives (default: from ~/.sage/config.json or ~/.sage/nook)",
    )

    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="Detect rooms from your folder structure")
    p_init.add_argument("dir", help="Project directory to set up")
    p_init.add_argument(
        "--yes",
        action="store_true",
        help="Auto-accept all detected entities (non-interactive)",
    )
    p_init.add_argument(
        "--auto-mine",
        action="store_true",
        help=(
            "Skip the post-init mine prompt and run mine automatically. "
            "Combine with --yes for a fully non-interactive setup."
        ),
    )
    p_init.add_argument(
        "--lang",
        default=None,
        help=(
            "Comma-separated language codes for entity detection "
            "(e.g. 'en' or 'en,pt-br'). Defaults to value from config "
            "(SAGE_ENTITY_LANGUAGES env var or config.json), or 'en'. "
            "When given, the value is also persisted to config.json."
        ),
    )
    p_init.add_argument(
        "--llm",
        action="store_true",
        help=(
            "DEPRECATED — LLM-assisted entity refinement is now ON by default. "
            "This flag is preserved for backward compatibility; pass --no-llm "
            "to opt out instead."
        ),
    )
    p_init.add_argument(
        "--no-llm",
        action="store_true",
        help=(
            "Disable LLM-assisted entity refinement. Run init in heuristics-only "
            "mode (no provider acquisition, no LLM calls). Use when running "
            "without a local LLM and you don't want the graceful-fallback message."
        ),
    )
    p_init.add_argument(
        "--llm-provider",
        default="ollama",
        choices=["ollama", "openai-compat", "anthropic"],
        help="LLM provider (default: ollama). Pass --no-llm to disable LLM-assisted refinement entirely.",
    )
    p_init.add_argument(
        "--llm-model",
        default="gemma4:e4b",
        help="Model name for the chosen provider (default: gemma4:e4b for Ollama).",
    )
    p_init.add_argument(
        "--llm-endpoint",
        default=None,
        help=(
            "Provider endpoint URL. Default for Ollama: http://localhost:11434. "
            "Required for openai-compat."
        ),
    )
    p_init.add_argument(
        "--llm-api-key",
        default=None,
        help=(
            "API key for the provider. For anthropic, defaults to $ANTHROPIC_API_KEY; "
            "for openai-compat, defaults to $OPENAI_API_KEY."
        ),
    )
    p_init.add_argument(
        "--accept-external-llm",
        action="store_true",
        help=(
            "Bypass the interactive consent prompt that fires when an external "
            "LLM is configured via an environment-variable API key (issue #26). "
            "Use this in CI / non-interactive runs where you've already decided "
            "the external send is acceptable."
        ),
    )

    # mine
    p_mine = sub.add_parser("mine", help="Mine files into the nook")
    p_mine.add_argument("dir", help="Directory to mine")
    p_mine.add_argument(
        "--mode",
        choices=["projects", "convos", "extract"],
        default="projects",
        help=(
            "Ingest mode: 'projects' for code/docs (default), 'convos' for chat "
            "exports, 'extract' for office documents (PDF/DOCX/RTF/etc., requires "
            "sage[extract])"
        ),
    )
    p_mine.add_argument("--wing", default=None, help="Wing name (default: directory name)")
    p_mine.add_argument(
        "--no-gitignore",
        action="store_true",
        help="Don't respect .gitignore files when scanning project files",
    )
    p_mine.add_argument(
        "--include-ignored",
        action="append",
        default=[],
        help="Always scan these project-relative paths even if ignored; repeat or pass comma-separated paths",
    )
    p_mine.add_argument(
        "--agent",
        default="sage",
        help=(
            "Your name — recorded on every drawer (default: 'sage'). "
            "Pass --agent <your-handle> to attribute mines to you."
        ),
    )
    p_mine.add_argument("--limit", type=int, default=0, help="Max files to process (0 = all)")
    p_mine.add_argument(
        "--redetect-origin",
        action="store_true",
        help=(
            "Re-run corpus_origin detection on this directory and overwrite "
            "<nook>/.sage/origin.json. Useful when the corpus has grown "
            "since `sage init` and the stored origin may be stale. "
            "Heuristic-only (no LLM call) — re-run `sage init --llm` for "
            "Tier 2 refinement."
        ),
    )
    p_mine.add_argument(
        "--dry-run", action="store_true", help="Show what would be filed without filing"
    )
    p_mine.add_argument(
        "--extract",
        choices=["exchange", "general"],
        default="exchange",
        help="Extraction strategy for convos mode: 'exchange' (default) or 'general' (5 memory types)",
    )
    from . import miner as _miner_for_default

    p_mine.add_argument(
        "--max-chunks-per-file",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"Per-file chunk cap; files producing more chunks are skipped with a "
            f"summary counter. Default {_miner_for_default.MAX_CHUNKS_PER_FILE} "
            f"(or SAGE_MAX_CHUNKS_PER_FILE). Set 0 to disable. Lower this on "
            f"Windows if you hit ONNX bad_alloc (#1455)."
        ),
    )
    p_mine.add_argument(
        "--agents",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "Tag every drawer this mine writes with one or more agent names "
            "(sage extension). Pass repeatedly (--agents X --agents Y) "
            "or comma-separated (--agents X,Y). Recall later via "
            "`sage recall --agent X` or `nook_search agents=[X]`."
        ),
    )

    # sweep
    p_sweep = sub.add_parser(
        "sweep",
        help="Tandem miner: catch anything the primary miner missed "
        "(message-level, timestamp-coordinated, idempotent)",
    )
    p_sweep.add_argument(
        "target",
        help="A .jsonl transcript file, or a directory to scan recursively",
    )

    # sync
    p_sync = sub.add_parser(
        "sync",
        help="Prune drawers whose source files are gitignored, deleted, or moved (#1252)",
    )
    p_sync.add_argument(
        "dir",
        nargs="?",
        default=None,
        help="Project root to sync (optional; auto-detects from drawer metadata)",
    )
    p_sync.add_argument("--wing", default=None, help="Limit to one wing")
    p_sync.add_argument(
        "--root",
        action="append",
        default=[],
        help="Additional project root (repeatable)",
    )
    p_sync.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Preview only (default)",
    )
    p_sync.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Actually delete drawers (overrides --dry-run; requires --wing or a project root)",
    )

    # search
    p_search = sub.add_parser("search", help="Find anything, exact words")
    p_search.add_argument("query", help="What to search for")
    p_search.add_argument("--wing", default=None, help="Limit to one project")
    p_search.add_argument("--room", default=None, help="Limit to one room")
    p_search.add_argument(
        "--agent",
        default=None,
        help="Limit to drawers tagged with this agent name (sage extension)",
    )
    p_search.add_argument("--results", type=int, default=5, help="Number of results")

    # recall — agent-keyed search (sage extension)
    p_recall = sub.add_parser(
        "recall",
        help="Find drawers tagged with a specific agent (agent-keyed search)",
    )
    p_recall.add_argument("query", help="What to search for")
    p_recall.add_argument(
        "--agent", default=None, help="Agent name to filter by (e.g. aidev-code-reviewer)"
    )
    p_recall.add_argument("--wing", default=None, help="Limit to one wing")
    p_recall.add_argument("--results", type=int, default=5, help="Number of results")

    # wing — registry management (sage extension)
    p_wing = sub.add_parser(
        "wing",
        help="Manage the wing registry (list / add). sage gates drawer writes on registration.",
    )
    p_wing_sub = p_wing.add_subparsers(dest="wing_command")
    p_wing_sub.add_parser("list", help="List all registered wings, grouped by type")
    p_wing_add = p_wing_sub.add_parser("add", help="Register a new wing")
    p_wing_add.add_argument("slug", help="Wing slug (the name used in nook_search / mine --wing)")
    p_wing_add.add_argument(
        "--type",
        required=True,
        choices=["dev", "project", "knowledge", "ops", "meta"],
        help="Wing type (controls hall set + L1 wake-up halls)",
    )
    p_wing_add.add_argument(
        "--path",
        default=None,
        help="Filesystem path this wing tracks (optional but recommended)",
    )

    # registry — skill/agent/script metadata registry
    p_registry = sub.add_parser(
        "registry",
        help="Build or search the skill/agent/script metadata registry (name, one_line, triggers, path)",
    )
    p_registry.add_argument(
        "--root",
        dest="registry_root",
        default=None,
        help="Path to the repo root containing agents/ and skills/ (auto-detected if omitted)",
    )
    p_registry.add_argument(
        "--rebuild",
        dest="force_rebuild",
        action="store_true",
        default=False,
        help="Re-scan from disk, bypassing the in-process cache",
    )
    p_registry_sub = p_registry.add_subparsers(dest="registry_command")
    p_registry_sub.add_parser("build", help="Scan and build the registry; print entry counts")
    p_registry_search = p_registry_sub.add_parser("search", help="Keyword search the registry")
    p_registry_search.add_argument("query", nargs="?", default="", help="Search query (keywords)")
    p_registry_search.add_argument(
        "--kind",
        choices=["agent", "skill", "script"],
        default=None,
        help="Filter by artifact kind",
    )
    p_registry_search.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max results (default 10)",
    )

    # bootstrap — one-command first-time setup
    p_bootstrap = sub.add_parser(
        "bootstrap",
        help=(
            "Discover repos, register wings, mine, and build the registry in one step. "
            "Run once after install to populate the nook."
        ),
    )
    p_bootstrap.add_argument(
        "--root",
        dest="bootstrap_roots",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Parent directory whose immediate child dirs are candidate dev-type wings "
            "(repeatable). Replaces the default ~/dev/github/<owner>/ + ~/dev/projects/ "
            "layout when given. Expand ~ automatically."
        ),
    )
    p_bootstrap.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Print the full plan (wings to register + repos to mine) and exit without writing anything.",
    )
    p_bootstrap.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Mine without prompting (non-interactive mode).",
    )
    p_bootstrap.add_argument(
        "--no-mine",
        dest="no_mine",
        action="store_true",
        default=False,
        help="Discover and register wings + build registry, but skip the mine step entirely.",
    )

    # export — agentskills.io conformance validator + portable packager
    p_export = sub.add_parser(
        "export",
        help="Validate and export skills as portable agentskills.io-conformant bundles",
    )
    p_export.add_argument(
        "--root",
        dest="export_root",
        default=None,
        help="Path to the repo root containing skills/ (auto-detected if omitted)",
    )
    p_export_sub = p_export.add_subparsers(dest="export_command")
    p_export_skill = p_export_sub.add_parser(
        "skill",
        help="Validate + export one skill (by name) or all skills (--all)",
    )
    p_export_skill.add_argument(
        "skill_name",
        nargs="?",
        default=None,
        help="Name of the skill to export (directory name under skills/)",
    )
    p_export_skill.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Export all skills and print a per-skill PASS/FAIL conformance summary",
    )
    p_export_skill.add_argument(
        "--dest",
        default=None,
        help="Destination directory for the exported bundle(s) (default: ./exported-skills)",
    )
    p_export_skill.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Export even if the skill fails conformance validation",
    )
    p_export_fw = p_export_sub.add_parser(
        "framework",
        help="Build a clean-room public export of the whole framework (allowlist; fails closed)",
    )
    p_export_fw.add_argument(
        "dest",
        help="Destination directory for the clean export (rebuilt from scratch each run)",
    )
    p_export_fw.add_argument(
        "--no-pii-gate",
        action="store_true",
        default=False,
        help="Skip the export-PII gate (NOT for release; debugging the copy only)",
    )
    p_export_fw.add_argument(
        "--no-git",
        action="store_true",
        default=False,
        help="Skip the distinct-git-root init (debugging the copy only)",
    )

    # audit — operational audits over the nook (sage extension)
    p_audit = sub.add_parser(
        "audit", help="Operational audits over the nook (dispatch-reliability, ...)"
    )
    p_audit_sub = p_audit.add_subparsers(dest="audit_command")
    p_audit_dr = p_audit_sub.add_parser(
        "dispatch-reliability",
        help="Count hook-emergency drawers vs Keeper drawers; surface if the orchestrator is forgetting to dispatch the Keeper.",
    )
    p_audit_dr.add_argument("--days", type=int, default=7, help="Window size in days (default 7)")
    p_audit_dr.add_argument("--wing", default=None, help="Scope to one wing (optional)")
    p_audit_dr.add_argument("-v", "--verbose", action="store_true", help="List recent hook drawers")

    # verdict — parse + log an auditor verdict reply (improvement 6+7)
    p_verdict = sub.add_parser(
        "verdict",
        help="Parse + log a structured auditor verdict (improvement 6+7)",
    )
    p_verdict_sub = p_verdict.add_subparsers(dest="verdict_command")
    p_verdict_log = p_verdict_sub.add_parser(
        "log",
        help="Parse one auditor reply (stdin or --file) and log a telemetry row",
    )
    p_verdict_log.add_argument(
        "--file",
        default=None,
        help="Read the auditor reply from this file (default: stdin)",
    )
    p_verdict_log.add_argument(
        "--phase",
        default="audit",
        help="Telemetry phase tag (plan|dispatch|audit|implement|commit|self-check)",
    )
    p_verdict_log.add_argument(
        "--mode",
        default="aidev",
        choices=["aidev", "normal"],
        help="Mode the orchestrator is running in",
    )
    p_verdict_log.add_argument("--wing", default=None, help="Current wing at turn time")
    p_verdict_log.add_argument(
        "--turn-id",
        dest="turn_id",
        default=None,
        help="Pin a specific turn_id (for paired-auditor rows)",
    )

    # tunnel — curate cross-wing tunnels (sage extension)
    p_tunnel = sub.add_parser(
        "tunnel",
        help="Curate cross-wing tunnels (list / create / delete / follow / find)",
    )
    p_tunnel_sub = p_tunnel.add_subparsers(dest="tunnel_command")

    p_tunnel_list = p_tunnel_sub.add_parser(
        "list", help="List tunnels (optionally scoped to one wing)"
    )
    p_tunnel_list.add_argument("--wing", default=None, help="Filter to tunnels touching this wing")

    p_tunnel_create = p_tunnel_sub.add_parser("create", help="Create a new tunnel")
    p_tunnel_create.add_argument("source_wing")
    p_tunnel_create.add_argument("source_room")
    p_tunnel_create.add_argument("target_wing")
    p_tunnel_create.add_argument("target_room")
    p_tunnel_create.add_argument(
        "--label", default=None, help="Optional human label for the tunnel"
    )

    p_tunnel_delete = p_tunnel_sub.add_parser("delete", help="Delete a tunnel by ID")
    p_tunnel_delete.add_argument("tunnel_id")

    p_tunnel_follow = p_tunnel_sub.add_parser(
        "follow", help="Show rooms connected to (wing, room) via tunnels"
    )
    p_tunnel_follow.add_argument("wing")
    p_tunnel_follow.add_argument("room")

    p_tunnel_find = p_tunnel_sub.add_parser(
        "find", help="Find candidate tunnel-worthy room bridges between two wings"
    )
    p_tunnel_find.add_argument(
        "--wing-a", dest="wing_a", default=None, help="One side of the bridge"
    )
    p_tunnel_find.add_argument(
        "--wing-b", dest="wing_b", default=None, help="Other side of the bridge"
    )

    # wake-up
    p_wakeup = sub.add_parser("wake-up", help="Show L0 + L1 wake-up context (~600-900 tokens)")
    p_wakeup.add_argument("--wing", default=None, help="Wake-up for a specific project/wing")

    # split
    p_split = sub.add_parser(
        "split",
        help="Split concatenated transcript mega-files into per-session files (run before mine)",
    )
    p_split.add_argument("dir", help="Directory containing transcript files")
    p_split.add_argument(
        "--output-dir",
        default=None,
        help="Write split files here (default: same directory as source files)",
    )
    p_split.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be split without writing files",
    )
    p_split.add_argument(
        "--min-sessions",
        type=int,
        default=2,
        help="Only split files containing at least N sessions (default: 2)",
    )

    # hook
    p_hook = sub.add_parser(
        "hook",
        help="Run hook logic (reads JSON from stdin, outputs JSON to stdout)",
    )
    hook_sub = p_hook.add_subparsers(dest="hook_action")
    p_hook_run = hook_sub.add_parser("run", help="Execute a hook")
    p_hook_run.add_argument(
        "--hook",
        required=True,
        choices=["session-start", "stop", "precompact"],
        help="Hook name to run",
    )
    p_hook_run.add_argument(
        "--harness",
        required=True,
        choices=["claude-code", "codex"],
        help="Harness type (determines stdin JSON format)",
    )

    # instructions
    p_instructions = sub.add_parser(
        "instructions",
        help="Output skill instructions to stdout",
    )
    instructions_sub = p_instructions.add_subparsers(dest="instructions_name")
    for instr_name in ["init", "search", "mine", "help", "status"]:
        instructions_sub.add_parser(instr_name, help=f"Output {instr_name} instructions")

    # repair
    p_repair = sub.add_parser(
        "repair",
        help=(
            "Rebuild nook vector index (default --mode full) or un-poison "
            "max_seq_id rows (--mode max-seq-id)"
        ),
    )
    p_repair.add_argument(
        "--yes", action="store_true", help="Skip confirmation for destructive changes"
    )
    p_repair.add_argument(
        "--confirm-truncation-ok",
        action="store_true",
        help=(
            "Override the #1208 safety guard. Required when chromadb's collection-layer "
            "extraction returns exactly 10,000 drawers and the SQLite ground-truth check "
            "either matches or can't be read. Use only after independently confirming "
            "the nook really contains that count."
        ),
    )
    p_repair.add_argument(
        "--mode",
        choices=["full", "max-seq-id", "from-sqlite"],
        default="full",
        help=(
            "full: full-nook rebuild via the chromadb client (default). "
            "max-seq-id: un-poison max_seq_id rows corrupted by an older "
            "chromadb 0.6.x shim. "
            "from-sqlite: rebuild by reading rows directly from chroma.sqlite3, "
            "bypassing the chromadb client. Use when full mode bails because "
            "the chromadb client cannot open the collection."
        ),
    )
    p_repair.add_argument(
        "--source",
        default=None,
        help=(
            "Source nook path for --mode from-sqlite (defaults to --nook). "
            "Use when extracting from an archived corrupt nook into a new location."
        ),
    )
    p_repair.add_argument(
        "--archive-existing",
        action="store_true",
        help=(
            "For --mode from-sqlite when --source equals --nook: rename the "
            "existing nook to <nook>.pre-rebuild-<timestamp> before "
            "rebuilding so the corrupt copy is preserved."
        ),
    )
    p_repair.add_argument(
        "--segment",
        default=None,
        help="Segment UUID filter for --mode max-seq-id (repairs only that segment).",
    )
    p_repair.add_argument(
        "--from-sidecar",
        default=None,
        help=(
            "Path to a pre-corruption chroma.sqlite3 sidecar (for --mode max-seq-id); "
            "clean values are copied from its max_seq_id table verbatim."
        ),
    )
    p_repair.add_argument(
        "--backup",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Back up SQLite before mutation (default: on)",
    )
    p_repair.add_argument(
        "--dry-run",
        action="store_true",
        help="Print detected poisoned rows and exit without mutation (--mode max-seq-id only)",
    )

    # consolidate — decay + consolidation pass (WI-6)
    p_consolidate = sub.add_parser(
        "consolidate",
        help="Decay + consolidation pass: down-rank stale/duplicate drawers (never deletes)",
    )
    p_consolidate.add_argument("--wing", default=None, help="Scope to one wing (optional)")
    p_consolidate.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="List per-drawer changes",
    )
    p_consolidate.add_argument(
        "--skip-decay",
        action="store_true",
        help="Run consolidation pass only; skip decay",
    )
    p_consolidate.add_argument(
        "--skip-consolidation",
        action="store_true",
        help="Run decay pass only; skip consolidation",
    )
    p_consolidate_sub = p_consolidate.add_subparsers(dest="consolidate_command")
    p_consolidate_sub.add_parser(
        "report",
        help="Dry-run: show what would change, nothing written (default)",
    )
    p_consolidate_sub.add_parser(
        "run",
        help="Apply strength updates (NEVER deletes any drawer)",
    )

    # repair-status — read-only HNSW capacity health check (#1222)
    sub.add_parser(
        "repair-status",
        help="Compare sqlite vs HNSW element counts (read-only; never opens a chromadb client)",
    )

    # mcp
    sub.add_parser(
        "mcp",
        help="Show MCP setup command for connecting sage to your AI client",
    )

    # status
    # migrate
    p_migrate = sub.add_parser(
        "migrate",
        help="Migrate nook data between ChromaDB schema versions",
    )
    p_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without changing anything",
    )
    p_migrate.add_argument(
        "--yes", action="store_true", help="Skip confirmation for destructive changes"
    )

    sub.add_parser("status", help="Show what's been filed")

    sub.add_parser(
        "dashboard",
        help="Governance (verdict-log) + Nook store health dashboard",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Handle two-level subcommands
    if args.command == "hook":
        if not getattr(args, "hook_action", None):
            p_hook.print_help()
            return
        cmd_hook(args)
        return

    if args.command == "instructions":
        name = getattr(args, "instructions_name", None)
        if not name:
            p_instructions.print_help()
            return
        args.name = name
        cmd_instructions(args)
        return

    dispatch = {
        "bootstrap": cmd_bootstrap,
        "init": cmd_init,
        "mine": cmd_mine,
        "split": cmd_split,
        "search": cmd_search,
        "recall": cmd_recall,
        "wing": cmd_wing,
        "registry": cmd_registry,
        "export": cmd_export,
        "tunnel": cmd_tunnel,
        "audit": cmd_audit,
        "verdict": cmd_verdict,
        "sweep": cmd_sweep,
        "sync": cmd_sync,
        "consolidate": cmd_consolidate,
        "mcp": cmd_mcp,
        "wake-up": cmd_wakeup,
        "repair": cmd_repair,
        "repair-status": cmd_repair_status,
        "migrate": cmd_migrate,
        "status": cmd_status,
        "dashboard": cmd_dashboard,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
