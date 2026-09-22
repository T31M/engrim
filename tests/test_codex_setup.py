"""Regression tests for the Codex CLI setup and hook integration."""
import io
import json
import sqlite3
import sys
from types import SimpleNamespace

import pytest

import engrim.cli as cli
from engrim.hosts.codex import wiring as codex_host
from engrim.cli import main


def _codex_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    codex = home / ".codex"
    codex.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    return codex


def _run_codex_setup(tmp_path, monkeypatch, which="/opt/my tools/bin/engrim"):
    codex = _codex_home(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cli.shutil, "which",
        lambda name: which if name == "engrim" else "/usr/local/bin/codex",
    )
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)
    db = tmp_path / "memory.db"
    main(["--db", str(db), "setup", "--codex"])
    return db, codex


def test_setup_codex_writes_codex_native_hooks_without_requiring_mcp(tmp_path, monkeypatch, capsys):
    db, codex = _run_codex_setup(tmp_path, monkeypatch)
    out = capsys.readouterr().out

    hooks_path = codex / "hooks.json"
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert set(hooks["hooks"]) == {"SessionStart", "SessionEnd", "Stop", "UserPromptSubmit"}
    commands = {
        event: group["hooks"][0]["command"]
        for event, groups in hooks["hooks"].items()
        for group in groups
    }
    assert '"/opt/my tools/bin/engrim" hook --agent codex --event sessionstart' in commands["SessionStart"]
    assert '"/opt/my tools/bin/engrim" assist' in commands["UserPromptSubmit"]
    assert '"/opt/my tools/bin/engrim" log --hook --agent codex' in commands["Stop"]
    assert '"/opt/my tools/bin/engrim" log --hook --agent codex' in commands["SessionEnd"]
    assert "✓ wired Codex hooks" in out
    assert "review and trust" in out
    assert not (codex / "config.toml").exists()
    assert db.exists()


def test_setup_codex_adds_managed_agents_guidance(tmp_path, monkeypatch, capsys):
    codex = _codex_home(tmp_path, monkeypatch)
    agents_path = codex / "AGENTS.md"
    agents_path.write_text("# My instructions\n", encoding="utf-8")
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/local/bin/engrim")
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)
    db = tmp_path / "memory.db"

    main(["--db", str(db), "setup", "--codex"])
    first = agents_path.read_text(encoding="utf-8")
    main(["--db", str(db), "setup", "--codex"])
    second = agents_path.read_text(encoding="utf-8")
    capsys.readouterr()

    assert first.startswith("# My instructions\n")
    assert "<!-- engrim:begin -->" in first
    assert "engrim recall" in first
    assert "engrim add" in first
    assert "<!-- engrim:end -->" in first
    assert second == first


def test_managed_guidance_preserves_surrounding_user_whitespace(
    tmp_path, monkeypatch, capsys
):
    codex = _codex_home(tmp_path, monkeypatch)
    agents_path = codex / "AGENTS.md"
    prefix = "# My instructions\n\n\n"
    suffix = "\n\n\n## Later instructions\n\nKeep these gaps.\n\n"
    agents_path.write_text(
        prefix
        + "<!-- engrim:begin -->\nold guidance\n<!-- engrim:end -->"
        + suffix,
        encoding="utf-8",
    )
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/local/bin/engrim")
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)
    monkeypatch.setattr(codex_host.shutil, "which", lambda _name: None)
    db = tmp_path / "memory.db"

    main(["--db", str(db), "setup", "--codex"])
    updated = agents_path.read_text(encoding="utf-8")
    assert updated.startswith(prefix + codex_host.AGENTS_BLOCK)
    assert updated.endswith(suffix)

    main(["--db", str(db), "uninstall", "--codex"])
    capsys.readouterr()

    assert agents_path.read_text(encoding="utf-8") == prefix + suffix


def test_setup_codex_rejects_ambiguous_managed_guidance_before_writing_hooks(
    tmp_path, monkeypatch
):
    codex = _codex_home(tmp_path, monkeypatch)
    (codex / "AGENTS.md").write_text(
        "<!-- engrim:begin -->\nold\n<!-- engrim:end -->\n"
        "<!-- engrim:begin -->\nsecond\n<!-- engrim:end -->\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/local/bin/engrim")
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)

    with pytest.raises(SystemExit, match="managed markers"):
        main(["--db", str(tmp_path / "memory.db"), "setup", "--codex"])

    assert not (codex / "hooks.json").exists()


