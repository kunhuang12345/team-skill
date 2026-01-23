from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core import constants as C
from . import inbox
from ..infra import io as io_mod
from . import registry as registry_mod
from ..core import settings
from . import state_store
from ..core import util


_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _request_dir(team_dir: Path, *, request_id: str) -> Path:
    return state_store._requests_root(team_dir) / request_id.strip()


def _request_meta_path(team_dir: Path, *, request_id: str) -> Path:
    return _request_dir(team_dir, request_id=request_id) / C.REQUEST_META_FILE


def _request_responses_dir(team_dir: Path, *, request_id: str) -> Path:
    return _request_dir(team_dir, request_id=request_id) / C.REQUEST_RESPONSES_DIR


def _request_response_path(team_dir: Path, *, request_id: str, target_base: str) -> Path:
    request_id = request_id.strip()
    if not request_id:
        raise SystemExit("❌ request id missing")
    return _request_responses_dir(team_dir, request_id=request_id) / f"{util._slugify(target_base)}.md"


def _resolve_request_id(team_dir: Path, raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        raise SystemExit("❌ request id missing")
    if _REQUEST_ID_RE.match(s) and _request_dir(team_dir, request_id=s).is_dir():
        return s
    if s.isdigit():
        alt = f"req-{s}"
        if _request_dir(team_dir, request_id=alt).is_dir():
            return alt
    if s.startswith("req-") and s[4:].isdigit():
        alt2 = s[4:]
        if _request_dir(team_dir, request_id=alt2).is_dir():
            return alt2
    return s


def _list_request_ids(team_dir: Path) -> list[str]:
    root = state_store._requests_root(team_dir)
    if not root.is_dir():
        return []
    out: list[str] = []
    for p in root.iterdir():
        if p.is_dir():
            name = p.name.strip()
            if name:
                out.append(name)
    out.sort()
    return out


def _load_request_meta(team_dir: Path, *, request_id: str) -> dict[str, Any]:
    request_id = _resolve_request_id(team_dir, request_id)
    path = _request_meta_path(team_dir, request_id=request_id)
    data = io_mod._read_json(path) if path.is_file() else {}
    if not isinstance(data, dict) or not data:
        raise SystemExit(f"❌ request not found: {request_id}")
    data.setdefault("version", 1)
    data.setdefault("id", request_id)
    data.setdefault("created_at", "")
    data.setdefault("updated_at", "")
    status = str(data.get("status", "") or "").strip() or C.REQUEST_STATUS_OPEN
    if status not in {C.REQUEST_STATUS_OPEN, C.REQUEST_STATUS_DONE, C.REQUEST_STATUS_TIMED_OUT}:
        status = C.REQUEST_STATUS_OPEN
    data["status"] = status
    targets = data.get("targets")
    if not isinstance(targets, dict):
        data["targets"] = {}
    return data


def _update_request_meta(team_dir: Path, *, request_id: str, updater) -> dict[str, Any]:
    request_id = _resolve_request_id(team_dir, request_id)
    lock = team_dir / ".lock"
    with io_mod._locked(lock):
        state_store._ensure_share_layout(team_dir)
        path = _request_meta_path(team_dir, request_id=request_id)
        data = io_mod._read_json(path) if path.is_file() else {}
        if not isinstance(data, dict) or not data:
            raise SystemExit(f"❌ request not found: {request_id}")
        updater(data)
        data["updated_at"] = util._now()
        io_mod._write_json_atomic(path, data)
        return data


def _request_all_replied(meta: dict[str, Any]) -> bool:
    targets = meta.get("targets")
    if not isinstance(targets, dict) or not targets:
        return False
    for _k, t in targets.items():
        if not isinstance(t, dict):
            return False
        if str(t.get("status", "")).strip() != C.REQUEST_TARGET_STATUS_REPLIED:
            return False
    return True


def _render_request_result(team_dir: Path, meta: dict[str, Any], *, final_status: str) -> str:
    request_id = str(meta.get("id", "") or "").strip()
    topic = str(meta.get("topic", "") or "").strip()
    created_at = str(meta.get("created_at", "") or "").strip()
    deadline_at = str(meta.get("deadline_at", "") or "").strip()

    from_info = meta.get("from") if isinstance(meta.get("from"), dict) else {}
    from_base = str(from_info.get("base", "") or "").strip()
    from_role = str(from_info.get("role", "") or "").strip()
    from_full = str(from_info.get("full", "") or "").strip()

    meta_path = _request_meta_path(team_dir, request_id=request_id) if request_id else state_store._requests_root(team_dir)
    responses_dir = _request_responses_dir(team_dir, request_id=request_id) if request_id else state_store._requests_root(team_dir)

    lines: list[str] = []
    header = "[REPLY-NEEDED RESULT]"
    if final_status == C.REQUEST_STATUS_TIMED_OUT:
        header += " timed_out"
    lines.append(header)
    if request_id:
        lines.append(f"- request_id: {request_id}")
    if topic:
        lines.append(f"- topic: {topic}")
    if from_base or from_full:
        lines.append(f"- from: {from_base or from_full} (role={from_role or '?'})")
    if created_at:
        lines.append(f"- created_at: {created_at}")
    if deadline_at:
        lines.append(f"- deadline_at: {deadline_at}")
    lines.append(f"- meta: `{meta_path}`")
    lines.append(f"- responses: `{responses_dir}`")

    targets = meta.get("targets")
    if not isinstance(targets, dict) or not targets:
        lines.append("- targets: (none)")
        return "\n".join(lines).rstrip() + "\n"

    replied: list[str] = []
    pending: list[str] = []
    for base, t in targets.items():
        if not isinstance(t, dict):
            pending.append(str(base))
            continue
        role = str(t.get("role", "") or "").strip() or "?"
        st = str(t.get("status", "") or "").strip() or C.REQUEST_TARGET_STATUS_PENDING
        if st == C.REQUEST_TARGET_STATUS_REPLIED:
            resp_file = str(t.get("response_file", "") or "").strip()
            resp_note = f" file={resp_file}" if resp_file else ""
            replied.append(f"{base} (role={role}){resp_note}")
            continue
        blocked_until = str(t.get("blocked_until", "") or "").strip()
        waiting_on = str(t.get("waiting_on", "") or "").strip()
        extra: list[str] = []
        if blocked_until:
            extra.append(f"blocked_until={blocked_until}")
        if waiting_on:
            extra.append(f"waiting_on={waiting_on}")
        extra_s = (" " + " ".join(extra)) if extra else ""
        pending.append(f"{base} (role={role} status={st}{extra_s})")

    if replied:
        lines.append("")
        lines.append("Replied:")
        for item in replied:
            lines.append(f"- {item}")
    if pending:
        lines.append("")
        lines.append("Pending:")
        for item in pending:
            lines.append(f"- {item}")

    return "\n".join(lines).rstrip() + "\n"


def _scan_reply_requests(
    team_dir: Path,
    *,
    now_dt: datetime,
) -> tuple[list[tuple[str, str]], bool, list[tuple[str, str, str, str]], dict[str, int]]:
    finalizable: list[tuple[str, str]] = []
    has_pending = False
    due: list[tuple[str, str, str, str]] = []
    waiters: dict[str, int] = {}

    for req_id in _list_request_ids(team_dir):
        meta_path = _request_meta_path(team_dir, request_id=req_id)
        if not meta_path.is_file():
            continue
        meta = io_mod._read_json(meta_path)
        if not isinstance(meta, dict) or not meta:
            continue
        if str(meta.get("status", "")).strip() != C.REQUEST_STATUS_OPEN:
            continue

        targets = meta.get("targets")
        if not isinstance(targets, dict) or not targets:
            continue

        if _request_all_replied(meta):
            finalizable.append((req_id, C.REQUEST_STATUS_DONE))
            continue

        deadline_dt = util._parse_iso_dt(str(meta.get("deadline_at", "") or ""))
        if deadline_dt is not None and now_dt >= deadline_dt:
            finalizable.append((req_id, C.REQUEST_STATUS_TIMED_OUT))
            continue

        for base, t in targets.items():
            if not isinstance(t, dict):
                has_pending = True
                due.append((req_id, str(base), "?", C.REQUEST_TARGET_STATUS_PENDING))
                continue
            st = str(t.get("status", "") or "").strip() or C.REQUEST_TARGET_STATUS_PENDING
            if st == C.REQUEST_TARGET_STATUS_REPLIED:
                continue
            has_pending = True
            role = str(t.get("role", "") or "").strip() or "?"
            waiting_on = str(t.get("waiting_on", "") or "").strip()
            if waiting_on:
                waiters[waiting_on] = int(waiters.get(waiting_on, 0) or 0) + 1
            blocked_until = util._parse_iso_dt(str(t.get("blocked_until", "") or ""))
            if blocked_until is not None and now_dt < blocked_until:
                continue
            due.append((req_id, str(base), role, st))

    return finalizable, has_pending, due, waiters


def _finalize_request(
    team_dir: Path,
    registry_data: dict[str, Any],
    *,
    request_id: str,
    msg_id: str,
    final_status: str,
    now_iso: str,
) -> bool:
    if final_status not in {C.REQUEST_STATUS_DONE, C.REQUEST_STATUS_TIMED_OUT}:
        return False

    now_dt = util._parse_iso_dt(now_iso) or datetime.now()

    lock = team_dir / ".lock"
    with io_mod._locked(lock):
        meta_path = _request_meta_path(team_dir, request_id=request_id)
        meta = io_mod._read_json(meta_path) if meta_path.is_file() else {}
        if not isinstance(meta, dict) or not meta:
            return False
        if str(meta.get("status", "")).strip() != C.REQUEST_STATUS_OPEN:
            return False
        if str(meta.get("final_msg_id", "") or "").strip():
            return False

        all_replied = _request_all_replied(meta)
        deadline_dt = util._parse_iso_dt(str(meta.get("deadline_at", "") or ""))
        timed_out = deadline_dt is not None and now_dt >= deadline_dt and not all_replied

        if final_status == C.REQUEST_STATUS_DONE and not all_replied:
            return False
        if final_status == C.REQUEST_STATUS_TIMED_OUT and not timed_out:
            return False

        from_info = meta.get("from") if isinstance(meta.get("from"), dict) else {}
        to_base = str(from_info.get("base", "") or "").strip() or str(from_info.get("full", "") or "").strip()
        to_full = str(from_info.get("full", "") or "").strip()
        to_role = str(from_info.get("role", "") or "").strip() or "?"
        if to_base:
            m = registry_mod._resolve_member(registry_data, to_base) or {}
            to_full = str(m.get("full", "")).strip() or to_full or to_base
            to_role = str(m.get("role", "") or "").strip() or to_role

        if not to_base:
            return False

        body = _render_request_result(team_dir, meta, final_status=final_status)
        inbox._write_inbox_message_unlocked(
            team_dir,
            msg_id=msg_id,
            kind="reply-needed-result",
            from_full="atwf-reply",
            from_base="atwf-reply",
            from_role="system",
            to_full=to_full or to_base,
            to_base=to_base,
            to_role=to_role,
            body=body,
        )
        inbox._inbox_enforce_unread_limit_unlocked(
            team_dir,
            to_base=to_base,
            from_base="atwf-reply",
            max_unread=settings._inbox_max_unread_per_thread(),
        )

        meta["status"] = final_status
        meta["finalized_at"] = now_iso
        meta["final_msg_id"] = msg_id
        meta["updated_at"] = now_iso
        io_mod._write_json_atomic(meta_path, meta)
        return True
