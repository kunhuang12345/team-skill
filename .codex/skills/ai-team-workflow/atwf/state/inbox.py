from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core import constants as C
from ..infra import io as io_mod
from ..core import runtime
from ..core import settings
from . import state_store
from ..core import util


def _msg_seq_path(team_dir: Path) -> Path:
    return team_dir / C.MSG_SEQ_FILE


def _format_msg_id(n: int) -> str:
    return str(max(0, int(n))).zfill(C.MSG_ID_WIDTH)


def _next_msg_id(team_dir: Path) -> str:
    lock = team_dir / ".lock"
    seq_path = _msg_seq_path(team_dir)
    with io_mod._locked(lock):
        data = io_mod._read_json(seq_path)
        next_id_raw = data.get("next_id", 1)
        try:
            next_id = int(next_id_raw)
        except Exception:
            next_id = 1
        if next_id < 1:
            next_id = 1
        data.setdefault("created_at", util._now())
        data["updated_at"] = util._now()
        data["next_id"] = next_id + 1
        io_mod._write_json_atomic(seq_path, data)
    return _format_msg_id(next_id)


def _wrap_team_message(
    team_dir: Path,
    *,
    kind: str,
    sender_full: str,
    sender_role: str | None,
    to_full: str,
    body: str,
    msg_id: str | None = None,
) -> str:
    resolved_id = (msg_id or "").strip() or _next_msg_id(team_dir)
    kind_s = kind.strip() or "send"
    sender_full_s = sender_full.strip() or "unknown"
    to_full_s = to_full.strip() or "unknown"
    role_s = (sender_role or "").strip()
    role_part = f" role={role_s}" if role_s else ""
    header = f"[ATWF-MSG id={resolved_id} kind={kind_s} from={sender_full_s} to={to_full_s}{role_part} ts={util._now()}]"
    body_s = (body or "").rstrip()
    if body_s:
        return f"{header}\n{body_s}\n[ATWF-END id={resolved_id}]\n"
    return f"{header}\n[ATWF-END id={resolved_id}]\n"


def _inbox_notice(msg_id: str) -> str:
    msg_id = str(msg_id or "").strip()
    atwf_cmd = runtime._atwf_cmd()
    return (
        f"[INBOX] id={msg_id}\n"
        f"open (auto-read self): {atwf_cmd} inbox-open {msg_id}\n"
        f"ack (optional): {atwf_cmd} inbox-ack {msg_id}\n"
    )


def _inbox_member_dir(team_dir: Path, *, base: str) -> Path:
    return state_store._inbox_root(team_dir) / util._slugify(base)


def _inbox_thread_dir(team_dir: Path, *, to_base: str, from_base: str, state: str) -> Path:
    state = state.strip().lower()
    if state not in {C.INBOX_UNREAD_DIR, C.INBOX_READ_DIR, C.INBOX_OVERFLOW_DIR}:
        state = C.INBOX_UNREAD_DIR
    return _inbox_member_dir(team_dir, base=to_base) / state / f"from-{util._slugify(from_base)}"


def _inbox_message_path(team_dir: Path, *, to_base: str, from_base: str, state: str, msg_id: str) -> Path:
    return _inbox_thread_dir(team_dir, to_base=to_base, from_base=from_base, state=state) / f"{msg_id}.md"


def _inbox_summary(body: str) -> str:
    for line in (body or "").splitlines():
        s = line.strip()
        if s:
            return (s[:157] + "...") if len(s) > 160 else s
    return ""