def test_setup_codex_warns_when_global_override_shadows_guidance(
    tmp_path, monkeypatch, capsys
):
    codex = _codex_home(tmp_path, monkeypatch)
    (codex / "AGENTS.override.md").write_text("# Temporary override\n", encoding="utf-8")
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/local/bin/engrim")
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)

    main(["--db", str(tmp_path / "memory.db"), "setup", "--codex"])
    out = capsys.readouterr().out

    assert "AGENTS.override.md" in out
    assert "shadows" in out
    assert (codex / "AGENTS.override.md").read_text(encoding="utf-8") == "# Temporary override\n"


def test_setup_and_uninstall_preserve_agents_symlink(tmp_path, monkeypatch, capsys):
    codex = _codex_home(tmp_path, monkeypatch)
    target = codex.parent / "AGENTS.md"
    target.write_text("# Shared user instructions\n", encoding="utf-8")
    agents_path = codex / "AGENTS.md"
    agents_path.symlink_to("../AGENTS.md")
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/local/bin/engrim")
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)
    monkeypatch.setattr(codex_host.shutil, "which", lambda _name: None)
    db = tmp_path / "memory.db"

    main(["--db", str(db), "setup", "--codex"])

    assert agents_path.is_symlink()
    assert "<!-- engrim:begin -->" in target.read_text(encoding="utf-8")

    main(["--db", str(db), "uninstall", "--codex"])
    capsys.readouterr()

    assert agents_path.is_symlink()
    assert target.read_text(encoding="utf-8") == "# Shared user instructions\n"


def test_setup_preserves_existing_unmanaged_engrim_guidance(
    tmp_path, monkeypatch, capsys
):
    codex = _codex_home(tmp_path, monkeypatch)
    agents_path = codex / "AGENTS.md"
    existing = (
        "# My rules\n\n"
        "## Project Memory (engrim) — use it every session\n\n"
        "My detailed user-owned guidance.\n\n"
        "## Other rules\n\nKeep this too.\n"
    )
    agents_path.write_text(existing, encoding="utf-8")
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/local/bin/engrim")
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)

    main(["--db", str(tmp_path / "memory.db"), "setup", "--codex"])
    out = capsys.readouterr().out

    assert agents_path.read_text(encoding="utf-8") == existing
    assert "user-owned Engrim guidance already present" in out


def test_setup_codex_preserves_existing_config_and_is_idempotent(tmp_path, monkeypatch, capsys):
    codex = _codex_home(tmp_path, monkeypatch)
    config_path = codex / "config.toml"
    config_path.write_text(
        'model = "gpt-5.6"\n\n[mcp_servers.keep]\ncommand = "keep-me"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/local/bin/engrim")
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)
    db = tmp_path / "memory.db"

    main(["--db", str(db), "setup", "--codex"])
    first_hooks = (codex / "hooks.json").read_text(encoding="utf-8")
    first_config = config_path.read_text(encoding="utf-8")
    main(["--db", str(db), "setup", "--codex"])
    capsys.readouterr()

    assert (codex / "hooks.json").read_text(encoding="utf-8") == first_hooks
    assert config_path.read_text(encoding="utf-8") == first_config
    assert first_config == 'model = "gpt-5.6"\n\n[mcp_servers.keep]\ncommand = "keep-me"\n'


def test_codex_mcp_flag_implies_setup_and_registers_raw_binary_path(
    tmp_path, monkeypatch, capsys
):
    codex = _codex_home(tmp_path, monkeypatch)
    calls = []

    def which(name):
        return "/opt/my tools/bin/engrim" if name == "engrim" else "/usr/local/bin/codex"

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="Added global MCP server 'engrim'.\n", stderr="")

    monkeypatch.setattr(cli.shutil, "which", which)
    monkeypatch.setattr(codex_host.shutil, "which", which)
    monkeypatch.setattr(codex_host.subprocess, "run", run)
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)

    main(["--db", str(tmp_path / "memory.db"), "setup", "--codex-mcp"])
    out = capsys.readouterr().out

    assert (codex / "hooks.json").exists()
    assert (codex / "AGENTS.md").exists()
    assert calls[0][0] == [
        "/usr/local/bin/codex", "mcp", "add", "engrim", "--",
        "/opt/my tools/bin/engrim", "serve", "--mcp",
    ]
    assert calls[0][1]["env"]["CODEX_HOME"] == str(codex)
    assert "registered Codex MCP server" in out


