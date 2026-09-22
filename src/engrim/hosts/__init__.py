"""One package per agent environment (host) engrim plugs into.

Each host directory holds both halves of its integration: install-time wiring (`wiring.py`, with
any files it writes) and runtime behaviour (`hooks.py`, what happens on boot / prompt / stop),
so a host can be read, reviewed, or removed as a unit.
"""
import re


def command_has_marker(command: str, marker: str) -> bool:
    """Return whether a hook command contains a cross-platform executable marker."""
    norm = command.replace("\\", "/").replace('"', "").replace("'", "").lower()
    norm = norm.replace(".exe", "")
    pattern = rf"(?<![\w.-]){re.escape(marker.lower())}(?![\w.-])"
    return re.search(pattern, norm) is not None
