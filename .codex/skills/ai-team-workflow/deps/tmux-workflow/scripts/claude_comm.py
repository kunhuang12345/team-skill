#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple


REQ_ID_PREFIX = "TWF_REQ_ID:"
DONE_PREFIX = "TWF_DONE:"


def make_req_id() -> str:
    return secrets.token_hex(16)


def wrap_prompt(message: str, req_id: str) -> str:
    message = (message or "").rstrip()
    return (
        f"{REQ_ID_PREFIX} {req_id}\n\n"
        f"{message}\n\n"
        "IMPORTANT:\n"
        "- Reply normally.\n"
        "- End your reply with this exact final line (verbatim, on its own line):\n"
        f"{DONE_PREFIX} {req_id}\n"
    )


_DONE_RE_TEMPLATE = r"^\s*TWF_DONE:\s*{req_id}\s*$"


def _done_line_re(req_id: str) -> re.Pattern[str]:
    return re.compile(_DONE_RE_TEMPLATE.format(req_id=re.escape(req_id)))


def is_done_text(text: str, req_id: str) -> bool:
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    for i in range(len(lines) - 1, -1, -1):
        if not lines[i].strip():
            continue
        return bool(_done_line_re(req_id).match(lines[i]))
    return False


def strip_done_text(text: str, req_id: str) -> str:
    lines = [ln.rstrip("\n") for ln in (text or "").splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and _done_line_re(req_id).match(lines[-1] or ""):
        lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).rstrip()


def strip_trailing_markers(text: str) -> str:
    lines = [ln.rstrip("\n") for ln in (text or "").splitlines()]
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        if last.startswith(DONE_PREFIX):
            lines.pop()
            continue
        break
    return "\n".join(lines).rstrip()


def _project_key_for_path(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _candidate_project_dirs(projects_root: Path, work_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    env_pwd = os.environ.get("PWD")
    if env_pwd:
        try:
            candidates.append(Path(env_pwd))
        except Exception:
            pass
    candidates.append(work_dir)
    try:
        candidates.append(work_dir.resolve())
    except Exception:
        pass

    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _project_key_for_path(candidate)
        if key in seen:
            continue
        seen.add(key)
        out.append(projects_root / key)
    return out


def resolve_log_path(session: dict[str, Any]) -> Optional[Path]:
    sid = str(session.get("claude_session_id") or "").strip()
    if not sid:
        return None
    bound = session.get("claude_session_path")
    if isinstance(bound, str) and bound.strip():
        bound_path = Path(bound).expanduser()
        if bound_path.exists():
            return bound_path

    cfg_dir = session.get("claude_config_dir")
    if isinstance(cfg_dir, str) and cfg_dir.strip():
        config_dir = Path(cfg_dir).expanduser()
    else:
        config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")).expanduser()

    projects_root = config_dir / "projects"

    key = session.get("claude_project_key")
    preferred_dirs: list[Path] = []
    if isinstance(key, str) and key.strip():
        preferred_dirs.append(projects_root / key.strip())

    wd_raw = session.get("work_dir_norm") if isinstance(session.get("work_dir_norm"), str) else None
    if not wd_raw:
        wd_raw = session.get("work_dir") if isinstance(session.get("work_dir"), str) else None
    if wd_raw:
        work_dir = Path(wd_raw).expanduser()
    else:
        work_dir = Path.cwd()

    candidates = preferred_dirs + _candidate_project_dirs(projects_root, work_dir)
    fallback: Optional[Path] = None
    for project_dir in candidates:
        candidate = project_dir / f"{sid}.jsonl"
        if fallback is None:
            fallback = candidate
        if candidate.exists():
            return candidate

    # Fallback: scan for the session file (depth is exactly 2: projects/<key>/<sid>.jsonl).
    best: Optional[Path] = None
    best_mtime = -1.0
    try:
        for p in projects_root.glob(f"*/{sid}.jsonl"):
            if not p.is_file():
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            if mtime >= best_mtime:
                best = p
                best_mtime = mtime
    except OSError:
        best = None

    return best or fallback


def _extract_content_text(content: Any) -> Optional[str]:
    if content is None:
        return None
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None

    texts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type in {"thinking", "thinking_delta", "tool_use", "tool_use_delta", "tool_result"}:
            continue
        text = item.get("text")
        if not text and item_type == "text":
            text = item.get("content")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    if not texts:
        return None
    return "\n".join(texts).strip()


def extract_message(entry: Any, role: str) -> Optional[str]:
    if not isinstance(entry, dict):
        return None
    entry_type = str(entry.get("type") or "").strip().lower()

    if entry_type == "response_item":
        payload = entry.get("payload")
        if not isinstance(payload, dict) or str(payload.get("type") or "").strip().lower() != "message":
            return None
        if str(payload.get("role") or "").strip().lower() != role:
            return None
        return _extract_content_text(payload.get("content"))

    if entry_type == "event_msg":
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            return None
        payload_type = str(payload.get("type") or "").strip().lower()
        if payload_type in {"agent_message", "assistant_message", "assistant"}:
            if str(payload.get("role") or "").strip().lower() != role:
                return None
            msg = payload.get("message") or payload.get("content") or payload.get("text")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
        return None

    msg = entry.get("message")
    if isinstance(msg, dict):
        msg_role = str(msg.get("role") or entry_type).strip().lower()
        if msg_role != role:
            return None
        return _extract_content_text(msg.get("content"))

    if entry_type != role:
        return None
    return _extract_content_text(entry.get("content"))


def iter_events(path: Path, *, offset: int, carry: bytes) -> Tuple[list[tuple[str, str]], int, bytes]:
    try:
        size = path.stat().st_size
    except OSError:
        return [], offset, carry

    if size < offset:
        offset = 0
        carry = b""

    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except OSError:
        return [], offset, carry

    new_offset = offset + len(data)
    buf = carry + data
    lines = buf.split(b"\n")
    if buf and not buf.endswith(b"\n"):
        carry = lines.pop()
    else:
        carry = b""

    events: list[tuple[str, str]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            entry = json.loads(line.decode("utf-8", errors="replace"))
        except Exception:
            continue
        if not isinstance(entry, dict):
            continue
        user = extract_message(entry, "user")
        if user:
            events.append(("user", user))
            continue
        assistant = extract_message(entry, "assistant")
        if assistant:
            events.append(("assistant", assistant))
    return events, new_offset, carry


def latest_conversations(path: Path, n: int) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    last_user: str | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                user = extract_message(entry, "user")
                if user:
                    last_user = user
                    continue
                assistant = extract_message(entry, "assistant")
                if assistant:
                    pairs.append((last_user or "", assistant))
                    last_user = None
    except OSError:
        return []
    return pairs[-max(1, int(n)) :]


def latest_message(path: Path) -> Optional[str]:
    last: Optional[str] = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                msg = extract_message(entry, "assistant")
                if msg:
                    last = msg
    except OSError:
        return None
    return last


__all__ = [
    "DONE_PREFIX",
    "REQ_ID_PREFIX",
    "extract_message",
    "is_done_text",
    "iter_events",
    "latest_conversations",
    "latest_message",
    "make_req_id",
    "resolve_log_path",
    "strip_done_text",
    "strip_trailing_markers",
    "wrap_prompt",
]
