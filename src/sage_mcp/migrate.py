#!/usr/bin/env python3
"""
sage migrate — Recover a nook created with a different ChromaDB version.

Reads documents and metadata directly from the nook's SQLite database
(bypassing ChromaDB's API, which fails on version-mismatched nooks),
then re-imports everything into a fresh nook using the currently installed
ChromaDB version.

Since sage 0.1.0 (chromadb>=1.5.4), chromadb automatically migrates
0.4.1+ databases on first open — no manual migration needed for upgrades.
Use this command only when downgrading chromadb (e.g. rolling back to an
older release) or if automatic migration fails.

Usage:
    sage migrate                          # migrate default nook
    sage migrate --nook /path/to/nook  # migrate specific nook
    sage migrate --dry-run                # show what would be migrated
"""

import errno
import os
import shutil
import sqlite3
import tempfile
import uuid
from collections import defaultdict
from contextlib import closing, contextmanager
from datetime import datetime


def _dir_size_bytes(path: str) -> int:
    """Best-effort recursive size of a directory tree.

    Used by migrate's pre-flight to estimate the backup + temp-nook footprint.
    Symlinks are not followed (avoid double-counting) and unreadable entries
    are skipped silently — the pre-check is advisory.
    """
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            fpath = os.path.join(root, name)
            try:
                total += os.path.getsize(fpath)
            except OSError:
                continue
    return total


@contextmanager
def _migrate_lock(nook_path: str):
    """Hold an exclusive flock for the duration of a migrate run.

    Two concurrent ``sage migrate`` calls against the same nook would
    race on the rename-aside swap and could leave the nook in a half-migrated
    state. The lock is advisory but enforced by every sage entry point; it is
    the cheapest cross-process guard available without a third-party dependency.
    POSIX uses a non-blocking ``fcntl.flock``; Windows uses a non-blocking
    ``msvcrt.locking`` (mirroring the lock sites in ``nook.py``) so ``sage
    migrate`` runs natively on both. The lock file lives next to the nook so it
    is cleaned up by the rename-aside itself if the nook is moved.
    """
    lock_path = nook_path.rstrip(os.sep) + ".migrate.lock"
    fh = open(lock_path, "w")
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            # POSIX raises BlockingIOError (an OSError subclass); Windows
            # LK_NBLCK raises OSError on contention. Either means held.
            raise RuntimeError(
                f"Another sage migrate is running against {nook_path} (lock {lock_path} held)."
            ) from exc
        try:
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        fh.close()
        # Only the lock holder may unlink the path. If we failed to acquire,
        # the live holder owns the file — unlinking it would let a third
        # process create a fresh inode and acquire its own flock, defeating
        # the lock. (Pass 3 Cat 14 F4)
        if locked:
            try:
                os.unlink(lock_path)
            except OSError:
                pass


def _restore_stale_nook(nook_path: str, stale_path: str) -> None:
    """Roll back a failed swap.

    shutil.move() can partially create nook_path before raising, which
    would make a bare os.replace(stale_path, nook_path) fail (dest exists).
    Clear any partial destination first, then restore. Best-effort: if the
    restore itself fails, log both paths so the operator can recover by hand.
    """
    try:
        if os.path.lexists(nook_path):
            shutil.rmtree(nook_path, ignore_errors=True)
        os.replace(stale_path, nook_path)
    except Exception as err:
        print(
            f"  CRITICAL: rollback failed — original nook at {stale_path}, "
            f"partial migration data at {nook_path}. Restore manually. "
            f"({err})"
        )


