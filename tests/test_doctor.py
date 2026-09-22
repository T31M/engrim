import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
import pytest

from engrim.cli import build_parser, connect, cmd_doctor, _hook_bin


def test_hook_bin_cross_platform_resilience():
    # If on POSIX, a Windows path should fall back to engrim on PATH
    if sys.platform != "win32":
        resolved = _hook_bin("C:\\Users\\timgo\\AppData\\Local\\Programs\\Python\\Python313\\Scripts\\engrim.EXE")
        assert not resolved.startswith('"C:')
        assert "engrim" in resolved


def test_doctor_terminal_output(capsys, monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("HOME", tmpdir)
        monkeypatch.setenv("USERPROFILE", tmpdir)
        monkeypatch.setenv("CODEX_HOME", str(Path(tmpdir) / ".codex"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(Path(tmpdir) / ".config"))
        db_path = Path(tmpdir) / "test_memory.db"
        conn = connect(str(db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, ts TEXT, project TEXT, type TEXT, summary TEXT, detail TEXT, status TEXT, tags TEXT, links TEXT, source TEXT, origin_agent TEXT)")
        conn.execute("INSERT INTO memories (id, ts, project, type, summary, detail, status, tags, links, source, origin_agent) VALUES (1, '2026-09-13T00:00:00Z', 'test-proj', 'decision', 'Test decision', 'Detail', 'active', 'tag1', NULL, 'cli', 'cli')")
        conn.commit()

        parser = build_parser()
        args = parser.parse_args(["--db", str(db_path), "doctor", "-p", "test-proj"])

        cmd_doctor(conn, args)
        conn.close()

        captured = capsys.readouterr().out
        assert "ENGRIM DOCTOR: DIAGNOSTIC HEALTH CHECK" in captured
        assert "Database & Storage Engine" in captured
        assert "Integrity Check" in captured
        assert "Curated Memories" in captured
        assert "test_memory.db" in captured


def test_doctor_json_output(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_memory.db"
        conn = connect(str(db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, ts TEXT, project TEXT, type TEXT, summary TEXT, detail TEXT, status TEXT, tags TEXT, links TEXT, source TEXT, origin_agent TEXT)")
        conn.commit()

        parser = build_parser()
        args = parser.parse_args(["--db", str(db_path), "doctor", "-p", "test-proj", "--json"])

        cmd_doctor(conn, args)
        conn.close()

        captured = capsys.readouterr().out
        data = json.loads(captured)
        assert "platform" in data
        assert "database" in data
        assert "semantic" in data
        assert "environments" in data
        assert data["database"]["integrity"] == "ok"
        assert data["database"]["journal_mode"] == "wal"


def test_doctor_detects_broken_hook_and_fix(capsys, monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_home = Path(tmpdir)
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(fake_home)))

        # Create broken Antigravity hook
        gemini_dir = fake_home / ".gemini" / "config"
        gemini_dir.mkdir(parents=True, exist_ok=True)
        hooks_file = gemini_dir / "hooks.json"
        with open(hooks_file, "w") as f:
            json.dump({
                "engrim": {
                    "PreInvocation": [{"type": "command", "command": '"C:/NonExistent/engrim.EXE" hook --agent agy --event boot 2>/dev/null || true'}],
                    "Stop": [{"type": "command", "command": '"C:/NonExistent/engrim.EXE" hook --agent agy --event stop >/dev/null 2>&1 || true'}]
                }
            }, f)

        db_path = fake_home / "test_memory.db"
        conn = connect(str(db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, ts TEXT, project TEXT, type TEXT, summary TEXT, detail TEXT, status TEXT, tags TEXT, links TEXT, source TEXT, origin_agent TEXT)")
        conn.commit()

        parser = build_parser()
        # 1. Run doctor without --fix, expect issues detected and exit code 1
        args = parser.parse_args(["--db", str(db_path), "doctor", "-p", "test-proj"])
        with pytest.raises(SystemExit) as exc_info:
            cmd_doctor(conn, args)
        assert exc_info.value.code == 1

        captured = capsys.readouterr().out
        assert "Antigravity PreInvocation hook broken" in captured

        # 2. Run doctor with --fix, should repair
        args_fix = parser.parse_args(["--db", str(db_path), "doctor", "-p", "test-proj", "--fix"])
        cmd_doctor(conn, args_fix)

        captured_fix = capsys.readouterr().out
        assert "REPAIRS APPLIED" in captured_fix or "Repaired Google Antigravity" in captured_fix

        # Check repaired hooks file
        with open(hooks_file, "r") as f:
            repaired = json.load(f)
        cmd_pre = repaired["engrim"]["PreInvocation"][0]["command"]
        assert "|| engrim hook --agent agy --event boot" in cmd_pre

        conn.close()
