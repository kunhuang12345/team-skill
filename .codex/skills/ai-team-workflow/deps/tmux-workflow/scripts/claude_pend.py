#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from claude_comm import latest_conversations, latest_message, resolve_log_path, strip_trailing_markers


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_REPLY = 2


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Print latest Claude replies from session logs.")
    parser.add_argument("n", nargs="?", default="1", help="Number of Q/A rounds to show (default: 1).")
    parser.add_argument("--session-file", default=os.environ.get("TWF_SESSION_FILE", ".claude-tmux-session.json"))
    parser.add_argument("--log", default=None, help="Force a specific Claude session .jsonl log file.")
    args = parser.parse_args(argv[1:])

    try:
        n = max(1, int(args.n))
    except ValueError:
        eprint("Usage: claude_pend.py [N]")
        return EXIT_ERROR

    session_file = Path(args.session_file).expanduser()
    session = _load_json(session_file) if session_file.exists() else {}

    log_path: Optional[Path]
    if args.log:
        log_path = Path(args.log).expanduser()
    else:
        log_path = resolve_log_path(session)

    if not log_path or not log_path.exists():
        eprint("❌ Claude session log not found.")
        if log_path:
            eprint(f"   expected: {log_path}")
        return EXIT_NO_REPLY

    if n == 1:
        msg = latest_message(log_path)
        if not msg:
            return EXIT_NO_REPLY
        print(strip_trailing_markers(msg))
        return EXIT_OK

    pairs = latest_conversations(log_path, n)
    if not pairs:
        return EXIT_NO_REPLY
    for i, (q, a) in enumerate(pairs, 1):
        print(f"[Q{i}]")
        print(q.rstrip())
        print()
        print(f"[A{i}]")
        print(strip_trailing_markers(a).rstrip())
        if i != len(pairs):
            print("\n---\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))

