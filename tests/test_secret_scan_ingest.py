"""Tests for WI-8: secret-scan gate on the miner / convo_miner ingest paths.

Validates three contracts from ADR-0042 and the WI-8 scope-gap closure:

1. A project file containing a fake credential token is stored with
   [REDACTED] in place of the token — the raw token never reaches stored
   drawers (miner.process_file gate).

2. A .env file is never mined at all because .sageignore excludes it
   (ignore-file defense-in-depth, independent of the scrub gate).

3. A conversation transcript containing a fake credential token is stored
   with [REDACTED] in place of the token — the raw token never reaches
   stored drawers (convo_miner._mine_convos_impl gate).

4. A file containing a 40-char git SHA is mined verbatim — the high-confidence
   scrub must NOT over-redact legitimate dev content (ADR-0042 I#5).

The tests use only fixtures and temp directories — no live/real data is touched.
"""

import shutil
import tempfile
from pathlib import Path

import chromadb
import yaml

from sage_mcp.miner import mine, process_file, scan_project

# ---------------------------------------------------------------------------
# Shared fake tokens — deliberately invalid / non-functional values.
# Using the same prefix shapes as real credentials so the high-confidence
# patterns fire, but the suffixes are obviously fake test data.
# ---------------------------------------------------------------------------
_FAKE_GHP = "ghp_FAKEtestTOKENforWI8abcdefghijklmno"  # GitHub PAT pattern
_FAKE_SK_ANT = "sk-ant-api01-FAKEtestTOKENforWI8xxxxxxxxxx"  # Anthropic key
_GIT_SHA_40 = "a3f9c2e1d0b7f6e5d4c3b2a1908070605040302"  # real-looking 40-char git SHA


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _all_documents(nook_path: str) -> list[str]:
    """Retrieve every stored document string from the drawers collection."""
    client = chromadb.PersistentClient(path=nook_path)
    try:
        col = client.get_collection("nook_drawers")
    except Exception:
        return []
    result = col.get(include=["documents"])
    return result.get("documents") or []


# ---------------------------------------------------------------------------
# Test 1: project file with a credential token → stored [REDACTED]
# ---------------------------------------------------------------------------


