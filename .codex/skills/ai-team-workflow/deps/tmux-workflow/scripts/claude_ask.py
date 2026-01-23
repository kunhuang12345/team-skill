#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from claude_comm import (
    DONE_PREFIX,
    REQ_ID_PREFIX,
    is_done_text,
    iter_events,
    make_req_id,
    resolve_log_path,
    strip_done_text,
    wrap_prompt,
)


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_TIMEOUT = 2


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _env_float(*names: str, default: float) -> float:
    for name in names:
        raw = os.environ.get(name)
        if raw is None:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        return max(0.0, value)
    return max(0.0, default)


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _tmux_cmd(args: list[str], *, input_text: Optional[str] = None) -> None:
    data = input_text.encode("utf-8") if input_text is not None else None
    subprocess.run(["tmux", *args], input=data, check=True)


def _resolve_pane_target(tmux_target: str) -> str:
    target = (tmux_target or "").strip()
    if not target:
        raise RuntimeError("tmux target is empty")
    if target.startswith("%"):
        return target
    # assume <session>:<window>.<pane> or <session>
    if ":" in target and "." in target:
        return target
    try:
        pane_id = subprocess.check_output(["tmux", "display-message", "-p", "-t", target, "#{pane_id}"]).decode().strip()
        return pane_id if pane_id else target
    except Exception:
        return target


def _send_enter(tmux_target: str) -> None:
    resolved = _resolve_pane_target(tmux_target)
    _tmux_cmd(["send-keys", "-t", resolved, "C-m"])


def _inject_text(tmux_target: str, text: str, *, submit_delay_s: float) -> None:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    resolved = _resolve_pane_target(tmux_target)
    if "\n" in text or len(text) > 200:
        buf = f"twf-claude-ask-{os.getpid()}"
        _tmux_cmd(["load-buffer", "-b", buf, "-"], input_text=text)
        try:
            _tmux_cmd(["paste-buffer", "-t", resolved, "-b", buf, "-r"])
        finally:
            subprocess.run(["tmux", "delete-buffer", "-b", buf], check=False)
    else:
        _tmux_cmd(["send-keys", "-t", resolved, "-l", text])

    if submit_delay_s > 0:
        time.sleep(submit_delay_s)
    _send_enter(tmux_target)


def _read_message_from_stdin() -> str:
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Ask a Claude tmux worker and wait for the next reply.")
    parser.add_argument("message", nargs="?", default="", help="message to send (or read stdin)")
    parser.add_argument("--session-file", default=os.environ.get("TWF_SESSION_FILE", ".claude-tmux-session.json"))
    parser.add_argument("--send-only", action="store_true", help="Send prompt but do not wait for reply.")
    parser.add_argument("--timeout", type=float, default=_env_float("TWF_TIMEOUT", default=3600.0))
    parser.add_argument("--poll", type=float, default=_env_float("TWF_POLL_INTERVAL", "CLAUDE_POLL_INTERVAL", default=0.05))
    parser.add_argument("--submit-delay", type=float, default=_env_float("TWF_SUBMIT_DELAY", default=0.2))
    args = parser.parse_args(argv[1:])

    message = (args.message or "").strip()
    if not message:
        message = (_read_message_from_stdin() or "").strip()
    if not message:
        eprint("❌ empty message")
        return EXIT_ERROR

    session_file = Path(args.session_file).expanduser()
    session = _load_json(session_file) if session_file.exists() else {}

    tmux_target = str(session.get("tmux_target") or "").strip()
    if not tmux_target:
        tmux_session = str(session.get("tmux_session") or "").strip() or session_file.stem
        tmux_target = f"{tmux_session}:0.0"

    def resolve_log(current: Optional[Path] = None) -> Optional[Path]:
        lp = resolve_log_path(session)
        if lp and (current is None or lp != current):
            session["claude_session_path"] = str(lp)
            if session_file.exists():
                _atomic_write_json(session_file, session)
        return lp

    log_path = resolve_log()

    if args.send_only:
        _inject_text(tmux_target, message, submit_delay_s=args.submit_delay)
        return EXIT_OK

    req_id = make_req_id()
    prompt = wrap_prompt(message, req_id)

    state_offset = 0
    state_carry = b""
    if log_path and log_path.exists():
        try:
            state_offset = log_path.stat().st_size
        except OSError:
            state_offset = 0

    _inject_text(tmux_target, prompt, submit_delay_s=args.submit_delay)

    deadline = time.time() + max(0.0, float(args.timeout))
    anchor = f"{REQ_ID_PREFIX} {req_id}"
    done_seen = False
    anchor_seen = False
    chunks: list[str] = []

    while True:
        if time.time() >= deadline:
            break
        if not log_path or not log_path.exists():
            new_path = resolve_log(log_path)
            if new_path and new_path != log_path:
                log_path = new_path
                state_offset = 0
                state_carry = b""
            time.sleep(args.poll)
            continue

        events, state_offset, state_carry = iter_events(log_path, offset=state_offset, carry=state_carry)
        if not events:
            time.sleep(args.poll)
            continue

        for role, text in events:
            if role == "user":
                if anchor in text:
                    anchor_seen = True
                continue
            if role != "assistant":
                continue
            if not anchor_seen:
                continue
            chunks.append(text)
            combined = "\n".join(chunks)
            if is_done_text(combined, req_id):
                done_seen = True
                reply = strip_done_text(combined, req_id)
                print(reply)
                return EXIT_OK

    combined = "\n".join(chunks).strip()
    if combined:
        print(strip_done_text(combined, req_id).strip())
        return EXIT_TIMEOUT if not done_seen else EXIT_OK

    eprint("❌ timeout waiting for reply")
    eprint(f"   expected done marker: {DONE_PREFIX} {req_id}")
    return EXIT_TIMEOUT


if __name__ == "__main__":
    sys.exit(main(sys.argv))