def test_codex_mcp_failure_does_not_write_partial_setup(tmp_path, monkeypatch):
    codex = _codex_home(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cli.shutil, "which", lambda name: "/usr/local/bin/engrim" if name == "engrim" else None
    )
    monkeypatch.setattr(
        codex_host.shutil, "which",
        lambda name: "/usr/local/bin/engrim" if name == "engrim" else None,
    )
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)

    with pytest.raises(SystemExit, match="needs the `codex` command"):
        main(["--db", str(tmp_path / "memory.db"), "setup", "--codex-mcp"])

    assert not (codex / "hooks.json").exists()
    assert not (codex / "AGENTS.md").exists()


def test_codex_mcp_validates_hooks_before_registering(tmp_path, monkeypatch):
    codex = _codex_home(tmp_path, monkeypatch)
    (codex / "hooks.json").write_text(
        json.dumps({"hooks": {"Stop": "not-an-array"}}), encoding="utf-8"
    )

    def which(name):
        return "/usr/local/bin/engrim" if name == "engrim" else "/usr/local/bin/codex"

    monkeypatch.setattr(cli.shutil, "which", which)
    monkeypatch.setattr(codex_host.shutil, "which", which)
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)
    monkeypatch.setattr(
        codex_host.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("MCP changed before hooks validation"),
    )

    with pytest.raises(SystemExit, match="Stop must contain an array"):
        main(["--db", str(tmp_path / "memory.db"), "setup", "--codex-mcp"])


@pytest.mark.parametrize(
    "stop_groups",
    [
        ["not-an-object"],
        [{"hooks": "not-an-array"}],
        [{"hooks": ["not-an-object"]}],
    ],
)
def test_codex_mcp_validates_nested_hooks_before_registering(
    tmp_path, monkeypatch, stop_groups
):
    codex = _codex_home(tmp_path, monkeypatch)
    (codex / "hooks.json").write_text(
        json.dumps({"hooks": {"Stop": stop_groups}}), encoding="utf-8"
    )

    def which(name):
        return "/usr/local/bin/engrim" if name == "engrim" else "/usr/local/bin/codex"

    monkeypatch.setattr(cli.shutil, "which", which)
    monkeypatch.setattr(codex_host.shutil, "which", which)
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)
    monkeypatch.setattr(
        codex_host.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("MCP changed before nested hooks validation"),
    )

    with pytest.raises(SystemExit, match="Codex hook event Stop"):
        main(["--db", str(tmp_path / "memory.db"), "setup", "--codex-mcp"])


def test_codex_mcp_dry_run_has_no_side_effects(tmp_path, monkeypatch, capsys):
    codex = _codex_home(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/local/bin/engrim")
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)
    monkeypatch.setattr(
        codex_host.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not invoke Codex"),
    )

    main(["--db", str(tmp_path / "memory.db"), "setup", "--codex-mcp", "--dry-run"])
    out = capsys.readouterr().out

    assert "Would register the Engrim MCP server" in out
    assert not (codex / "hooks.json").exists()
    assert not (codex / "AGENTS.md").exists()


def test_codex_session_start_uses_payload_cwd_and_emits_context(tmp_path, monkeypatch, capsys):
    _codex_home(tmp_path, monkeypatch)
    project = tmp_path / "workspace"
    project.mkdir()
    main(["--db", str(tmp_path / "memory.db"), "add", "-p", str(project), "-t", "fact",
          "-s", "Codex project memory is available"])
    capsys.readouterr()

    payload = {"cwd": str(project), "hook_event_name": "SessionStart", "source": "startup"}
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(payload)))
    main(["--db", str(tmp_path / "memory.db"), "hook", "--agent", "codex",
          "--event", "sessionstart", "--no-sync"])
    result = json.loads(capsys.readouterr().out)
    assert result["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Codex project memory is available" in result["hookSpecificOutput"]["additionalContext"]


def test_codex_prompt_and_stop_hooks_log_once(tmp_path, monkeypatch, capsys):
    _codex_home(tmp_path, monkeypatch)
    project = tmp_path / "workspace"
    project.mkdir()
    prompt = {
        "cwd": str(project),
        "hook_event_name": "UserPromptSubmit",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "prompt": "We decided to keep the SQLite memory store.",
    }
    stop = {
        "cwd": str(project),
        "hook_event_name": "Stop",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "last_assistant_message": "The SQLite memory store remains the selected design.",
    }
    for payload in (prompt, prompt, stop, stop):
        monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(payload)))
        args = ["--db", str(tmp_path / "memory.db"), "log", "--hook", "--agent", "codex"]
        main(args)
        capsys.readouterr()

    conn = sqlite3.connect(tmp_path / "memory.db")
    rows = conn.execute("SELECT role, content FROM log ORDER BY id").fetchall()
    assert rows == [
        ("user", "We decided to keep the SQLite memory store."),
        ("assistant", "The SQLite memory store remains the selected design."),
    ]


