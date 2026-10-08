import hashlib
import importlib.util
import json
import os
import sqlite3
import tarfile
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "deploy" / "vps" / "export_state.py"
SPEC = importlib.util.spec_from_file_location("vps_export", MODULE_PATH)
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)


def fixture_state(tmp_path):
    source = tmp_path / "synthetic-state"
    owner = source / "media" / "7"
    owner.mkdir(parents=True)
    database = source / "db.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY, balance INTEGER, reserved INTEGER, consent INTEGER,
            spent INTEGER, trial_granted INTEGER, trial_used INTEGER, trial_reserved INTEGER,
            extra BLOB, note TEXT
        );
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, user_id INTEGER, status TEXT, result TEXT, usage TEXT,
            request_key TEXT, trial INTEGER
        );
        CREATE TABLE ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, value BLOB, note TEXT);
        INSERT INTO users VALUES(7,3,0,1,2,1,1,1,X'0001FF','preserved');
    """)
    connection.executemany("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)", [
        ("done", 7, "delivered", "7\\done.jpg", '{"a": 1}', "event-a", 1),
        ("old", 7, "delivered", "7\\missing.jpg", None, "event-b", 0),
        ("review", 7, "review", None, None, "event-c", 1),
    ])
    connection.execute("INSERT INTO ledger(value,note) VALUES(?,?)", (b"\0\xff", "prompt\\untouched"))
    connection.commit()
    connection.close()
    prompt = 'Private synthetic prompt: keep \\slashes and "quotes" exactly.'
    payload = '{ "inputs" : ["7\\\\original.jpg", "7\\\\missing-original.jpg"], "prompt" : '
    payload += json.dumps(prompt) + ', "nested" : {"inputs": ["not/a/reference"]}, "number": 1.0000 }\n'
    (owner / "done.json").write_text(payload, encoding="utf-8")
    (owner / "recent-inputs.json").write_text('{"inputs":["7\\\\original.jpg"]}', encoding="utf-8")
    (owner / "original.jpg").write_bytes(b"synthetic-original-bytes")
    (owner / "done.jpg").write_bytes(b"synthetic-result-bytes")
    old_ns = 1_700_000_000_123_456_700
    for path in owner.iterdir():
        os.utime(path, ns=(old_ns, old_ns))
    # These non-state files must never enter a snapshot.
    (source / ".env").write_bytes(b"synthetic-private-config")
    (source / "runtime").mkdir()
    (source / "runtime" / "lock").write_bytes(b"synthetic-lock")
    return source, prompt


def source_signature(source):
    return {
        str(path.relative_to(source)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in source.rglob("*") if path.is_file() and path.name != "db.sqlite3-shm"
    }


def rows(database):
    connection = sqlite3.connect(database)
    try:
        names = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
        ).fetchall()
        return {name: connection.execute(f'SELECT * FROM "{name}"').fetchall() for (name,) in names}
    finally:
        connection.close()


def test_export_preserves_state_normalizes_snapshot_and_mtime(tmp_path, capsys):
    source, prompt = fixture_state(tmp_path)
    wal_connection = sqlite3.connect(source / "db.sqlite3")
    assert wal_connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    wal_connection.execute("INSERT INTO ledger(value,note) VALUES(?,?)", (b"committed-wal", "preserve WAL row"))
    wal_connection.commit()
    assert (source / "db.sqlite3-wal").stat().st_size > 0
    before = source_signature(source)
    output = tmp_path / "state.tar.gz"
    report = EXPORT.export_state(source, output)
    assert source_signature(source) == before
    assert report["users"] == 1 and report["jobs"] == 3
    assert report["normalized_job_results"] == 2
    assert report["normalized_json_files"] == 2
    assert report["normalized_input_references"] == 3
    assert report["missing_references"] == 2
    assert report["missing_job_payloads"] == 2
    assert report["expired_references"] == 3
    assert report["users_trial_reserved_total"] == 1
    assert report["archive_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert capsys.readouterr().out == ""
    restored = tmp_path / "restored"
    restored.mkdir()
    with tarfile.open(output, "r:gz") as archive:
        names = archive.getnames()
        assert all(name == "db.sqlite3" or name == "media" or name.startswith("media/") for name in names)
        assert all(member.isfile() or member.isdir() for member in archive)
        assert all(member.uid == member.gid == 0 and member.uname == member.gname == "" for member in archive)
        for member in archive:
            if member.isfile() and member.name.startswith("media/"):
                source_path = source / member.name
                seconds, nanos = divmod(source_path.stat().st_mtime_ns, 1_000_000_000)
                assert member.pax_headers["mtime"] == f"{seconds}.{nanos:09d}"
        archive.extractall(restored, filter="data")
    actual, expected = rows(restored / "db.sqlite3"), rows(source / "db.sqlite3")
    expected["jobs"] = [tuple(value.replace("\\", "/") if i == 3 and value else value
                              for i, value in enumerate(row)) for row in expected["jobs"]]
    assert actual == expected
    assert actual["ledger"][-1][1] == b"committed-wal"
    record = json.loads((restored / "media" / "7" / "done.json").read_text(encoding="utf-8"))
    assert record["inputs"] == ["7/original.jpg", "7/missing-original.jpg"]
    assert record["prompt"] == prompt
    original_text = before["media\\7\\done.json" if os.name == "nt" else "media/7/done.json"][0].decode()
    assert (restored / "media" / "7" / "done.json").read_bytes().decode("utf-8") == original_text.replace(
        '["7\\\\original.jpg", "7\\\\missing-original.jpg"]',
        '["7/original.jpg", "7/missing-original.jpg"]',
    )
    assert not (restored / "media" / "7" / "missing.jpg").exists()
    saved_archive = output.read_bytes()
    with pytest.raises(EXPORT.ExportError, match="archive_exists"):
        EXPORT.export_state(source, output)
    assert output.read_bytes() == saved_archive
    assert EXPORT.main(["--source", str(source), "--output", str(output)]) == 1
    assert capsys.readouterr().err == "export_failed:archive_exists\n"
    assert not list(tmp_path.glob(".state-export-*"))
    wal_connection.close()


@pytest.mark.parametrize("unsafe", [
    "../outside.jpg", "/7/absolute.jpg", "C:\\7\\absolute.jpg", "8/other-owner.jpg",
    "7/../outside.jpg", "7//original.jpg", "payload-owner", "file-symlink", "directory-symlink",
    "output-symlink", "output-inside-source", "active-job", "input-owner", "input-traversal",
    "recent-input-owner",
])
def test_export_rejects_unsafe_references_and_symlinks(tmp_path, unsafe):
    source, _ = fixture_state(tmp_path)
    output = tmp_path / "unsafe.tar.gz"
    if unsafe in {"file-symlink", "directory-symlink", "output-symlink"}:
        if unsafe == "file-symlink":
            link, target, directory = source / "media" / "7" / "link.jpg", tmp_path / "outside.jpg", False
            target.write_bytes(b"outside")
        elif unsafe == "directory-symlink":
            link, target, directory = source / "media" / "8", tmp_path / "outside", True
            target.mkdir()
        else:
            link, target, directory = output, tmp_path / "existing.tar.gz", False
            target.write_bytes(b"preserve-existing")
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError:
            pytest.skip("OS does not permit creation of synthetic symlinks")
    elif unsafe == "payload-owner":
        other = source / "media" / "8"
        other.mkdir()
        (source / "media" / "7" / "done.json").rename(other / "done.json")
    elif unsafe == "output-inside-source":
        output = source / "archive.tar.gz"
    elif unsafe in {"input-owner", "input-traversal", "recent-input-owner"}:
        filename = "recent-inputs.json" if unsafe == "recent-input-owner" else "done.json"
        reference = "7/../outside.jpg" if unsafe == "input-traversal" else "8/outside.jpg"
        (source / "media" / "7" / filename).write_text(
            json.dumps({"inputs": [reference], "prompt": "synthetic"}), encoding="utf-8",
        )
    else:
        connection = sqlite3.connect(source / "db.sqlite3")
        if unsafe == "active-job":
            connection.execute("UPDATE jobs SET status='queued' WHERE id='review'")
        else:
            connection.execute("UPDATE jobs SET result=? WHERE id='done'", (unsafe,))
        connection.commit()
        connection.close()
    before_db = (source / "db.sqlite3").read_bytes()
    with pytest.raises(EXPORT.ExportError):
        EXPORT.export_state(source, output)
    assert (source / "db.sqlite3").read_bytes() == before_db
    assert not list(tmp_path.glob(".state-export-*"))
    if unsafe != "output-symlink":
        assert not output.exists()
