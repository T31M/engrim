"""Codex CLI install-time wiring for hooks and global agent guidance."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from importlib import resources

from engrim.hosts import command_has_marker

_FILES = resources.files(__package__)
AGENTS_MD = (_FILES / "AGENTS.md").read_text(encoding="utf-8")
AGENTS_BEGIN = "<!-- engrim:begin -->"
AGENTS_END = "<!-- engrim:end -->"
AGENTS_BLOCK = f"{AGENTS_BEGIN}\n{AGENTS_MD.rstrip()}\n{AGENTS_END}"
LEGACY_AGENTS_HEADING = "## Project Memory (engrim)"


def home() -> str:
    """Return the Codex home directory, including the supported override."""
    return os.path.abspath(os.path.expanduser(os.environ.get("CODEX_HOME") or "~/.codex"))


def _hook_commands(engrim_bin: str) -> dict[str, tuple[str, int]]:
    return {
        "SessionStart": (
            f"{engrim_bin} hook --agent codex --event sessionstart 2>/dev/null || "
            "engrim hook --agent codex --event sessionstart 2>/dev/null || true",
            20,
        ),
        "SessionEnd": (
            f"{engrim_bin} log --hook --agent codex 2>/dev/null || "
            "engrim log --hook --agent codex 2>/dev/null || true",
            3,
        ),
        "Stop": (
            f"{engrim_bin} log --hook --agent codex 2>/dev/null || "
            "engrim log --hook --agent codex 2>/dev/null || true",
            30,
        ),
        "UserPromptSubmit": (
            f"{engrim_bin} assist 2>/dev/null || engrim assist 2>/dev/null || true",
            20,
        ),
    }


def _instruction_span(content: str) -> tuple[int, int] | None:
    if content.count(AGENTS_BEGIN) > 1 or content.count(AGENTS_END) > 1:
        raise ValueError("found duplicate managed markers")
    start = content.find(AGENTS_BEGIN)
    if start < 0:
        if AGENTS_END in content:
            raise ValueError(f"found {AGENTS_END!r} without matching {AGENTS_BEGIN!r}")
        return None
    end = content.find(AGENTS_END, start + len(AGENTS_BEGIN))
    if end < 0:
        raise ValueError(f"found {AGENTS_BEGIN!r} without matching {AGENTS_END!r}")
    return start, end + len(AGENTS_END)


def _has_user_owned_guidance(content: str) -> bool:
    return any(line.startswith(LEGACY_AGENTS_HEADING) for line in content.splitlines())


def _guidance_write_path(agents_path: str) -> str:
    """Write through a user-managed symlink instead of replacing the link itself."""
    return os.path.realpath(agents_path) if os.path.islink(agents_path) else agents_path


def _replace_instructions(content: str, replacement: str | None) -> str:
    span = _instruction_span(content)
    if span is None:
        if replacement is None:
            return content
        if _has_user_owned_guidance(content):
            return content
        separator = "" if not content or content.endswith("\n") else "\n"
        return content + separator + replacement + "\n"
    start, end = span
    suffix = content[end:]
    if replacement is None and suffix == "\n":
        suffix = ""
    return content[:start] + (replacement or "") + suffix


def _register_mcp(engrim_bin: str) -> None:
    codex_bin = shutil.which("codex")
    if not codex_bin:
        sys.exit("Codex MCP setup needs the `codex` command on PATH. Install Codex, then re-run.")
    raw_engrim_bin = engrim_bin.strip('"')
    env = os.environ.copy()
    env["CODEX_HOME"] = home()
    argv = [codex_bin, "mcp", "add", "engrim", "--", raw_engrim_bin, "serve", "--mcp"]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        sys.exit(f"couldn't register the Codex MCP server ({type(exc).__name__}: {exc})")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        sys.exit(f"Codex MCP registration failed: {detail}")
    print(f"✓ registered Codex MCP server in {os.path.join(home(), 'config.toml')}")


def _remove_mcp() -> None:
    codex_bin = shutil.which("codex")
    manual = "codex mcp remove engrim"
    if not codex_bin:
        print(f"• the `codex` command is not on PATH; run `{manual}` later to remove the optional MCP entry")
        return
    env = os.environ.copy()
    env["CODEX_HOME"] = home()
    try:
        result = subprocess.run(
            [codex_bin, "mcp", "remove", "engrim"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"• couldn't remove the Codex MCP entry ({type(exc).__name__}: {exc}); run `{manual}` later")
        return
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        print(f"• Codex MCP removal failed ({detail}); run `{manual}` later")
        return
    print(f"✓ removed optional Codex MCP server from {os.path.join(home(), 'config.toml')}")


def guidance_status() -> dict[str, object]:
    agents_path = os.path.join(home(), "AGENTS.md")
    override_path = os.path.join(home(), "AGENTS.override.md")
    managed = False
    present = False
    reason = "managed guidance is missing"
    if os.path.exists(agents_path):
        with open(agents_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
        try:
            managed = _instruction_span(content) is not None
        except ValueError as exc:
            reason = str(exc)
        else:
            if managed:
                present = True
                reason = "ok"
            elif _has_user_owned_guidance(content):
                present = True
                reason = "user-owned Engrim guidance is present"
    shadowed = os.path.exists(override_path) and os.path.getsize(override_path) > 0
    return {"present": present, "managed": managed, "shadowed": shadowed, "reason": reason}


def inspect_mcp() -> dict[str, object]:
    """Inspect the optional MCP entry through Codex's supported configuration interface."""
    codex_bin = shutil.which("codex")
    if not codex_bin:
        return {
            "configured": None,
            "valid": False,
            "reason": "the `codex` command is not on PATH",
        }
    env = os.environ.copy()
    env["CODEX_HOME"] = home()
    try:
        result = subprocess.run(
            [codex_bin, "mcp", "get", "engrim", "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"configured": None, "valid": False, "reason": str(exc)}
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        if "No MCP server named 'engrim'" in detail:
            return {"configured": False, "valid": True, "reason": "optional MCP is not configured"}
        return {"configured": None, "valid": False, "reason": detail}
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        return {"configured": True, "valid": False, "reason": f"invalid Codex JSON output: {exc}"}
    transport = data.get("transport") if isinstance(data, dict) else None
    command = transport.get("command") if isinstance(transport, dict) else None
    args = transport.get("args") if isinstance(transport, dict) else None
    enabled = data.get("enabled", True) if isinstance(data, dict) else False
    valid = (
        enabled is not False
        and isinstance(transport, dict)
        and transport.get("type") == "stdio"
        and isinstance(command, str)
        and command_has_marker(command, "engrim")
        and args == ["serve", "--mcp"]
    )
    return {
        "configured": True,
        "valid": valid,
        "command": command,
        "args": args,
        "reason": (
            "ok"
            if valid
            else "expected an enabled Engrim stdio command with args ['serve', '--mcp']"
        ),
    }


def setup(engrim_bin: str, *, with_mcp: bool = False, dry_run: bool = False) -> None:
    """Install or update Codex command hooks and global Engrim guidance."""
    print("Wiring Codex CLI environment…")
    codex_home = home()
    hooks_path = os.path.join(codex_home, "hooks.json")
    agents_path = os.path.join(codex_home, "AGENTS.md")
    agents_write_path = _guidance_write_path(agents_path)
    override_path = os.path.join(codex_home, "AGENTS.override.md")
    commands = _hook_commands(engrim_bin)
    if dry_run:
        print(f"[dry-run] Would wire Codex hooks in {hooks_path}")
        for event, (command, _timeout) in commands.items():
            print(f"    {event}: {command}")
        print(f"[dry-run] Would add or update usage note in {agents_path}")
        if with_mcp:
            print(f"[dry-run] Would register the Engrim MCP server in {os.path.join(codex_home, 'config.toml')}")
        print("[dry-run] Codex hooks must be reviewed and trusted with /hooks before they run")
        return

    hooks_data = {}
    if os.path.exists(hooks_path):
        try:
            with open(hooks_path, "r", encoding="utf-8") as f:
                hooks_data = json.load(f)
        except json.JSONDecodeError as exc:
            sys.exit(f"Codex hooks file exists but is not valid JSON ({exc}). Fix it, then re-run.")
        except OSError as exc:
            sys.exit(f"can't read {hooks_path} ({exc}). Fix the permissions, then re-run.")
    if not isinstance(hooks_data, dict):
        sys.exit(f"Codex hooks file must contain a JSON object: {hooks_path}")
    hooks = hooks_data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        sys.exit(f"Codex hooks field must be a JSON object: {hooks_path}")

    existing_agents = ""
    if os.path.exists(agents_path):
        with open(agents_path, encoding="utf-8", errors="replace") as f:
            existing_agents = f.read()
    try:
        updated_agents = _replace_instructions(existing_agents, AGENTS_BLOCK)
    except ValueError as exc:
        sys.exit(f"{agents_path}: {exc}. Fix the managed markers, then re-run.")

    for event in commands:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            sys.exit(f"Codex hook event {event} must contain an array: {hooks_path}")
        for group in groups:
            if not isinstance(group, dict):
                sys.exit(f"Codex hook event {event} contains a non-object group: {hooks_path}")
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                sys.exit(f"Codex hook event {event} group must contain a hooks array: {hooks_path}")
            if not all(isinstance(handler, dict) for handler in handlers):
                sys.exit(f"Codex hook event {event} contains a non-object hook: {hooks_path}")

    if with_mcp:
        _register_mcp(engrim_bin)

    changed = False
    for event, (command, timeout) in commands.items():
        groups = hooks[event]
        managed = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            handlers = group.get("hooks", [])
            if not isinstance(handlers, list):
                continue
            managed.extend(
                handler for handler in handlers
                if isinstance(handler, dict)
                and command_has_marker(handler.get("command", ""), "engrim")
            )
        if managed:
            for handler in managed:
                desired = {"type": "command", "command": command, "timeout": timeout}
                if handler != desired:
                    handler.clear()
                    handler.update(desired)
                    changed = True
            print(f"✓ {event} Codex hook already present in {hooks_path}")
        else:
            groups.append({"hooks": [{"type": "command", "command": command, "timeout": timeout}]})
            changed = True
            print(f"✓ wired {event} Codex hook\n    {command}")

    os.makedirs(codex_home, exist_ok=True)
    if changed:
        tmp = hooks_path + ".engrim-tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(hooks_data, f, indent=2)
            f.write("\n")
        os.replace(tmp, hooks_path)
        print(f"✓ wired Codex hooks in {hooks_path}")
    else:
        print(f"✓ Codex hooks already current in {hooks_path}")

    if updated_agents == existing_agents:
        if _has_user_owned_guidance(existing_agents) and _instruction_span(existing_agents) is None:
            print(f"✓ user-owned Engrim guidance already present ({agents_path})")
        else:
            print(f"✓ AGENTS.md usage note already current ({agents_path})")
    else:
        os.makedirs(os.path.dirname(agents_write_path), exist_ok=True)
        tmp = agents_write_path + ".engrim-tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(updated_agents)
        os.replace(tmp, agents_write_path)
        print(f"✓ added or updated usage note in {agents_path}")
    if os.path.exists(override_path) and os.path.getsize(override_path) > 0:
        print(f"! {override_path} is non-empty and shadows {agents_path}; add the Engrim guidance there too")
    print("! review and trust these hooks in Codex with /hooks before they run")


def uninstall(*, dry_run: bool = False) -> None:
    """Remove only Engrim-owned Codex hooks and global guidance."""
    print("Unwiring Codex CLI environment…")
    codex_home = home()
    hooks_path = os.path.join(codex_home, "hooks.json")
    agents_path = os.path.join(codex_home, "AGENTS.md")
    agents_write_path = _guidance_write_path(agents_path)
    if dry_run:
        print(f"[dry-run] Would unwire Codex hooks in {hooks_path}")
        print(f"[dry-run] Would remove the managed usage note from {agents_path}")
        print(f"[dry-run] Would remove the optional Engrim MCP server from {os.path.join(codex_home, 'config.toml')}")
        return

    if os.path.exists(hooks_path):
        try:
            with open(hooks_path, "r", encoding="utf-8") as f:
                hooks_data = json.load(f)
        except json.JSONDecodeError as exc:
            sys.exit(f"Codex hooks file exists but is not valid JSON ({exc}). Fix it, then re-run.")
        if not isinstance(hooks_data, dict):
            sys.exit(f"Codex hooks file must contain a JSON object: {hooks_path}")
        hooks = hooks_data.get("hooks", {})
        if not isinstance(hooks, dict):
            sys.exit(f"Codex hooks field must be a JSON object: {hooks_path}")
        changed = False
        for event in ("SessionStart", "SessionEnd", "Stop", "UserPromptSubmit"):
            groups = hooks.get(event)
            if not isinstance(groups, list):
                continue
            new_groups = []
            for group in groups:
                if not isinstance(group, dict):
                    new_groups.append(group)
                    continue
                handlers = group.get("hooks", [])
                if not isinstance(handlers, list):
                    new_groups.append(group)
                    continue
                new_handlers = [
                    handler for handler in handlers
                    if not (
                        isinstance(handler, dict)
                        and command_has_marker(handler.get("command", ""), "engrim")
                    )
                ]
                if len(new_handlers) != len(handlers):
                    changed = True
                if new_handlers:
                    group["hooks"] = new_handlers
                    new_groups.append(group)
            if new_groups:
                hooks[event] = new_groups
            elif event in hooks:
                del hooks[event]
                changed = True
        if changed:
            tmp = hooks_path + ".engrim-tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(hooks_data, f, indent=2)
                f.write("\n")
            os.replace(tmp, hooks_path)
            print(f"✓ unwired Codex hooks from {hooks_path}")
        else:
            print(f"✓ Codex hooks already unwired from {hooks_path}")
    else:
        print(f"✓ Codex hooks already unwired from {hooks_path}")

    if os.path.exists(agents_path):
        with open(agents_path, encoding="utf-8", errors="replace") as f:
            existing = f.read()
        try:
            updated = _replace_instructions(existing, None)
        except ValueError as exc:
            print(f"• {agents_path}: {exc} - remove the Engrim section by hand")
        else:
            if updated != existing:
                tmp = agents_write_path + ".engrim-tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(updated)
                os.replace(tmp, agents_write_path)
                print(f"✓ removed usage note from {agents_path}")

    _remove_mcp()