def _inbox_unread_stats(team_dir: Path, *, to_base: str) -> tuple[int, int, list[str]]:
    base_dir = _inbox_member_dir(team_dir, base=to_base)
    unread_root = base_dir / C.INBOX_UNREAD_DIR
    overflow_root = base_dir / C.INBOX_OVERFLOW_DIR

    unread = 0
    overflow = 0
    ids: list[tuple[int, str]] = []

    if unread_root.is_dir():
        for from_dir in unread_root.glob("from-*"):
            if not from_dir.is_dir():
                continue
            for n, stem, _p in _inbox_list_msgs(from_dir):
                unread += 1
                ids.append((n, stem))

    if overflow_root.is_dir():
        for from_dir in overflow_root.glob("from-*"):
            if not from_dir.is_dir():
                continue
            overflow += len(_inbox_list_msgs(from_dir))

    ids.sort(key=lambda t: t[0])
    return unread, overflow, [stem for _n, stem in ids]


def _inbox_pending_min_id(team_dir: Path, *, to_base: str) -> tuple[int, str]:
    base_dir = _inbox_member_dir(team_dir, base=to_base)
    min_n: int | None = None
    min_s = ""

    for state in (C.INBOX_UNREAD_DIR, C.INBOX_OVERFLOW_DIR):
        root = base_dir / state
        if not root.is_dir():
            continue
        for from_dir in root.glob("from-*"):
            if not from_dir.is_dir():
                continue
            for p in from_dir.glob("*.md"):
                if not p.is_file():
                    continue
                stem = p.stem.strip()
                if not stem.isdigit():
                    continue
                try:
                    n = int(stem)
                except Exception:
                    continue
                if min_n is None or n < min_n:
                    min_n = n
                    min_s = stem

    if min_n is None:
        return 0, ""
    return int(min_n), min_s


def _inbox_message_created_at(team_dir: Path, *, to_base: str, msg_id: str) -> datetime | None:
    hit = _find_inbox_message_file(team_dir, to_base=to_base, msg_id=msg_id)
    if not hit:
        return None
    _state, _from_base, path = hit
    try:
        head = path.read_text(encoding="utf-8", errors="ignore").splitlines()[:40]
    except Exception:
        return None
    for line in head:
        s = line.strip()
        if s.startswith("- created_at:"):
            raw = s.split(":", 1)[1].strip()
            try:
                return datetime.fromisoformat(raw)
            except Exception:
                return None
    return None


def _inbox_list_msgs(dir_path: Path) -> list[tuple[int, str, Path]]:
    if not dir_path.is_dir():
        return []
    out: list[tuple[int, str, Path]] = []
    for p in dir_path.glob("*.md"):
        if not p.is_file():
            continue
        stem = p.stem.strip()
        if not stem.isdigit():
            continue
        try:
            n = int(stem)
        except Exception:
            continue
        out.append((n, stem, p))
    out.sort(key=lambda t: t[0])
    return out


def _inbox_enforce_unread_limit_unlocked(team_dir: Path, *, to_base: str, from_base: str, max_unread: int) -> None:
    if max_unread < 1:
        max_unread = 1
    unread_dir = _inbox_thread_dir(team_dir, to_base=to_base, from_base=from_base, state=C.INBOX_UNREAD_DIR)
    entries = _inbox_list_msgs(unread_dir)
    if len(entries) <= max_unread:
        return

    overflow_dir = _inbox_thread_dir(team_dir, to_base=to_base, from_base=from_base, state=C.INBOX_OVERFLOW_DIR)
    overflow_dir.mkdir(parents=True, exist_ok=True)

    excess = entries[: max(0, len(entries) - max_unread)]
    for _n, stem, p in excess:
        dst = overflow_dir / f"{stem}.md"
        try:
            p.replace(dst)
        except OSError:
            try:
                shutil.copy2(p, dst)
                p.unlink(missing_ok=True)  # type: ignore[call-arg]
            except Exception:
                pass