def test_statusline_reads_codex_payload(tmp_path, monkeypatch, capsys):
    _codex_home(tmp_path, monkeypatch)
    project = tmp_path / "workspace"
    project.mkdir()
    db = tmp_path / "memory.db"
    main(["--db", str(db), "add", "-p", str(project), "-t", "fact", "-s", "status is live"])
    capsys.readouterr()

    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps({
        "cwd": str(project),
        "session_id": "session-1",
    })))
    main(["--db", str(db), "statusline"])
    out = capsys.readouterr().out
    assert "engrim" in out
    assert "curated" in out


def test_doctor_reports_codex_guidance_and_optional_absent_mcp_as_healthy(
    tmp_path, monkeypatch, capsys
):
    codex = _codex_home(tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRIM_EMBED", "off")

    def which(name):
        return sys.executable if name == "engrim" else "/usr/local/bin/codex"

    monkeypatch.setattr(cli.shutil, "which", which)
    monkeypatch.setattr(codex_host.shutil, "which", which)
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)
    monkeypatch.setattr(
        codex_host.subprocess,
        "run",
        lambda argv, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="Error: No MCP server named 'engrim' found.\n"
        ),
    )
    db = tmp_path / "memory.db"
    main(["--db", str(db), "setup", "--codex"])
    capsys.readouterr()

    main(["--db", str(db), "doctor", "--json"])
    report = json.loads(capsys.readouterr().out)
    env = report["environments"]["codex"]

    assert env["agents_md"]["managed"] is True
    assert env["agents_md"]["shadowed"] is False
    assert env["mcp"]["configured"] is False
    assert env["mcp"]["valid"] is True
    assert not any("Codex CLI" in issue for issue in report["issues"])
    assert (codex / "AGENTS.md").exists()


def test_doctor_fix_restores_codex_without_enabling_absent_mcp(
    tmp_path, monkeypatch, capsys
):
    codex = _codex_home(tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRIM_EMBED", "off")
    calls = []

    def which(name):
        return sys.executable if name == "engrim" else "/usr/local/bin/codex"

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(
            returncode=1, stdout="", stderr="Error: No MCP server named 'engrim' found.\n"
        )

    monkeypatch.setattr(cli.shutil, "which", which)
    monkeypatch.setattr(codex_host.shutil, "which", which)
    monkeypatch.setattr(codex_host.subprocess, "run", run)
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)

    main(["--db", str(tmp_path / "memory.db"), "doctor", "--fix", "--json"])
    report = json.loads(capsys.readouterr().out)

    assert report["fixes"] == ["Repaired Codex CLI hooks, guidance, and configured MCP state"]
    assert (codex / "hooks.json").exists()
    assert (codex / "AGENTS.md").exists()
    assert all(argv[1:4] != ["mcp", "add", "engrim"] for argv in calls)


def test_doctor_fix_repairs_configured_codex_mcp(tmp_path, monkeypatch, capsys):
    codex = _codex_home(tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRIM_EMBED", "off")
    calls = []

    def which(name):
        return sys.executable if name == "engrim" else "/usr/local/bin/codex"

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1:4] == ["mcp", "get", "engrim"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "name": "engrim",
                    "transport": {
                        "type": "stdio",
                        "command": "/missing/engrim",
                        "args": ["serve", "--mcp"],
                    },
                }),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="Added global MCP server 'engrim'.\n", stderr="")

    monkeypatch.setattr(cli.shutil, "which", which)
    monkeypatch.setattr(codex_host.shutil, "which", which)
    monkeypatch.setattr(codex_host.subprocess, "run", run)
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)

    main(["--db", str(tmp_path / "memory.db"), "doctor", "--fix", "--json"])
    report = json.loads(capsys.readouterr().out)

    assert any("Codex CLI MCP server broken" in issue for issue in report["issues"])
    assert any(argv[1:4] == ["mcp", "add", "engrim"] for argv in calls)