def extract_drawers_from_sqlite(db_path: str) -> list:
    """Read all drawers directly from ChromaDB's SQLite, bypassing the API.

    Works regardless of which ChromaDB version created the database.
    Returns list of dicts with 'id', 'document', and 'metadata' keys.

    The connection is wrapped in ``contextlib.closing`` so an exception
    during extraction does not leak the SQLite handle. On Windows that
    would leave a file lock on ``chroma.sqlite3`` and prevent the rest
    of the migration from touching the nook directory.
    """
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row

        # Get all embedding IDs and their documents
        rows = conn.execute(
            """
            SELECT e.embedding_id,
                   MAX(CASE WHEN em.key = 'chroma:document' THEN em.string_value END) as document
            FROM embeddings e
            JOIN embedding_metadata em ON em.id = e.id
            GROUP BY e.embedding_id
        """
        ).fetchall()

        drawers = []
        for row in rows:
            embedding_id = row["embedding_id"]
            document = row["document"]
            if not document:
                continue

            # Get metadata for this embedding
            meta_rows = conn.execute(
                """
                SELECT em.key, em.string_value, em.int_value, em.float_value, em.bool_value
                FROM embedding_metadata em
                JOIN embeddings e ON e.id = em.id
                WHERE e.embedding_id = ?
                  AND em.key NOT LIKE 'chroma:%'
            """,
                (embedding_id,),
            ).fetchall()

            metadata = {}
            for mr in meta_rows:
                key = mr["key"]
                if mr["string_value"] is not None:
                    metadata[key] = mr["string_value"]
                elif mr["int_value"] is not None:
                    metadata[key] = mr["int_value"]
                elif mr["float_value"] is not None:
                    metadata[key] = mr["float_value"]
                elif mr["bool_value"] is not None:
                    metadata[key] = bool(mr["bool_value"])

            drawers.append(
                {
                    "id": embedding_id,
                    "document": document,
                    "metadata": metadata,
                }
            )

    return drawers


