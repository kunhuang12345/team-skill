#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple


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


def _realpath(path: Path) -> str:
    return str(path.expanduser().resolve())


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


def _sessions_root() -> Path:
    if os.environ.get("TWF_CODEX_SESSION_ROOT"):
        return Path(os.environ["TWF_CODEX_SESSION_ROOT"]).expanduser()
    if os.environ.get("CODEX_SESSION_ROOT"):
        return Path(os.environ["CODEX_SESSION_ROOT"]).expanduser()
    if os.environ.get("CODEX_HOME"):
        return Path(os.environ["CODEX_HOME"]).expanduser() / "sessions"
    return Path.home() / ".codex" / "sessions"


def _sessions_root_for_session(session: Dict[str, Any]) -> Path:
    value = session.get("codex_session_root")
    if isinstance(value, str) and value.strip():
        return Path(value).expanduser()

    codex_home = session.get("codex_home")
    if isinstance(codex_home, str) and codex_home.strip():
        return Path(codex_home).expanduser() / "sessions"

    return _sessions_root()


def _parse_ts(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str):
        return None
    # e.g. "2025-12-22T02:55:11.921Z"
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _extract_assistant_text(entry: Dict[str, Any]) -> Optional[str]:
    entry_type = entry.get("type")
    payload = entry.get("payload") or {}
    if not isinstance(payload, dict):
        return None

    if entry_type == "event_msg" and payload.get("type") == "agent_message":
        msg = payload.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
        return None

    if entry_type != "response_item":
        return None
    if payload.get("type") != "message":
        return None
    if payload.get("role") != "assistant":
        return None

    content = payload.get("content") or []
    if isinstance(content, list):
        texts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "output_text":
                text = item.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
        if texts:
            return "\n".join(texts).strip()

    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None


def _extract_user_text(entry: Dict[str, Any]) -> Optional[str]:
    entry_type = entry.get("type")
    payload = entry.get("payload") or {}
    if not isinstance(payload, dict):
        return None

    if entry_type == "event_msg" and payload.get("type") == "user_message":
        msg = payload.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
        return None

    if entry_type == "response_item" and payload.get("type") == "message" and payload.get("role") == "user":
        content = payload.get("content") or []
        if isinstance(content, list):
            texts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "input_text":
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        texts.append(text)
            if texts:
                return "\n".join(texts).strip()
    return None