@pytest.mark.parametrize(
    "entry",
    [
        {},
        {
            "name": "engrim",
            "enabled": False,
            "transport": {
                "type": "stdio",
                "command": "/usr/local/bin/engrim",
                "args": ["serve", "--mcp"],
            },
        },
        {
            "name": "engrim",
            "enabled": True,
            "transport": {
                "type": "stdio",
                "command": "/usr/local/bin/not-engrim",
                "args": ["serve", "--mcp"],
            },
        },
    ],
)
def test_inspect_codex_mcp_rejects_disabled_or_non_engrim_entry(
    tmp_path, monkeypatch, entry
):
    _codex_home(tmp_path, monkeypatch)
    monkeypatch.setattr(codex_host.shutil, "which", lambda _name: "/usr/local/bin/codex")
    monkeypatch.setattr(
        codex_host.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(entry), stderr=""
        ),
    )

    status = codex_host.inspect_mcp()

    assert status["configured"] is True
    assert status["valid"] is False


def test_codex_setup_and_uninstall_preserve_near_match_hook(
    tmp_path, monkeypatch, capsys
):
    codex = _codex_home(tmp_path, monkeypatch)
    near_match = {
        "hooks": [
            {"type": "command", "command": "/usr/local/bin/not-engrim --keep", "timeout": 9}
        ]
    }
    (codex / "hooks.json").write_text(
        json.dumps({"hooks": {"Stop": [near_match]}}), encoding="utf-8"
    )
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/local/bin/engrim")
    monkeypatch.setattr(cli, "_verify_hook_bin", lambda _bin: None)
    monkeypatch.setattr(codex_host.shutil, "which", lambda _name: None)
    db = tmp_path / "memory.db"

    main(["--db", str(db), "setup", "--codex"])
    configured = json.loads((codex / "hooks.json").read_text(encoding="utf-8"))
    assert configured["hooks"]["Stop"][0] == near_match
    assert len(configured["hooks"]["Stop"]) == 2

    main(["--db", str(db), "uninstall", "--codex"])
    capsys.readouterr()
    remaining = json.loads((codex / "hooks.json").read_text(encoding="utf-8"))
    assert remaining["hooks"]["Stop"] == [near_match]


def test_uninstall_codex_removes_managed_guidance_and_preserves_user_content(
    tmp_path, monkeypatch, capsys
):
    db, codex = _run_codex_setup(tmp_path, monkeypatch)
    hooks_path = codex / "hooks.json"
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    hooks["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "keep-me"}]})
    hooks_path.write_text(json.dumps(hooks), encoding="utf-8")
    config_path = codex / "config.toml"
    config_path.write_text('[mcp_servers.keep]\ncommand = "keep-me"\n', encoding="utf-8")
    original_config = config_path.read_text(encoding="utf-8")
    agents_path = codex / "AGENTS.md"
    agents_path.write_text(
        "# My instructions\n\n" + agents_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(
        codex_host.subprocess,
        "run",
        lambda argv, **kwargs: (
            calls.append((argv, kwargs))
            or SimpleNamespace(returncode=0, stdout="Removed global MCP server 'engrim'.\n", stderr="")
        ),
    )

    main(["--db", str(db), "uninstall", "--codex"])
    capsys.readouterr()
    remaining_hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert all(
        h.get("command") == "keep-me"
        for groups in remaining_hooks["hooks"].values()
        for group in groups
        for h in group.get("hooks", [])
    )
    assert config_path.read_text(encoding="utf-8") == original_config
    assert agents_path.read_text(encoding="utf-8") == "# My instructions\n\n"
    assert calls[0][0] == ["/usr/local/bin/codex", "mcp", "remove", "engrim"]


def test_uninstall_codex_finishes_local_cleanup_when_codex_is_missing(
    tmp_path, monkeypatch, capsys
):
    db, codex = _run_codex_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        codex_host.shutil,
        "which",
        lambda name: "/usr/local/bin/engrim" if name == "engrim" else None,
    )

    main(["--db", str(db), "uninstall", "--codex"])
    out = capsys.readouterr().out

    hooks = json.loads((codex / "hooks.json").read_text(encoding="utf-8"))
    assert hooks.get("hooks") == {}
    assert "<!-- engrim:begin -->" not in (codex / "AGENTS.md").read_text(encoding="utf-8")
    assert "codex mcp remove engrim" in out