def detect_chromadb_version(db_path: str) -> str:
    """Detect which ChromaDB version created the database by checking schema."""
    conn = sqlite3.connect(db_path)
    try:
        # 1.x has schema_str column in collections table
        cols = [r[1] for r in conn.execute("PRAGMA table_info(collections)").fetchall()]
        if "schema_str" in cols:
            return "1.x"
        # 0.6.x has embeddings_queue but no schema_str
        tables = [
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        if "embeddings_queue" in tables:
            return "0.6.x"
        return "unknown"
    finally:
        conn.close()


def contains_nook_database(path: str) -> bool:
    """Return True when path looks like a sage ChromaDB directory."""
    return os.path.isfile(os.path.join(path, "chroma.sqlite3"))


def confirm_destructive_action(
    operation_name: str, nook_path: str, assume_yes: bool = False
) -> bool:
    """Require confirmation before destructive nook operations."""
    if assume_yes:
        return True

    print(f"\n  {operation_name} will replace data in: {nook_path}")
    print("  A backup will be created first, then the nook will be rebuilt.")
    try:
        answer = input("  Continue? [y/N]: ").strip().lower()
    except EOFError:
        print("  Aborted. Re-run with --yes to confirm destructive changes.")
        return False

    if answer not in {"y", "yes"}:
        print("  Aborted.")
        return False
    return True


def _result_ids(result) -> list:
    """Return ids from either the backend typed result or raw Chroma dict."""

    if isinstance(result, dict):
        return list(result.get("ids") or [])

    return list(getattr(result, "ids", []) or [])


def collection_write_roundtrip_works(col) -> bool:
    """Return True only if the collection can upsert, read, and delete.

    Some ChromaDB 0.6.x -> 1.5.x migrated collections remain readable while
    writes and deletes silently no-op. A plain ``count()`` probe misses that
    failure mode, so migrate must verify an actual write round-trip before
    deciding that no rebuild is needed.
    """

    probe_id = f"_nook_migrate_probe_{uuid.uuid4().hex}"
    probe_doc = "sage migrate write round-trip probe"
    probe_meta = {
        "wing": "_nook_probe",
        "room": "_nook_probe",
        "source_file": "nook_migrate_probe",
        "chunk_index": 0,
    }

    try:
        col.upsert(
            ids=[probe_id],
            documents=[probe_doc],
            metadatas=[probe_meta],
        )

        after_upsert = col.get(ids=[probe_id], include=[])
        if probe_id not in _result_ids(after_upsert):
            return False

        col.delete(ids=[probe_id])

        after_delete = col.get(ids=[probe_id], include=[])
        if probe_id in _result_ids(after_delete):
            return False

        return True
    except Exception:
        return False


def migrate(nook_path: str, dry_run: bool = False, confirm: bool = False):
    """Migrate a nook to the currently installed ChromaDB version."""
    nook_path = os.path.abspath(os.path.expanduser(nook_path))
    db_path = os.path.join(nook_path, "chroma.sqlite3")

    if not os.path.isdir(nook_path) or not contains_nook_database(nook_path):
        print(f"\n  No nook database found at {db_path}")
        return False

    if dry_run:
        return _migrate_inner(nook_path, db_path, dry_run=True, confirm=confirm)

    try:
        with _migrate_lock(nook_path):
            return _migrate_inner(nook_path, db_path, dry_run=False, confirm=confirm)
    except RuntimeError as exc:
        print(f"\n  {exc}")
        return False


def _migrate_inner(nook_path: str, db_path: str, dry_run: bool, confirm: bool):
    from .backends.chroma import ChromaBackend

    print(f"\n{'=' * 60}")
    print("  sage Migrate")
    print(f"{'=' * 60}\n")
    print(f"  Nook:    {nook_path}")
    print(f"  Database:  {db_path}")
    print(f"  DB size:   {os.path.getsize(db_path) / 1024 / 1024:.1f} MB")

    # Detect version
    source_version = detect_chromadb_version(db_path)
    target_version = ChromaBackend.backend_version()
    print(f"  Source:    ChromaDB {source_version}")
    print(f"  Target:    ChromaDB {target_version}")

    # Try reading and writing with current chromadb first.
    #
    # A plain count() is not enough: some 0.6.x -> 1.5.x migrated collections
    # are readable but silently drop upsert/delete operations. In that state,
    # migrate must rebuild from SQLite instead of returning "No migration needed."
    try:
        col = ChromaBackend().get_collection(nook_path, "nook_drawers")
        count = col.count()

        if collection_write_roundtrip_works(col):
            print(f"\n Nook is already readable and writable by chromadb {target_version}.")
            print(f" {count} drawers found. No migration needed.")
            return True

        print(
            f"\n Nook is readable by chromadb {target_version}, but write/delete verification failed."
        )
        print(" Rebuilding from SQLite to restore native write/delete behavior...")
    except Exception:
        print(f"\n Nook is NOT readable by chromadb {target_version}.")
        print(" Extracting from SQLite directly...")

    # Extract all drawers via raw SQL
    drawers = extract_drawers_from_sqlite(db_path)
    print(f"  Extracted {len(drawers)} drawers from SQLite")

    if not drawers:
        print("  Nothing to migrate.")
        return True

    # Show summary
    wings = defaultdict(lambda: defaultdict(int))
    for d in drawers:
        w = d["metadata"].get("wing", "?")
        r = d["metadata"].get("room", "?")
        wings[w][r] += 1

    print("\n  Summary:")
    for wing, rooms in sorted(wings.items()):
        total = sum(rooms.values())
        print(f"    WING: {wing} ({total} drawers)")
        for room, count in sorted(rooms.items(), key=lambda x: -x[1]):
            print(f"      ROOM: {room:30} {count:5}")

    if dry_run:
        print("\n  DRY RUN — no changes made.")
        print(f"  Would migrate {len(drawers)} drawers.")
        return True

    if not confirm_destructive_action("Migration", nook_path, assume_yes=confirm):
        return False

    # Disk-space pre-check. The backup is a full copy of the nook and the
    # temp build directory holds a second full copy until the swap. Refuse
    # early if either destination can't fit twice the nook footprint plus a
    # 10% margin — better to abort before touching anything than to fail mid-
    # backup or mid-swap and leave the operator to clean up by hand.
    nook_size = _dir_size_bytes(nook_path)
    required = int(nook_size * 2.2)
    parent_dir = os.path.dirname(nook_path) or "."
    temp_root = tempfile.gettempdir()
    for label, target in (("nook parent", parent_dir), ("temp root", temp_root)):
        try:
            free = shutil.disk_usage(target).free
        except OSError:
            continue
        if free < required:
            print(
                f"\n  Insufficient disk space at {label} ({target}): need "
                f"~{required / 1024 / 1024:.0f} MB free, have "
                f"{free / 1024 / 1024:.0f} MB. Refusing to migrate."
            )
            return False

    # Backup the old nook. Wrap in try/except BaseException so a
    # Ctrl+C mid-copytree leaves no half-populated backup directory.
    # (Pass 3 Cat 14 F5)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{nook_path}.pre-migrate.{timestamp}"
    print(f"\n  Backing up to {backup_path}...")
    try:
        shutil.copytree(nook_path, backup_path)
    except BaseException:
        if os.path.exists(backup_path):
            shutil.rmtree(backup_path, ignore_errors=True)
        raise

    # Build fresh nook in a temp directory (avoids chromadb reading old state).
    # Wrap the whole import-and-swap dance in try/finally so the temp dir is
    # cleaned up if any of the chromadb writes, the verify count, or the
    # rename fails — without try/finally a crashed migration leaves a partial
    # nook dir under the system temp root that the user has to find by hand.
    temp_nook = tempfile.mkdtemp(prefix="nook_migrate_")
    stale_path = nook_path + ".old"
    try:
        print(f"  Creating fresh nook in {temp_nook}...")
        fresh_backend = ChromaBackend()
        col = fresh_backend.get_collection(temp_nook, "nook_drawers", create=True)

        # Re-import in batches
        batch_size = 500
        imported = 0
        for i in range(0, len(drawers), batch_size):
            batch = drawers[i : i + batch_size]
            col.add(
                ids=[d["id"] for d in batch],
                documents=[d["document"] for d in batch],
                metadatas=[d["metadata"] for d in batch],
            )
            imported += len(batch)
            print(f"  Imported {imported}/{len(drawers)} drawers...")

        # Verify before swapping
        final_count = col.count()
        del col
        del fresh_backend

        # Swap: rename old nook aside, then move new one into place.
        # This avoids a window where both old and new are missing.
        # Catch BaseException (KeyboardInterrupt + SystemExit included) so a
        # Ctrl+C between the rename-aside and the move-into-place restores the
        # original instead of leaving nook_path empty. (Pass 3 Cat 13 F1/F2)
        print("  Swapping old nook for migrated version...")
        if os.path.exists(stale_path):
            shutil.rmtree(stale_path)
        os.replace(nook_path, stale_path)
        try:
            os.replace(temp_nook, nook_path)
        except OSError as e:
            # EXDEV = temp lives on a different filesystem; fall back to copy+delete.
            # Anything else is a real error — don't mask it with shutil.move.
            if getattr(e, "errno", None) != errno.EXDEV:
                _restore_stale_nook(nook_path, stale_path)
                raise
            try:
                shutil.move(temp_nook, nook_path)
            except BaseException:
                _restore_stale_nook(nook_path, stale_path)
                raise
        except BaseException:
            _restore_stale_nook(nook_path, stale_path)
            raise
        shutil.rmtree(stale_path, ignore_errors=True)
    finally:
        # On the happy path os.replace/shutil.move consumed temp_nook, so
        # the directory no longer exists at the temp location — the existence
        # guard makes this a no-op.
        #
        # On a failure path (BaseException incl. KeyboardInterrupt) the
        # original nook has already been restored at `nook_path` via
        # _restore_stale_nook, and the partially-built migrated copy at
        # `temp_nook` is no longer reachable as the live nook. We wipe
        # it to avoid orphaning a multi-GB directory under the system temp
        # root — the operator's recovery path is the `.pre-migrate.<ts>`
        # backup, not the abandoned temp build. (Pass 4 Cat 22 F3)
        if os.path.exists(temp_nook):
            shutil.rmtree(temp_nook, ignore_errors=True)

    print("\n  Migration complete.")
    print(f"  Drawers migrated: {final_count}")
    print(f"  Backup at: {backup_path}")

    if final_count != len(drawers):
        print(f"  WARNING: Expected {len(drawers)}, got {final_count}")

    print(f"\n{'=' * 60}\n")
    return True
