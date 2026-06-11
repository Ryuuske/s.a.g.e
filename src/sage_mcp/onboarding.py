#!/usr/bin/env python3
"""
onboarding.py — sage first-run setup.

Asks the user:
  1. How they're using sage (work / personal / combo)
  2. Who the people in their life are (names, nicknames, relationships)
  3. What their projects are
  4. What they want their wings called

Seeds the entity_registry with confirmed data so sage knows your world
from minute one — before a single session is indexed.

Usage:
    python3 -m sage_mcp.onboarding
    or: sage init
"""

from pathlib import Path
from sage_mcp.entity_registry import EntityRegistry
from sage_mcp.entity_detector import detect_entities, scan_for_detection


# ─────────────────────────────────────────────────────────────────────────────
# Default wing taxonomies by mode
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_WINGS = {
    "work": [
        "projects",
        "clients",
        "team",
        "decisions",
        "research",
    ],
    "personal": [
        "family",
        "health",
        "creative",
        "reflections",
        "relationships",
    ],
    "combo": [
        "family",
        "work",
        "health",
        "creative",
        "projects",
        "reflections",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _hr():
    print(f"\n{'─' * 58}")


def _header(text):
    print(f"\n{'=' * 58}")
    print(f"  {text}")
    print(f"{'=' * 58}")


def _ask(prompt, default=None):
    if default:
        val = input(f"  {prompt} [{default}]: ").strip()
        return val if val else default
    return input(f"  {prompt}: ").strip()


def _yn(prompt, default="y"):
    val = input(f"  {prompt} [{'Y/n' if default == 'y' else 'y/N'}]: ").strip().lower()
    if not val:
        return default == "y"
    return val.startswith("y")


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Mode selection
# ─────────────────────────────────────────────────────────────────────────────


def _ask_embedding_model() -> str:
    """Return ``"embeddinggemma"`` (multilingual, default) or ``"minilm"``.

    Defaulting to multilingual: the recall promise breaks for non-EN content
    under the English-only MiniLM default — cross-lingual cosine similarity
    collapses to roughly 0.35 (essentially random). Multilingual is the safe
    default; English-only is offered as an opt-down for users on slow links
    or who really only ever store English content.
    """
    _header("Language support")
    print("""
  sage embeds your memories into a vector space so the AI can find
  related content later. The default model supports 100+ languages,
  including non-Latin scripts (CJK, Cyrillic, Arabic, Devanagari).

    Multilingual: ~300 MB one-time download, works across all languages.
    English-only: ~30 MB, smaller and faster, but cross-lingual recall
                  is poor (a Russian memory and its English translation
                  won't match each other).
""")
    if _yn("  Use the multilingual embedding model?", default="y"):
        return "embeddinggemma"
    return "minilm"


def _ask_mode() -> str:
    _header("Welcome to sage")
    print("""
  sage is a personal memory system. To work well, it needs to know
  a little about your world — who the people are, what the projects
  are, and how you want your memory organized.

  This takes about 2 minutes. You can always update it later.
""")
    print("  How are you using sage?")
    print()
    print("    [1]  Work     — notes, projects, clients, colleagues, decisions")
    print("    [2]  Personal — diary, family, health, relationships, reflections")
    print("    [3]  Both     — personal and professional mixed")
    print()

    while True:
        choice = input("  Your choice [1/2/3]: ").strip()
        if choice == "1":
            return "work"
        elif choice == "2":
            return "personal"
        elif choice == "3":
            return "combo"
        print("  Please enter 1, 2, or 3.")


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: People
# ─────────────────────────────────────────────────────────────────────────────


def _ask_people(mode: str) -> tuple[list, dict]:
    """Returns (people_list, aliases_dict)."""
    people = []
    aliases = {}  # nickname → full name

    if mode in ("personal", "combo"):
        _hr()
        print("""
  Personal world — who are the important people in your life?

  Format: name, relationship (e.g. "Riley, daughter" or just "Devon")
  For nicknames, you'll be asked separately.
  Type 'done' when finished.
""")
        while True:
            entry = input("  Person: ").strip()
            if entry.lower() in ("done", ""):
                break
            parts = [p.strip() for p in entry.split(",", 1)]
            name = parts[0]
            relationship = parts[1] if len(parts) > 1 else ""
            if name:
                # Ask about nicknames
                nick = input(f"  Nickname for {name}? (or enter to skip): ").strip()
                if nick:
                    aliases[nick] = name
                people.append({"name": name, "relationship": relationship, "context": "personal"})

    if mode in ("work", "combo"):
        _hr()
        print("""
  Work world — who are the colleagues, clients, or collaborators
  you'd want to find in your notes?

  Format: name, role (e.g. "Ben, co-founder" or just "Sarah")
  Type 'done' when finished.
""")
        while True:
            entry = input("  Person: ").strip()
            if entry.lower() in ("done", ""):
                break
            parts = [p.strip() for p in entry.split(",", 1)]
            name = parts[0]
            role = parts[1] if len(parts) > 1 else ""
            if name:
                people.append({"name": name, "relationship": role, "context": "work"})

    return people, aliases


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Projects
# ─────────────────────────────────────────────────────────────────────────────


def _ask_projects(mode: str) -> list:
    if mode == "personal":
        return []

    _hr()
    print("""
  What are your main projects? (These help sage distinguish project
  names from person names — e.g. "Lantern" the project vs. "Lantern" the word.)

  Type 'done' when finished.
""")
    projects = []
    while True:
        proj = input("  Project: ").strip()
        if proj.lower() in ("done", ""):
            break
        if proj:
            projects.append(proj)
    return projects


# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Wings
# ─────────────────────────────────────────────────────────────────────────────


def _ask_wings(mode: str) -> list:
    defaults = DEFAULT_WINGS[mode]
    _hr()
    print(f"""
  Wings are the top-level categories in your memory nook.

  Suggested wings for {mode} mode:
    {", ".join(defaults)}

  Press enter to keep these, or type your own comma-separated list.
""")
    custom = input("  Wings: ").strip()
    if custom:
        return [w.strip() for w in custom.split(",") if w.strip()]
    return defaults


# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Auto-detect from files
# ─────────────────────────────────────────────────────────────────────────────


def _auto_detect(directory: str, known_people: list) -> list:
    """Scan directory for additional entity candidates."""
    known_names = {p["name"].lower() for p in known_people}

    try:
        files = scan_for_detection(directory)
        if not files:
            return []
        detected = detect_entities(files)
        new_people = [
            e
            for e in detected["people"]
            if e["name"].lower() not in known_names and e["confidence"] >= 0.7
        ]
        return new_people
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Step 6: Ambiguity warnings
# ─────────────────────────────────────────────────────────────────────────────


def _warn_ambiguous(people: list) -> list:
    """
    Flag names that are also common English words.
    Returns list of ambiguous names for user awareness.
    """
    from sage_mcp.entity_registry import COMMON_ENGLISH_WORDS

    ambiguous = []
    for p in people:
        if p["name"].lower() in COMMON_ENGLISH_WORDS:
            ambiguous.append(p["name"])
    return ambiguous


# ─────────────────────────────────────────────────────────────────────────────
# Main onboarding flow
# ─────────────────────────────────────────────────────────────────────────────


def run_onboarding(
    directory: str = ".",
    config_dir: Path = None,
    auto_detect: bool = True,
) -> EntityRegistry:
    """
    Run the full onboarding flow.
    Returns the seeded EntityRegistry.
    """
    # Step 1: Mode
    mode = _ask_mode()

    # Step 1b: Embedding model (asked once on first run; choice persists in
    # config.json so future loads don't re-prompt).
    embedding_model = _ask_embedding_model()
    from .config import SageConfig

    SageConfig(config_dir=config_dir).set_embedding_model(embedding_model)

    # Step 2: People
    people, aliases = _ask_people(mode)

    # Step 3: Projects
    projects = _ask_projects(mode)

    # Step 4: Wings (stored in config, not registry — just show user)
    wings = _ask_wings(mode)

    # Step 5: Auto-detect additional people from files
    if auto_detect and _yn("\nScan your files for additional names we might have missed?"):
        directory = _ask("Directory to scan", default=directory)
        detected = _auto_detect(directory, people)
        if detected:
            _hr()
            print(f"\n  Found {len(detected)} additional name candidates:\n")
            for e in detected:
                print(
                    f"    {e['name']:20} confidence={e['confidence']:.0%}  "
                    f"({', '.join(e['signals'][:1])})"
                )
            print()
            if _yn("  Add any of these to your registry?"):
                for e in detected:
                    ans = input(f"    {e['name']} — (p)erson, (s)kip? ").strip().lower()
                    if ans == "p":
                        rel = input(f"    Relationship/role for {e['name']}? ").strip()
                        ctx = (
                            "personal"
                            if mode == "personal"
                            else (
                                "work"
                                if mode == "work"
                                else input("    Context — (p)ersonal or (w)ork? ")
                                .strip()
                                .lower()
                                .replace("w", "work")
                                .replace("p", "personal")
                            )
                        )
                        people.append({"name": e["name"], "relationship": rel, "context": ctx})

    # Step 6: Warn about ambiguous names
    ambiguous = _warn_ambiguous(people)
    if ambiguous:
        _hr()
        print(f"""
  Heads up — these names are also common English words:
    {", ".join(ambiguous)}

  sage will check the context before treating them as person names.
  For example: "I picked up Riley" → person.
               "Have you ever tried" → adverb.
""")

    # Build and save registry
    registry = EntityRegistry.load(config_dir)
    registry.seed(mode=mode, people=people, projects=projects, aliases=aliases)

    # Summary
    _header("Setup Complete")
    print()
    print(f"  {registry.summary()}")
    print(f"\n  Wings: {', '.join(wings)}")
    print(f"\n  Registry saved to: {registry._path}")
    print("\n  Your AI will know your world from the first session.")
    print()

    return registry


# ─────────────────────────────────────────────────────────────────────────────
# Quick setup (non-interactive, for testing)
# ─────────────────────────────────────────────────────────────────────────────


def quick_setup(
    mode: str,
    people: list,
    projects: list = None,
    aliases: dict = None,
    config_dir: Path = None,
    embedding_model: str = None,
) -> EntityRegistry:
    """
    Programmatic setup without interactive prompts.
    Used in tests and benchmark scripts.

    people: list of dicts {"name": str, "relationship": str, "context": str}
    embedding_model: optional ``"minilm"`` or ``"embeddinggemma"``. When set,
        writes the choice to ``config.json`` so subsequent runs pick the
        right EF. When omitted, the config stays untouched and the hard
        default (``"minilm"``) governs.
    """
    registry = EntityRegistry.load(config_dir)
    registry.seed(
        mode=mode,
        people=people,
        projects=projects or [],
        aliases=aliases or {},
    )
    if embedding_model is not None:
        from .config import SageConfig

        SageConfig(config_dir=config_dir).set_embedding_model(embedding_model)
    return registry


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    directory = sys.argv[1] if len(sys.argv) > 1 else "."
    run_onboarding(directory=directory)
