#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional


EXIT_OK = 0
EXIT_ERROR = 1


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _sessions_root() -> Path:
    if os.environ.get("CCB_CODEX_SESSION_ROOT"):
        return Path(os.environ["CCB_CODEX_SESSION_ROOT"]).expanduser()
    if os.environ.get("CODEX_SESSION_ROOT"):
        return Path(os.environ["CODEX_SESSION_ROOT"]).expanduser()
    if os.environ.get("CODEX_HOME"):
        return Path(os.environ["CODEX_HOME"]).expanduser() / "sessions"
    return Path.home() / ".codex" / "sessions"


def _tmux_has_session(name: str) -> bool:
    try:
        subprocess.run(["tmux", "has-session", "-t", name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check tmux Codex worker health and log binding.")
    parser.add_argument("--session-file", default=os.environ.get("CCB_CODEX_SESSION_FILE", ".ccb-codex-session.json"))
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv[1:])

    session_file = Path(args.session_file).expanduser()
    session = _load_json(session_file) if session_file.exists() else {}

    tmux_session = os.environ.get("CCB_TMUX_SESSION") or session.get("tmux_session") or ""
    tmux_ok = bool(tmux_session) and _tmux_has_session(str(tmux_session))

    log_path_str = session.get("codex_session_path") if isinstance(session.get("codex_session_path"), str) else ""
    log_path = Path(log_path_str).expanduser() if log_path_str else None
    log_ok = bool(log_path and log_path.exists())

    status = {
        "tmux_session": tmux_session or None,
        "tmux_ok": tmux_ok,
        "log_path": str(log_path) if log_path else None,
        "log_ok": log_ok,
        "sessions_root": str(_sessions_root()),
    }

    if args.json:
        print(json.dumps(status, ensure_ascii=False))
    else:
        if tmux_ok:
            print(f"✅ tmux OK: {tmux_session}")
        else:
            print(f"❌ tmux not running: {tmux_session or '(unset)'}")
        if log_ok:
            print(f"✅ log OK: {log_path}")
        else:
            print("⚠️  log not bound (codex_session_path missing) or file not found.")

    return EXIT_OK if tmux_ok else EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv))
