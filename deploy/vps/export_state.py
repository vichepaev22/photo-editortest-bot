"""Export a stopped bot's private state without modifying its source files.

The caller must stop the local poller first and choose a private output directory
(including its Windows ACL). No credentials or runtime files enter the archive.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

INPUT_TTL = 86400


class ExportError(ValueError):
    """A sanitized failure code, safe to display without private paths or data."""


def _no_links(path):
    for part in reversed((path, *path.parents)):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ExportError("unsafe_filesystem_path")


def _checked_path(value, *, directory=False):
    path = Path(value).expanduser()
    if ".." in path.parts:
        raise ExportError("unsafe_filesystem_path")
    path = path.absolute()
    _no_links(path)
    resolved = path.resolve(strict=True)
    if resolved != path or (directory and not path.is_dir()):
        raise ExportError("unsafe_filesystem_path")
    return resolved


def _owner(value):
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        if str(int(value)) == value:
            return value
    raise ExportError("invalid_media_owner")


def _filename(value):
    if (not isinstance(value, str) or value in {"", ".", ".."}
            or any(character in value for character in "/\\:\0")):
        raise ExportError("unsafe_media_reference")
    return value


def _reference(value, owner):
    if not isinstance(value, str):
        raise ExportError("unsafe_media_reference")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if len(parts) != 2 or parts[0] != owner:
        raise ExportError("unsafe_media_reference")
    _filename(parts[1])
    return normalized


def _sql_name(value):
    return '"' + value.replace('"', '""') + '"'


def _schema(connection):
    return connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
    ).fetchall()


def _table_rows(connection, *, normalize_results=False):
    tables = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
    ).fetchall()
    result = {}
    for (table,) in tables:
        cursor = connection.execute(f"SELECT * FROM {_sql_name(table)}")
        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
        if table == "jobs" and normalize_results:
            owner_index, result_index = columns.index("user_id"), columns.index("result")
            normalized_rows = []
            for row in rows:
                values = list(row)
                if values[result_index] is not None:
                    values[result_index] = _reference(values[result_index], _owner(values[owner_index]))
                normalized_rows.append(tuple(values))
            rows = normalized_rows
        result[table] = (columns, Counter(rows))
    return result


def _state_digest(tables):
    digest = hashlib.sha256()
    for table, (columns, rows) in sorted(tables.items()):
        digest.update(json.dumps([table, columns], ensure_ascii=False).encode("utf-8"))
        row_digests = []
        for row, count in rows.items():
            values = []
            for value in row:
                if isinstance(value, bytes):
                    values.append(["bytes", value.hex()])
                elif isinstance(value, float):
                    values.append(["float", value.hex()])
                else:
                    values.append([type(value).__name__, value])
            encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            row_digests.extend([hashlib.sha256(encoded).digest()] * count)
        for row_digest in sorted(row_digests):
            digest.update(row_digest)
    return digest.hexdigest()


def _integrity(connection):
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ExportError("database_integrity_failed")


def _copy_regular(source, target):
    _no_links(source)
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ExportError("unsafe_media_file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(source, flags)
    with os.fdopen(descriptor, "rb") as original:
        opened = os.fstat(original.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ExportError("source_changed")
        with target.open("xb") as copied:
            shutil.copyfileobj(original, copied)
        after = os.fstat(original.fileno())
    _no_links(source)
    current = source.lstat()
    def signature(item):
        return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns

    if signature(before) != signature(after) or signature(before) != signature(current):
        raise ExportError("source_changed")
    os.chmod(target, 0o600)
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    return before


def _copy_media(source, target):
    target.mkdir(mode=0o700)
    files = {}
    if not source.exists():
        _no_links(source)
        return files
    _checked_path(source, directory=True)
    for directory in sorted(source.iterdir()):
        _no_links(directory)
        if not directory.is_dir():
            raise ExportError("unsafe_media_layout")
        owner = _owner(directory.name)
        destination = target / owner
        destination.mkdir(mode=0o700)
        for file in sorted(directory.iterdir()):
            name = _filename(file.name)
            relative = f"{owner}/{name}"
            copied_stat = _copy_regular(file, destination / name)
            files[relative] = copied_stat
    return files


def _count_reference(reference, files, report, now):
    if reference not in files:
        report["missing_references"] += 1
    elif now - files[reference].st_mtime > INPUT_TTL:
        report["expired_references"] += 1


def _inputs_span(text):
    """Locate only the top-level inputs token; leave every other JSON byte intact."""
    decoder = json.JSONDecoder()
    whitespace = " \r\n\t"

    def skip(index):
        while index < len(text) and text[index] in whitespace:
            index += 1
        return index

    index = skip(0) + 1  # The separately parsed document is an object.
    found, keys = None, set()
    while text[skip(index)] != "}":
        key, index = decoder.raw_decode(text, skip(index))
        if key in keys:
            raise ExportError("invalid_media_json")
        keys.add(key)
        index = skip(index) + 1  # Colon, already checked by json.loads.
        start = skip(index)
        _, end = decoder.raw_decode(text, start)
        if key == "inputs":
            found = (start, end)
        index = skip(end)
        if text[index] == "}":
            break
        index += 1
    if found is None:
        raise ExportError("invalid_media_json")
    return found


def _normalize_payload(path, owner, files, report, now):
    original = path.read_bytes()
    try:
        text = original.decode("utf-8")
        record = json.loads(text)
        if not isinstance(record, dict) or not isinstance(record.get("inputs"), list):
            raise ExportError("invalid_media_json")
        start, end = _inputs_span(text)
        inputs = [_reference(value, owner) for value in record["inputs"]]
    except (UnicodeError, json.JSONDecodeError, IndexError, RecursionError):
        raise ExportError("invalid_media_json") from None
    for reference in inputs:
        _count_reference(reference, files, report, now)
    changed = sum(old != new for old, new in zip(record["inputs"], inputs))
    if changed:
        saved_stat = path.stat()
        replacement = json.dumps(inputs, ensure_ascii=False)
        normalized_text = text[:start] + replacement + text[end:]
        expected = dict(record, inputs=inputs)
        if json.loads(normalized_text) != expected:
            raise ExportError("payload_preservation_failed")
        path.write_bytes(normalized_text.encode("utf-8"))
        os.utime(path, ns=(saved_stat.st_atime_ns, saved_stat.st_mtime_ns))
        report["normalized_json_files"] += 1
        report["normalized_input_references"] += changed


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_archive(staging, output):
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(descriptor, "wb") as file:
            with tarfile.open(fileobj=file, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
                def private_metadata(info):
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mode = 0o700 if info.isdir() else 0o600
                    mtime_ns = (staging / info.name).stat().st_mtime_ns
                    seconds, nanos = divmod(mtime_ns, 1_000_000_000)
                    info.pax_headers["mtime"] = f"{seconds}.{nanos:09d}"
                    return info

                archive.add(staging / "db.sqlite3", arcname="db.sqlite3", filter=private_metadata)
                archive.add(staging / "media", arcname="media", filter=private_metadata)
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def export_state(source_dir, archive_path):
    """Return only aggregate counts and digests for a private tar.gz snapshot."""
    try:
        source = _checked_path(source_dir, directory=True)
        requested_output = Path(archive_path).expanduser()
        if ".." in requested_output.parts:
            raise ExportError("unsafe_filesystem_path")
        requested_output = requested_output.absolute()
        output_parent = _checked_path(requested_output.parent, directory=True)
        output = output_parent / _filename(requested_output.name)
        _no_links(output)
        if output.is_relative_to(source):
            raise ExportError("output_inside_source")
        if output.exists():
            raise ExportError("archive_exists")
        database = _checked_path(source / "db.sqlite3")
        if not stat.S_ISREG(database.stat().st_mode):
            raise ExportError("unsafe_database_file")
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(database) + suffix)
            _no_links(sidecar)
            if sidecar.exists() and not stat.S_ISREG(sidecar.stat().st_mode):
                raise ExportError("unsafe_database_file")
        report = {
            "database_tables": 0, "database_rows": 0, "users": 0, "jobs": 0,
            "media_files": 0, "media_bytes": 0, "normalized_job_results": 0,
            "normalized_json_files": 0, "normalized_input_references": 0,
            "missing_references": 0, "expired_references": 0, "missing_job_payloads": 0,
        }
        with tempfile.TemporaryDirectory(prefix=".state-export-", dir=output_parent) as temp:
            staging = Path(temp)
            os.chmod(staging, 0o700)
            snapshot_path = staging / "db.sqlite3"
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as original:
                original.execute("BEGIN")
                _integrity(original)
                if original.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()[0]:
                    raise ExportError("active_jobs")
                original_schema = _schema(original)
                expected_tables = _table_rows(original, normalize_results=True)
                with closing(sqlite3.connect(snapshot_path)) as snapshot:
                    original.backup(snapshot)
                    snapshot.execute("PRAGMA journal_mode=DELETE")
                    jobs = snapshot.execute("SELECT id,user_id,result FROM jobs").fetchall()
                    job_owners = {}
                    for job_id, user_id, result in jobs:
                        _filename(job_id)
                        owner = _owner(user_id)
                        job_owners[job_id] = owner
                        if result is not None:
                            normalized = _reference(result, owner)
                            if normalized != result:
                                snapshot.execute("UPDATE jobs SET result=? WHERE id=?", (normalized, job_id))
                                report["normalized_job_results"] += 1
                    snapshot.commit()
                    _integrity(snapshot)
                    actual_tables = _table_rows(snapshot)
                    if original_schema != _schema(snapshot) or expected_tables != actual_tables:
                        raise ExportError("database_preservation_failed")
                    report["database_tables"] = len(actual_tables)
                    report["database_rows"] = sum(sum(rows.values()) for _, rows in actual_tables.values())
                    report["users"] = snapshot.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                    report["jobs"] = len(jobs)
                    report["state_sha256"] = _state_digest(actual_tables)
                    for column in ("balance", "reserved", "spent", "consent", "trial_granted",
                                   "trial_used", "trial_reserved"):
                        report[f"users_{column}_total"] = snapshot.execute(
                            f"SELECT COALESCE(SUM({_sql_name(column)}),0) FROM users"
                        ).fetchone()[0]
                files = _copy_media(source / "media", staging / "media")
                now = time.time()
                report["media_files"] = len(files)
                report["media_bytes"] = sum(item.st_size for item in files.values())
                for job_id, owner in job_owners.items():
                    if f"{owner}/{job_id}.json" not in files:
                        report["missing_job_payloads"] += 1
                for _, user_id, result in jobs:
                    if result is not None:
                        _count_reference(_reference(result, _owner(user_id)), files, report, now)
                for relative in files:
                    owner, name = relative.split("/")
                    if name == "recent-inputs.json":
                        _normalize_payload(staging / "media" / owner / name, owner, files, report, now)
                    elif name.endswith(".json") and name[:-5] in job_owners:
                        if job_owners[name[:-5]] != owner:
                            raise ExportError("payload_owner_mismatch")
                        _normalize_payload(staging / "media" / owner / name, owner, files, report, now)
                report["media_bytes"] = sum(
                    (staging / "media" / relative).stat().st_size for relative in files
                )
            # Reopen after the consistent backup transaction to detect a writer
            # that violated the caller's stopped-poller prerequisite.
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as current:
                if (original_schema != _schema(current)
                        or expected_tables != _table_rows(current, normalize_results=True)):
                    raise ExportError("source_changed")
            os.chmod(snapshot_path, 0o600)
            report["database_sha256"] = _hash_file(snapshot_path)
            _write_archive(staging, output)
        report["archive_bytes"] = output.stat().st_size
        report["archive_sha256"] = _hash_file(output)
        return report
    except ExportError:
        raise
    except FileExistsError:
        raise ExportError("archive_exists") from None
    except (OSError, sqlite3.Error, UnicodeError, ValueError):
        raise ExportError("export_io_failed") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export stopped bot state to a private tar.gz archive.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args(argv)
    try:
        report = export_state(arguments.source, arguments.output)
    except ExportError as error:
        print(f"export_failed:{error}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