def _write_inbox_message_unlocked(
    team_dir: Path,
    *,
    msg_id: str,
    kind: str,
    from_full: str,
    from_base: str,
    from_role: str,
    to_full: str,
    to_base: str,
    to_role: str,
    body: str,
) -> Path:
    msg_id = msg_id.strip()
    if not msg_id:
        raise SystemExit("❌ inbox message id missing")

    kind_s = kind.strip() or "send"
    from_full_s = from_full.strip() or "unknown"
    from_base_s = from_base.strip() or from_full_s
    from_role_s = from_role.strip() or "?"
    to_full_s = to_full.strip() or "unknown"
    to_base_s = to_base.strip() or to_full_s
    to_role_s = to_role.strip() or "?"

    path = _inbox_message_path(
        team_dir,
        to_base=to_base_s,
        from_base=from_base_s,
        state=C.INBOX_UNREAD_DIR,
        msg_id=msg_id,
    )

    summary = _inbox_summary(body)
    meta_lines = [
        f"# ATWF Inbox Message {msg_id}",
        "",
        f"- id: `{msg_id}`",
        f"- kind: `{kind_s}`",
        f"- created_at: {util._now()}",
        f"- from: `{from_full_s}` (base `{from_base_s}` role `{from_role_s}`)",
        f"- to: `{to_full_s}` (base `{to_base_s}` role `{to_role_s}`)",
    ]
    if summary:
        meta_lines.append(f"- summary: {summary}")
    meta_lines.extend(["", "---", ""])

    body_s = (body or "").rstrip()
    payload = "\n".join(meta_lines) + (body_s + "\n" if body_s else "")
    io_mod._write_text_atomic(path, payload)
    return path


def _write_inbox_message(
    team_dir: Path,
    *,
    msg_id: str,
    kind: str,
    from_full: str,
    from_base: str,
    from_role: str,
    to_full: str,
    to_base: str,
    to_role: str,
    body: str,
) -> Path:
    lock = team_dir / ".lock"
    with io_mod._locked(lock):
        state_store._ensure_share_layout(team_dir)
        path = _write_inbox_message_unlocked(
            team_dir,
            msg_id=msg_id,
            kind=kind,
            from_full=from_full,
            from_base=from_base,
            from_role=from_role,
            to_full=to_full,
            to_base=to_base,
            to_role=to_role,
            body=body,
        )
        _inbox_enforce_unread_limit_unlocked(
            team_dir,
            to_base=to_base,
            from_base=from_base,
            max_unread=settings._inbox_max_unread_per_thread(),
        )
        return path


def _find_inbox_message_file(team_dir: Path, *, to_base: str, msg_id: str) -> tuple[str, str, Path] | None:
    base_dir = _inbox_member_dir(team_dir, base=to_base)
    msg_id = msg_id.strip()
    if not msg_id:
        return None
    for state in (C.INBOX_UNREAD_DIR, C.INBOX_OVERFLOW_DIR, C.INBOX_READ_DIR):
        state_dir = base_dir / state
        if not state_dir.is_dir():
            continue
        for from_dir in state_dir.glob("from-*"):
            if not from_dir.is_dir():
                continue
            p = from_dir / f"{msg_id}.md"
            if p.is_file():
                from_base = from_dir.name[len("from-") :]
                return state, from_base, p
    return None


def _mark_inbox_read(team_dir: Path, *, to_base: str, msg_id: str) -> Path | None:
    to_base = to_base.strip()
    msg_id = msg_id.strip()
    if not to_base or not msg_id:
        return None

    lock = team_dir / ".lock"
    with io_mod._locked(lock):
        hit = _find_inbox_message_file(team_dir, to_base=to_base, msg_id=msg_id)
        if not hit:
            return None
        state, from_base, src = hit
        if state == C.INBOX_READ_DIR:
            return src

        dst = _inbox_message_path(
            team_dir,
            to_base=to_base,
            from_base=from_base,
            state=C.INBOX_READ_DIR,
            msg_id=msg_id,
        )
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            src.replace(dst)
        except OSError:
            try:
                shutil.copy2(src, dst)
                src.unlink(missing_ok=True)  # type: ignore[call-arg]
            except Exception:
                pass
        return dst if dst.is_file() else None