def _extract_session_meta(entry: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    if entry.get("type") != "session_meta":
        return None, None
    payload = entry.get("payload") or {}
    if not isinstance(payload, dict):
        return None, None
    session_id = payload.get("id")
    cwd = payload.get("cwd")
    return (session_id if isinstance(session_id, str) else None, cwd if isinstance(cwd, str) else None)


def _read_first_lines(path: Path, limit: int = 50) -> Iterable[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for _ in range(limit):
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    yield entry
    except OSError:
        return


def _find_log_for_cwd(expected_cwd_norm: str) -> Optional[Path]:
    root = _sessions_root()
    if not root.exists():
        return None

    best: Optional[Path] = None
    best_mtime = -1.0

    for path in root.glob("**/*.jsonl"):
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < best_mtime:
            continue

        found_cwd = None
        for entry in _read_first_lines(path, limit=5):
            _sid, cwd = _extract_session_meta(entry)
            if cwd:
                found_cwd = cwd
                break
        if not found_cwd:
            continue

        try:
            found_norm = _realpath(Path(found_cwd))
        except Exception:
            found_norm = found_cwd
        if found_norm != expected_cwd_norm:
            continue

        best = path
        best_mtime = mtime

    return best


def _scan_latest_log(root: Path) -> Optional[Path]:
    if not root.exists():
        return None

    latest: Optional[Path] = None
    latest_mtime = -1.0

    for path in root.glob("**/*.jsonl"):
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= latest_mtime:
            latest = path
            latest_mtime = mtime

    return latest


def _tmux_cmd(args: list[str], *, input_text: Optional[str] = None) -> None:
    data = input_text.encode("utf-8") if input_text is not None else None
    subprocess.run(["tmux", *args], input=data, check=True)


def _inject_text(tmux_target: str, text: str, *, submit_delay_s: float) -> None:
    text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    # Use buffer paste for large/multiline to avoid argv limits.
    if "\n" in text or len(text) > 200:
        buf = f"twf-ask-{os.getpid()}"
        _tmux_cmd(["load-buffer", "-b", buf, "-"], input_text=text)
        try:
            # -p: wrap in bracketed paste (if supported by app); -r: keep LF as-is.
            _tmux_cmd(["paste-buffer", "-t", tmux_target, "-b", buf, "-p", "-r"])
        finally:
            subprocess.run(["tmux", "delete-buffer", "-b", buf], check=False)
    else:
        _tmux_cmd(["send-keys", "-t", tmux_target, "-l", text])

    if submit_delay_s > 0:
        time.sleep(submit_delay_s)
    _tmux_cmd(["send-keys", "-t", tmux_target, "Enter"])


def _poll_for_reply(
    log_path: Path,
    offset: int,
    *,
    allow_rescan: bool,
    per_worker_root: bool,
    sessions_root: Path,
    expected_cwd_norm: str,
    sent_after_utc: datetime,
    timeout_s: float,
    poll_s: float,
    tmux_target: Optional[str],
    submit_nudge_after_s: float,
    submit_nudge_max: int,
) -> Tuple[Optional[str], Path, int]:
    deadline = time.time() + timeout_s
    current_path = log_path
    current_offset = offset
    last_rescan = time.time()
    rescan_interval = min(2.0, max(0.2, timeout_s / 2.0 if timeout_s > 0 else 0.2))
    saw_user = False
    submit_nudges = 0
    inject_started = time.time()

    while True:
        if time.time() >= deadline:
            return None, current_path, current_offset

        try:
            size = current_path.stat().st_size
            if current_offset > size:
                current_offset = size
        except OSError:
            pass

        try:
            with current_path.open("rb") as f:
                f.seek(max(0, current_offset))
                while True:
                    if time.time() >= deadline:
                        return None, current_path, current_offset

                    pos_before = f.tell()
                    raw = f.readline()
                    if not raw:
                        break

                    if not raw.endswith(b"\n"):
                        f.seek(pos_before)
                        break

                    current_offset = f.tell()
                    line = raw.decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict):
                        continue

                    ts = _parse_ts(entry.get("timestamp"))
                    if ts is not None and ts < sent_after_utc:
                        continue

                    if not saw_user and _extract_user_text(entry) is not None:
                        saw_user = True

                    msg = _extract_assistant_text(entry)
                    if msg is not None:
                        return msg, current_path, current_offset
        except OSError:
            pass

        now = time.time()
        if allow_rescan and now - last_rescan >= rescan_interval:
            if per_worker_root:
                candidate = _scan_latest_log(sessions_root)
            else:
                candidate = _find_log_for_cwd(expected_cwd_norm)
            if candidate and candidate.exists() and candidate != current_path:
                current_path = candidate
                current_offset = 0
            last_rescan = now

        if (
            tmux_target
            and not saw_user
            and submit_nudges < submit_nudge_max
            and time.time() - inject_started >= submit_nudge_after_s * (submit_nudges + 1)
        ):
            try:
                # Some Codex TUI states (startup/paste-burst) may require an extra submit keypress.
                _tmux_cmd(["send-keys", "-t", tmux_target, "Enter"])
                submit_nudges += 1
            except Exception:
                pass

        time.sleep(poll_s)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Send a prompt to Codex running in tmux and wait for the next reply from Codex session logs.")
    parser.add_argument("text", nargs="?", help="Prompt text. If omitted, read from stdin.")
    parser.add_argument("--session-file", default=os.environ.get("TWF_SESSION_FILE", ".codex-tmux-session.json"))
    parser.add_argument("--log", default=None, help="Force a specific Codex session .jsonl log file.")
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("TWF_TIMEOUT", "3600")))
    parser.add_argument("--poll", type=float, default=float(os.environ.get("TWF_POLL_INTERVAL", "0.05")))
    parser.add_argument("--no-write-session", action="store_true", help="Do not update session file with discovered log binding.")
    args = parser.parse_args(argv[1:])

    text = args.text
    if text is None:
        text = sys.stdin.read()
    if not isinstance(text, str) or not text.strip():
        eprint("❌ Empty prompt.")
        return EXIT_ERROR

    session_file = Path(args.session_file).expanduser()
    session = _load_json(session_file) if session_file.exists() else {}

    tmux_target = (
        os.environ.get("TWF_TMUX_TARGET")
        or session.get("tmux_target")
        or session.get("pane_id")
        or session.get("tmux_session")
    )
    if not tmux_target:
        eprint(f"❌ tmux target not configured. Run codex_up_tmux.sh first to create {session_file}.")
        return EXIT_ERROR

    expected_cwd_norm = _realpath(Path.cwd())

    per_worker_root = bool(session.get("codex_home") or session.get("codex_session_root"))
    sessions_root = _sessions_root_for_session(session)

    log_path: Optional[Path]
    if args.log:
        log_path = Path(args.log).expanduser()
    else:
        bound = session.get("codex_session_path")
        log_path = Path(bound).expanduser() if isinstance(bound, str) and bound else None

    if not log_path or not log_path.exists():
        if per_worker_root:
            log_path = _scan_latest_log(sessions_root)
        else:
            log_path = _find_log_for_cwd(expected_cwd_norm) or _scan_latest_log(sessions_root)

    # If we just started a worker, the first session log may take a moment to appear.
    if (not args.log) and (not log_path or not log_path.exists()):
        wait_s = min(10.0, max(0.0, args.timeout))
        deadline = time.time() + wait_s
        while time.time() < deadline:
            time.sleep(min(0.5, max(0.05, args.poll)))
            if per_worker_root:
                log_path = _scan_latest_log(sessions_root)
            else:
                log_path = _find_log_for_cwd(expected_cwd_norm) or _scan_latest_log(sessions_root)
            if log_path and log_path.exists():
                break

    if not log_path or not log_path.exists():
        eprint(f"❌ Codex session log not found under {sessions_root}. Start Codex first, then retry.")
        return EXIT_ERROR

    try:
        offset = log_path.stat().st_size
    except OSError:
        offset = 0

    sent_after = datetime.now(timezone.utc) - timedelta(seconds=0.5)
    try:
        submit_delay = _env_float(
            "TWF_SUBMIT_DELAY",
            "TWF_TMUX_ENTER_DELAY",
            default=0.5,
        )
        _inject_text(str(tmux_target), text, submit_delay_s=submit_delay)
    except subprocess.CalledProcessError as exc:
        eprint(f"❌ tmux injection failed: {exc}")
        return EXIT_ERROR

    submit_nudge_after = float(os.environ.get("TWF_SUBMIT_NUDGE_AFTER", "0.7"))
    submit_nudge_max = int(os.environ.get("TWF_SUBMIT_NUDGE_MAX", "2"))
    if submit_nudge_after < 0:
        submit_nudge_after = 0.0
    submit_nudge_max = max(0, submit_nudge_max)

    reply, used_log_path, _new_offset = _poll_for_reply(
        log_path,
        offset,
        allow_rescan=args.log is None,
        per_worker_root=per_worker_root,
        sessions_root=sessions_root,
        expected_cwd_norm=expected_cwd_norm,
        sent_after_utc=sent_after,
        timeout_s=max(0.0, args.timeout),
        poll_s=min(0.5, max(0.01, args.poll)),
        tmux_target=str(tmux_target) if tmux_target else None,
        submit_nudge_after_s=min(submit_nudge_after, max(0.0, args.timeout)),
        submit_nudge_max=submit_nudge_max,
    )

    if reply is None:
        eprint("⏳ Timeout: no reply.")
        return EXIT_TIMEOUT

    if not args.no_write_session:
        try:
            data = _load_json(session_file) if session_file.exists() else {}
            data["codex_session_path"] = str(used_log_path)
            # Bind session_id if available (read from header)
            for entry in _read_first_lines(used_log_path, limit=10):
                sid, cwd = _extract_session_meta(entry)
                if sid:
                    data["codex_session_id"] = sid
                    data["codex_current_id"] = sid
                    data["codex_resume_from_id"] = sid
                if cwd:
                    data["work_dir"] = cwd
                    try:
                        data["work_dir_norm"] = _realpath(Path(cwd))
                    except Exception:
                        pass
                if sid or cwd:
                    break
            _atomic_write_json(session_file, data)
        except Exception:
            pass

    sys.stdout.write(reply)
    if not reply.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))