def test_miner_scrubs_credential_before_store():
    """A project file containing a fake GitHub PAT must be stored redacted.

    The raw token must not appear in any stored drawer; [REDACTED] must
    appear instead. The scrub gate is in miner.process_file (WI-8).
    """
    tmpdir = tempfile.mkdtemp(prefix="sage_wi8_")
    try:
        project_root = Path(tmpdir) / "project"
        project_root.mkdir()
        nook = Path(tmpdir) / "nook"
        nook.mkdir()

        sensitive_content = f"# Config\nGITHUB_TOKEN={_FAKE_GHP}\nsome_other_value=hello\n" * 30
        _write(project_root / "config.md", sensitive_content)
        _write(
            project_root / "sage.yaml",
            yaml.dump({"wing": "test", "rooms": [{"name": "general"}]}),
        )

        mine(str(project_root), str(nook))

        docs = _all_documents(str(nook))
        assert docs, "Expected at least one drawer to be stored"

        joined = "\n".join(docs)
        assert _FAKE_GHP not in joined, (
            f"Raw credential token found in stored drawers — WI-8 scrub gate failed.\n"
            f"Token: {_FAKE_GHP!r}"
        )
        assert "[REDACTED]" in joined, (
            "Expected [REDACTED] to appear in stored drawers after scrub gate."
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 2: .env file → never mined (.sageignore exclusion)
# ---------------------------------------------------------------------------


def test_dotenv_file_not_mined():
    """.env files must be excluded from mining via .sageignore.

    Defense-in-depth: the ignore-file excludes the file before the scrub
    gate even runs. This verifies the new secret-file patterns in
    .sageignore take effect through scan_project's nookignore path.
    """
    tmpdir = tempfile.mkdtemp(prefix="sage_wi8_")
    try:
        project_root = Path(tmpdir) / "project"
        project_root.mkdir()

        # Write a .env file with a fake token
        env_content = f"ANTHROPIC_API_KEY={_FAKE_SK_ANT}\nSOME_VAR=value\n"
        _write(project_root / ".env", env_content)

        # Also write a .env.local variant
        _write(project_root / ".env.local", env_content)

        # Write a legitimate .md file so the scan returns something on success
        _write(project_root / "README.md", "# Project\nThis is the readme.\n" * 10)

        # Place the repo's .sageignore at the project root so scan_project
        # picks it up. Copy the patterns from the real .sageignore —
        # specifically the .env / .env.* lines added by WI-8.
        repo_ignore = Path(__file__).parent.parent / ".sageignore"
        ignore_dest = project_root / ".sageignore"
        if repo_ignore.is_file():
            ignore_dest.write_text(repo_ignore.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            # Fallback: write the minimum patterns we need
            ignore_dest.write_text(".env\n.env.*\n", encoding="utf-8")

        files = scan_project(str(project_root))
        file_names = [f.name for f in files]

        assert ".env" not in file_names, (
            ".env was included in scan_project results — .sageignore "
            "exclusion not working for .env files (WI-8 defense-in-depth)."
        )
        assert ".env.local" not in file_names, (
            ".env.local was included in scan_project results — .env.* pattern "
            "in .sageignore not working (WI-8 defense-in-depth)."
        )
        # README.md should still be present
        assert "README.md" in file_names, (
            "README.md was unexpectedly excluded — over-exclusion in .sageignore."
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 3: conversation transcript with a credential token → stored [REDACTED]
# ---------------------------------------------------------------------------


def test_convo_miner_scrubs_credential_before_store():
    """A conversation transcript containing a fake Anthropic key must be
    stored redacted. The raw token must not appear in any stored drawer.

    The scrub gate is in convo_miner._mine_convos_impl (WI-8).
    """
    from sage_mcp.convo_miner import mine_convos

    tmpdir = tempfile.mkdtemp(prefix="sage_wi8_")
    try:
        convo_dir = Path(tmpdir) / "convos"
        convo_dir.mkdir()
        nook = Path(tmpdir) / "nook"
        nook.mkdir()

        # Simulate a chat transcript that accidentally contains an API key.
        # Use quote-style format so chunk_exchanges fires.
        transcript_content = (
            "> How do I call the API?\n"
            f"You can set ANTHROPIC_API_KEY={_FAKE_SK_ANT} in your environment.\n"
            "\n"
            "> What are the rate limits?\n"
            "Rate limits depend on your plan tier.\n"
        ) * 10

        _write(convo_dir / "session.txt", transcript_content)

        mine_convos(str(convo_dir), str(nook), wing="test_convos")

        docs = _all_documents(str(nook))
        assert docs, "Expected at least one drawer to be stored"

        joined = "\n".join(docs)
        assert _FAKE_SK_ANT not in joined, (
            f"Raw credential token found in stored convo drawers — WI-8 scrub "
            f"gate failed in convo_miner.\nToken: {_FAKE_SK_ANT!r}"
        )
        assert "[REDACTED]" in joined, (
            "Expected [REDACTED] to appear in stored convo drawers after scrub gate."
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 4: git SHA in a mined file → survives verbatim (no over-redaction)
# ---------------------------------------------------------------------------


def test_miner_git_sha_survives_high_confidence_scrub():
    """A file containing only a 40-char git SHA and surrounding text must be
    stored verbatim — the high-confidence scrub must NOT redact git SHAs
    (ADR-0042 I#5: legitimate dev content survives the write-boundary gate).
    """
    tmpdir = tempfile.mkdtemp(prefix="sage_wi8_")
    try:
        project_root = Path(tmpdir) / "project"
        project_root.mkdir()
        nook = Path(tmpdir) / "nook"
        nook.mkdir()

        sha_content = (
            f"# Changelog\n\nCommit {_GIT_SHA_40} fixes the race condition.\n"
            "See also the related PR for context on the fix approach.\n"
        ) * 30
        _write(project_root / "CHANGELOG.md", sha_content)
        _write(
            project_root / "sage.yaml",
            yaml.dump({"wing": "test", "rooms": [{"name": "general"}]}),
        )

        mine(str(project_root), str(nook))

        docs = _all_documents(str(nook))
        assert docs, "Expected at least one drawer to be stored"

        joined = "\n".join(docs)
        assert _GIT_SHA_40 in joined, (
            f"Git SHA-1 was incorrectly redacted by the write-boundary scrub — "
            f"ADR-0042 I#5 violation (high-confidence tier must not redact hex-only strings).\n"
            f"SHA: {_GIT_SHA_40!r}"
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Unit-level: process_file scrubs content before returning drawer count
# ---------------------------------------------------------------------------


def test_process_file_scrubs_in_place(tmp_path):
    """Unit test: process_file stores scrubbed content.

    Uses a real ChromaDB collection in a temp nook to verify the content
    reaching the collection has been scrubbed without requiring a full mine().
    """
    nook = tmp_path / "nook"
    nook.mkdir()
    project_root = tmp_path / "proj"
    project_root.mkdir()

    token_content = f"API_KEY={_FAKE_GHP}\nSome legitimate content about the project.\n" * 20
    src = project_root / "notes.md"
    src.write_text(token_content, encoding="utf-8")

    client = chromadb.PersistentClient(path=str(nook))
    col = client.get_or_create_collection("nook_drawers", metadata={"hnsw:space": "cosine"})

    from sage_mcp.nook import get_closets_collection

    closets = get_closets_collection(str(nook))

    n, room, skip = process_file(
        filepath=src,
        project_path=project_root,
        collection=col,
        wing="test",
        rooms=[{"name": "general", "description": "General"}],
        agent="test-agent",
        dry_run=False,
        closets_col=closets,
    )

    assert n > 0, "Expected drawers to be stored"
    stored = col.get(include=["documents"])
    joined = "\n".join(stored.get("documents") or [])

    assert _FAKE_GHP not in joined, (
        f"Raw token present in stored drawer — process_file scrub gate did not fire.\n"
        f"Token: {_FAKE_GHP!r}"
    )
    assert "[REDACTED]" in joined, (
        "Expected [REDACTED] in stored drawer content after process_file scrub."
    )


# ---------------------------------------------------------------------------
# Tests for WI-8 Round-2: centralized chokepoint + ignore wiring (ADR-0042)
# ---------------------------------------------------------------------------

# Fake DOCX byte payload: a minimal .zip with a word/document.xml containing
# a fake token. MarkItDown reads this format — we mock extract_text to avoid
# the optional runtime dep and focus the test on the scrub gate.

_FAKE_AKIA = "AKIAIOSFODNN7FAKETEST"  # exactly AKIA + 16 uppercase chars (total 20), matches AKIA[0-9A-Z]{16}


# ---------------------------------------------------------------------------
# Test 5: format_miner ingest of a file with a fake token → stored [REDACTED]
# ---------------------------------------------------------------------------


def test_format_miner_scrubs_credential_before_store(tmp_path, monkeypatch):
    """format_miner ingest of a file containing a fake AWS key must store
    [REDACTED]. Validates the centralized backend chokepoint (FIX 1 / ADR-0042).

    extract_text is monkeypatched to return a fake-token string so the test
    does not require the optional MarkItDown runtime dependency.
    """
    import yaml
    from sage_mcp.format_miner import ExtractionStatus, mine_formats

    nook = tmp_path / "nook"
    nook.mkdir()
    format_dir = tmp_path / "docs"
    format_dir.mkdir()

    # Create a fake .pdf file (extension accepted; content mocked below)
    fake_doc = format_dir / "report.pdf"
    fake_doc.write_bytes(b"%PDF fake content")

    # Write a sage.yaml so mine_formats can load_config
    (format_dir / "sage.yaml").write_text(
        yaml.dump({"wing": "testdocs", "rooms": [{"name": "documents"}]}),
        encoding="utf-8",
    )

    # Register the wing so require_registered_wing doesn't block
    import sage_mcp.extensions.wing_registry as _wr

    monkeypatch.setattr(_wr, "require_registered_wing", lambda wing: None)

    # Patch extract_text to return content with a fake credential
    token_text = (f"## Report\n\nIntegration key: {_FAKE_AKIA}\n\nSome legitimate analysis.\n") * 30

    import sage_mcp.format_miner as _fm

    monkeypatch.setattr(
        _fm,
        "extract_text",
        lambda p: (token_text, ExtractionStatus.OK),
    )

    mine_formats(str(format_dir), str(nook), wing="testdocs")

    docs = _all_documents(str(nook))
    assert docs, "Expected at least one drawer from format_miner"

    joined = "\n".join(docs)
    assert _FAKE_AKIA not in joined, (
        f"Raw AWS key found in stored format_miner drawer — centralized scrub "
        f"chokepoint (FIX 1) did not fire.\nToken: {_FAKE_AKIA!r}"
    )
    assert "[REDACTED]" in joined, (
        "Expected [REDACTED] in stored format_miner drawer content after chokepoint scrub."
    )


# ---------------------------------------------------------------------------
# Test 6: diary_ingest of content with a fake token → stored [REDACTED]
# ---------------------------------------------------------------------------


def test_diary_ingest_scrubs_credential_before_store(tmp_path):
    """diary_ingest of a daily-summary file containing a fake Anthropic key must
    store [REDACTED]. Validates the centralized backend chokepoint (FIX 1).
    """
    from sage_mcp.diary_ingest import ingest_diaries

    nook = tmp_path / "nook"
    nook.mkdir()
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()

    token_text = (
        "## Morning check-in\n\n"
        f"Rotated API key: {_FAKE_SK_ANT}\n\n"
        "Reviewed the sprint backlog and closed two issues.\n\n"
        "## Afternoon\n\nContinued work on the search layer.\n"
    )
    (diary_dir / "2026-05-01.md").write_text(token_text, encoding="utf-8")

    ingest_diaries(str(diary_dir), str(nook), wing="diary", force=True)

    docs = _all_documents(str(nook))
    assert docs, "Expected at least one drawer from diary_ingest"

    joined = "\n".join(docs)
    assert _FAKE_SK_ANT not in joined, (
        f"Raw Anthropic key found in stored diary drawer — centralized scrub "
        f"chokepoint (FIX 1) did not fire.\nToken: {_FAKE_SK_ANT!r}"
    )
    assert "[REDACTED]" in joined, (
        "Expected [REDACTED] in stored diary drawer content after chokepoint scrub."
    )


# ---------------------------------------------------------------------------
# Test 7a: scan_formats skips a .env file when .sageignore is present
# ---------------------------------------------------------------------------


def test_scan_formats_skips_dotenv_via_nookignore(tmp_path):
    """scan_formats must skip .env files when .sageignore excludes them.

    Verifies FIX 2: .sageignore is now consulted by scan_formats.
    This test FAILS against pre-fix code (ignore not wired in scan_formats).
    """
    from sage_mcp.format_miner import scan_formats

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    # Write a .pdf (supported format) and a .env (should be excluded)
    (docs_dir / "notes.pdf").write_bytes(b"%PDF fake")
    (docs_dir / ".env").write_text("SECRET=value\n", encoding="utf-8")
    # Also a .pem (PEM private key file — should be excluded)
    (docs_dir / "privkey.pem").write_text("-----BEGIN PRIVATE KEY-----\nfake\n", encoding="utf-8")

    # Place .sageignore at docs_dir root with the WI-8 secret-file patterns
    (docs_dir / ".sageignore").write_text(".env\n.env.*\n*.pem\n", encoding="utf-8")

    found = scan_formats(docs_dir)
    names = [p.name for p in found]

    assert ".env" not in names, (
        ".env was returned by scan_formats — .sageignore ignore wiring "
        "(FIX 2) not working for scan_formats."
    )
    assert "privkey.pem" not in names, (
        "privkey.pem was returned by scan_formats — *.pem pattern in "
        ".sageignore not working for scan_formats (FIX 2)."
    )
    assert "notes.pdf" in names, (
        "notes.pdf was unexpectedly excluded — over-exclusion in scan_formats."
    )


# ---------------------------------------------------------------------------
# Test 7b: scan_convos skips a .env.txt file when .sageignore is present
# ---------------------------------------------------------------------------


def test_scan_convos_skips_dotenv_via_nookignore(tmp_path):
    """scan_convos must skip files matched by .sageignore.

    Verifies FIX 2: .sageignore is now consulted by scan_convos.
    This test FAILS against pre-fix code (ignore not wired in scan_convos).
    """
    from sage_mcp.convo_miner import scan_convos

    convo_dir = tmp_path / "convos"
    convo_dir.mkdir()

    # .txt is in CONVO_EXTENSIONS — write one normal and one secret-ish file
    (convo_dir / "chat.txt").write_text("Q: Hello\nA: Hi there.\n" * 10, encoding="utf-8")
    (convo_dir / "secrets.txt").write_text("ANTHROPIC_KEY=sk-ant-fake\n", encoding="utf-8")

    # Place .sageignore at convo_dir root
    (convo_dir / ".sageignore").write_text("secrets.txt\n", encoding="utf-8")

    found = scan_convos(str(convo_dir))
    names = [p.name for p in found]

    assert "secrets.txt" not in names, (
        "secrets.txt was returned by scan_convos — .sageignore ignore "
        "wiring (FIX 2) not working for scan_convos."
    )
    assert "chat.txt" in names, (
        "chat.txt was unexpectedly excluded — over-exclusion in scan_convos."
    )


# ---------------------------------------------------------------------------
# Test 8: ChromaCollection.upsert with a fake token → stored [REDACTED]
# (chokepoint unit test — guards the backend method directly)
# ---------------------------------------------------------------------------


def test_chroma_collection_upsert_scrubs_at_chokepoint(tmp_path):
    """Direct call to ChromaCollection.upsert with a tokened document must
    store [REDACTED]. Guards the centralized chokepoint (FIX 1) directly —
    if this fails, ALL ingest paths are unprotected.

    This test FAILS against pre-fix code (no scrub in ChromaCollection).
    """
    import chromadb

    nook = tmp_path / "nook"
    nook.mkdir()

    client = chromadb.PersistentClient(path=str(nook))
    raw_col = client.get_or_create_collection("nook_drawers", metadata={"hnsw:space": "cosine"})

    from sage_mcp.backends.chroma import ChromaCollection

    col = ChromaCollection(raw_col)

    token_doc = f"Deployment note: GITHUB_TOKEN={_FAKE_GHP} set in CI.\n" * 5
    col.upsert(
        documents=[token_doc],
        ids=["test_chokepoint_001"],
        metadatas=[{"wing": "test", "room": "general"}],
    )

    stored = raw_col.get(ids=["test_chokepoint_001"], include=["documents"])
    stored_docs = stored.get("documents") or []
    assert stored_docs, "Expected document to be stored"

    assert _FAKE_GHP not in stored_docs[0], (
        f"Raw GitHub PAT found in stored document — ChromaCollection.upsert "
        f"chokepoint scrub (FIX 1) did not fire.\nToken: {_FAKE_GHP!r}"
    )
    assert "[REDACTED]" in stored_docs[0], (
        "Expected [REDACTED] in document stored via ChromaCollection.upsert."
    )


# ---------------------------------------------------------------------------
# Test 9: ChromaCollection.add with a fake token → stored [REDACTED]
# (parallel chokepoint unit test — guards the add() method directly)
# ---------------------------------------------------------------------------


def test_chroma_collection_add_scrubs_at_chokepoint(tmp_path):
    """Direct call to ChromaCollection.add with a tokened document must
    store [REDACTED]. Guards the centralized chokepoint (ADR-0042) for the
    add() path — parallel to test 8 which covers upsert().

    This test FAILS against pre-fix code (no scrub in ChromaCollection.add).
    """
    import chromadb

    nook = tmp_path / "nook"
    nook.mkdir()

    client = chromadb.PersistentClient(path=str(nook))
    raw_col = client.get_or_create_collection("nook_drawers", metadata={"hnsw:space": "cosine"})

    from sage_mcp.backends.chroma import ChromaCollection

    col = ChromaCollection(raw_col)

    token_doc = f"Config note: ANTHROPIC_API_KEY={_FAKE_SK_ANT} loaded at startup.\n" * 5
    col.add(
        documents=[token_doc],
        ids=["test_add_chokepoint_001"],
        metadatas=[{"wing": "test", "room": "general"}],
    )

    stored = raw_col.get(ids=["test_add_chokepoint_001"], include=["documents"])
    stored_docs = stored.get("documents") or []
    assert stored_docs, "Expected document to be stored"

    assert _FAKE_SK_ANT not in stored_docs[0], (
        f"Raw Anthropic key found in stored document — ChromaCollection.add "
        f"chokepoint scrub (ADR-0042) did not fire.\nToken: {_FAKE_SK_ANT!r}"
    )
    assert "[REDACTED]" in stored_docs[0], (
        "Expected [REDACTED] in document stored via ChromaCollection.add."
    )


# ---------------------------------------------------------------------------
# Test 10: ChromaCollection.update with a fake token → stored [REDACTED]
# (chokepoint unit test — guards the update() method directly)
# ---------------------------------------------------------------------------


def test_chroma_collection_update_scrubs_at_chokepoint(tmp_path):
    """Direct call to ChromaCollection.update with a tokened document must
    store [REDACTED]. Guards the centralized chokepoint (ADR-0042) for the
    update() path — the chokepoint contract now covers add / upsert / update.

    This test FAILS against pre-fix code (update() did not call _scrub_documents).
    """
    import chromadb

    nook = tmp_path / "nook"
    nook.mkdir()

    client = chromadb.PersistentClient(path=str(nook))
    raw_col = client.get_or_create_collection("nook_drawers", metadata={"hnsw:space": "cosine"})

    from sage_mcp.backends.chroma import ChromaCollection

    col = ChromaCollection(raw_col)

    # First add a clean document so we have something to update.
    col.add(
        documents=["Initial content without secrets."],
        ids=["test_update_chokepoint_001"],
        metadatas=[{"wing": "test", "room": "general"}],
    )

    # Now update the document with a token-bearing replacement.
    token_doc = f"Updated note: AWS_SECRET={_FAKE_AKIA} rotated today.\n" * 5
    col.update(
        ids=["test_update_chokepoint_001"],
        documents=[token_doc],
    )

    stored = raw_col.get(ids=["test_update_chokepoint_001"], include=["documents"])
    stored_docs = stored.get("documents") or []
    assert stored_docs, "Expected document to be present after update"

    assert _FAKE_AKIA not in stored_docs[0], (
        f"Raw AWS key found in stored document — ChromaCollection.update "
        f"chokepoint scrub (ADR-0042) did not fire.\nToken: {_FAKE_AKIA!r}"
    )
    assert "[REDACTED]" in stored_docs[0], (
        "Expected [REDACTED] in document stored via ChromaCollection.update."
    )
