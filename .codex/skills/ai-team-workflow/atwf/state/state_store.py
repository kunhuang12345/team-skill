from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core import constants as C
from ..infra import io as io_mod
from ..core import runtime
from ..core import settings
from ..core import util


def _inbox_root(team_dir: Path) -> Path:
    return team_dir / C.INBOX_DIR


def _requests_root(team_dir: Path) -> Path:
    return team_dir / C.REQUESTS_DIR


def _state_root(team_dir: Path) -> Path:
    return team_dir / C.STATE_DIR


def _state_lock_path(team_dir: Path) -> Path:
    return _state_root(team_dir) / ".lock"


def _reply_drive_state_path(team_dir: Path) -> Path:
    return _state_root(team_dir) / "reply_drive.json"


def _task_path(team_dir: Path) -> Path:
    return team_dir / "task.md"


def _design_dir(team_dir: Path) -> Path:
    return team_dir / "design"


def _design_summary_path(team_dir: Path) -> Path:
    return team_dir / "design.md"


def _ops_dir(team_dir: Path) -> Path:
    return team_dir / "ops"


def _ops_env_notes_path(team_dir: Path) -> Path:
    return _ops_dir(team_dir) / "env.md"


def _ops_host_deps_path(team_dir: Path) -> Path:
    return _ops_dir(team_dir) / "host-deps.md"


def _design_member_path(team_dir: Path, full: str) -> Path:
    safe = full.strip()
    if not safe:
        raise ValueError("full is required")
    return _design_dir(team_dir) / f"{safe}.md"


def _ensure_share_layout(team_dir: Path) -> None:
    team_dir.mkdir(parents=True, exist_ok=True)
    _design_dir(team_dir).mkdir(parents=True, exist_ok=True)
    _ops_dir(team_dir).mkdir(parents=True, exist_ok=True)
    _inbox_root(team_dir).mkdir(parents=True, exist_ok=True)
    _requests_root(team_dir).mkdir(parents=True, exist_ok=True)
    _state_root(team_dir).mkdir(parents=True, exist_ok=True)


def _agent_state_path(team_dir: Path, *, full: str) -> Path:
    return _state_root(team_dir) / f"{util._slugify(full)}.json"


def _drive_state_path(team_dir: Path) -> Path:
    return _state_root(team_dir) / "drive.json"


def _default_drive_state(*, mode: str) -> dict[str, Any]:
    return {
        "version": 1,
        "created_at": util._now(),
        "updated_at": util._now(),
        "mode": settings._normalize_drive_mode(mode) if mode else C.DRIVE_MODE_DEFAULT,
        "last_triggered_at": "",
        "last_msg_id": "",
        "last_reason": "",
        "last_driver_full": "",
    }


def _load_drive_state_unlocked(team_dir: Path, *, mode_default: str) -> dict[str, Any]:
    path = _drive_state_path(team_dir)
    data = io_mod._read_json(path) if path.is_file() else {}
    if not data:
        data = _default_drive_state(mode=mode_default)
        io_mod._write_json_atomic(path, data)
        return data

    data.setdefault("version", 1)
    data.setdefault("created_at", util._now())
    data["updated_at"] = util._now()
    data.setdefault("last_triggered_at", "")
    data.setdefault("last_msg_id", "")
    data.setdefault("last_reason", "")
    data.setdefault("last_driver_full", "")

    mode = settings._normalize_drive_mode(str(mode_default or ""))
    if mode not in C.DRIVE_MODES:
        mode = C.DRIVE_MODE_DEFAULT
    data["mode"] = mode
    return data


def _write_drive_state(team_dir: Path, *, update: dict[str, Any]) -> dict[str, Any]:
    lock = _state_lock_path(team_dir)
    with io_mod._locked(lock):
        _ensure_share_layout(team_dir)
        data = _load_drive_state_unlocked(team_dir, mode_default=settings._drive_mode_config_hot())
        for k, v in update.items():
            data[k] = v
        data["updated_at"] = util._now()
        io_mod._write_json_atomic(_drive_state_path(team_dir), data)
        return data


def _drive_subtree_state_path(team_dir: Path) -> Path:
    return _state_root(team_dir) / C.DRIVE_SUBTREE_STATE_FILE


def _default_drive_subtree_state(*, mode: str) -> dict[str, Any]:
    return {
        "version": 1,
        "created_at": util._now(),
        "updated_at": util._now(),
        "mode": settings._normalize_drive_mode(mode) if mode else C.DRIVE_MODE_DEFAULT,
        "subtrees": {},
    }


def _drive_subtree_entry(state: dict[str, Any], *, base: str) -> dict[str, Any]:
    base = (base or "").strip()
    if not base:
        return {}
    subs = state.get("subtrees")
    if not isinstance(subs, dict):
        subs = {}
        state["subtrees"] = subs
    raw = subs.get(base)
    if not isinstance(raw, dict):
        raw = {}
        subs[base] = raw
    raw.setdefault("base", base)
    status = str(raw.get("status", "") or "").strip().lower()
    if status not in C.DRIVE_SUBTREE_STATUSES:
        status = C.DRIVE_SUBTREE_STATUS_ACTIVE
    raw["status"] = status
    raw.setdefault("stopped_at", "")
    raw.setdefault("stopped_reason", "")
    raw.setdefault("last_triggered_at", "")
    raw.setdefault("last_msg_id", "")
    raw.setdefault("last_reason", "")
    return raw


def _load_drive_subtree_state_unlocked(team_dir: Path, *, mode_default: str) -> dict[str, Any]:
    path = _drive_subtree_state_path(team_dir)
    data = io_mod._read_json(path) if path.is_file() else {}
    if not data:
        data = _default_drive_subtree_state(mode=mode_default)
        io_mod._write_json_atomic(path, data)
        return data

    data.setdefault("version", 1)
    data.setdefault("created_at", util._now())
    data["updated_at"] = util._now()
    data.setdefault("subtrees", {})

    mode = settings._normalize_drive_mode(str(mode_default or ""))
    if mode not in C.DRIVE_MODES:
        mode = C.DRIVE_MODE_DEFAULT
    data["mode"] = mode

    subs = data.get("subtrees")
    if isinstance(subs, dict):
        for k in list(subs.keys()):
            base = str(k or "").strip()
            if not base:
                subs.pop(k, None)
                continue
            entry = subs.get(k)
            if isinstance(entry, dict):
                entry.setdefault("base", base)
                status = str(entry.get("status", "") or "").strip().lower()
                if status not in C.DRIVE_SUBTREE_STATUSES:
                    status = C.DRIVE_SUBTREE_STATUS_ACTIVE
                entry["status"] = status
                entry.setdefault("stopped_at", "")
                entry.setdefault("stopped_reason", "")
                entry.setdefault("last_triggered_at", "")
                entry.setdefault("last_msg_id", "")
                entry.setdefault("last_reason", "")
            else:
                subs[k] = {
                    "base": base,
                    "status": C.DRIVE_SUBTREE_STATUS_ACTIVE,
                    "stopped_at": "",
                    "stopped_reason": "",
                    "last_triggered_at": "",
                    "last_msg_id": "",
                    "last_reason": "",
                }
    return data


def _write_drive_subtree_state(team_dir: Path, *, updates: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    updates = updates or {}
    lock = _state_lock_path(team_dir)
    with io_mod._locked(lock):
        _ensure_share_layout(team_dir)
        data = _load_drive_subtree_state_unlocked(team_dir, mode_default=settings._drive_mode_config_hot())
        for base, patch in updates.items():
            base_s = str(base or "").strip()
            if not base_s:
                continue
            entry = _drive_subtree_entry(data, base=base_s)
            if not entry:
                continue
            if isinstance(patch, dict):
                for k, v in patch.items():
                    entry[k] = v
        data["updated_at"] = util._now()
        io_mod._write_json_atomic(_drive_subtree_state_path(team_dir), data)
        return data


def _set_drive_subtree_status(team_dir: Path, *, base: str, status: str, reason: str = "") -> None:
    base_s = str(base or "").strip()
    if not base_s:
        return
    st = str(status or "").strip().lower()
    if st not in C.DRIVE_SUBTREE_STATUSES:
        st = C.DRIVE_SUBTREE_STATUS_ACTIVE

    patch: dict[str, Any] = {"status": st}
    if st == C.DRIVE_SUBTREE_STATUS_STOPPED:
        patch["stopped_at"] = util._now()
        patch["stopped_reason"] = reason.strip()
    else:
        patch["stopped_at"] = ""
        patch["stopped_reason"] = ""
    _write_drive_subtree_state(team_dir, updates={base_s: patch})


def _set_drive_mode_config(mode: str) -> str:
    mode = settings._normalize_drive_mode(mode)
    if mode not in C.DRIVE_MODES:
        raise SystemExit(f"❌ invalid drive mode: {mode!r} (allowed: running|standby)")

    path = runtime._config_file()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(f"❌ config file missing: {path}")
    except OSError as e:
        raise SystemExit(f"❌ failed to read config file: {path} ({e})")

    lines = raw.splitlines(keepends=True)
    in_team = False
    team_indent: int | None = None
    in_drive = False
    drive_indent: int | None = None
    changed = False

    def leading_spaces(s: str) -> int:
        return len(s) - len(s.lstrip(" "))

    for i, line in enumerate(lines):
        stripped = line.lstrip(" ")
        if not stripped.strip() or stripped.lstrip().startswith("#"):
            continue
        indent = leading_spaces(line)
        key = stripped.split(":", 1)[0].strip()

        if not in_team:
            if key == "team" and stripped.startswith("team:"):
                in_team = True
                team_indent = indent
                in_drive = False
                drive_indent = None
            continue

        if team_indent is not None and indent <= team_indent and not stripped.startswith("-"):
            in_team = False
            in_drive = False
            drive_indent = None
            team_indent = None
            continue

        if not in_drive:
            if key == "drive" and stripped.startswith("drive:"):
                in_drive = True
                drive_indent = indent
            continue

        if drive_indent is not None and indent <= drive_indent and not stripped.startswith("-"):
            in_drive = False
            drive_indent = None
            continue

        if key != "mode" or not stripped.startswith("mode:"):
            continue

        suffix = ""
        if "#" in stripped:
            idx = stripped.find("#")
            suffix = " " + stripped[idx:].rstrip("\n")
        newline = "\n" if line.endswith("\n") else ""
        lines[i] = (" " * indent) + f"mode: {mode}" + suffix + newline
        changed = True
        break

    if not changed:
        raise SystemExit(f"❌ failed to locate `team.drive.mode` in config: {path}")

    new_raw = "".join(lines)
    try:
        io_mod._write_text_atomic(path, new_raw)
    except OSError as e:
        raise SystemExit(f"❌ failed to write config file: {path} ({e})")
    return mode


def _default_reply_drive_state() -> dict[str, Any]:
    return {
        "version": 1,
        "created_at": util._now(),
        "updated_at": util._now(),
        "last_triggered_at": "",
        "last_reason": "",
        "last_request_id": "",
        "last_target_base": "",
        "last_target_full": "",
    }


def _load_reply_drive_state_unlocked(team_dir: Path) -> dict[str, Any]:
    path = _reply_drive_state_path(team_dir)
    data = io_mod._read_json(path) if path.is_file() else {}
    if not data:
        data = _default_reply_drive_state()
        io_mod._write_json_atomic(path, data)
        return data

    data.setdefault("version", 1)
    data.setdefault("created_at", util._now())
    data["updated_at"] = util._now()
    data.setdefault("last_triggered_at", "")
    data.setdefault("last_reason", "")
    data.setdefault("last_request_id", "")
    data.setdefault("last_target_base", "")
    data.setdefault("last_target_full", "")
    return data


def _write_reply_drive_state(team_dir: Path, *, update: dict[str, Any]) -> dict[str, Any]:
    lock = _state_lock_path(team_dir)
    with io_mod._locked(lock):
        _ensure_share_layout(team_dir)
        data = _load_reply_drive_state_unlocked(team_dir)
        for k, v in update.items():
            data[k] = v
        data["updated_at"] = util._now()
        io_mod._write_json_atomic(_reply_drive_state_path(team_dir), data)
        return data


def _normalize_agent_status(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in {"work", "working", "busy"}:
        return C.STATE_STATUS_WORKING
    if s in {"drain", "draining"}:
        return C.STATE_STATUS_DRAINING
    if s in {"idle", "standby"}:
        return C.STATE_STATUS_IDLE
    return s


_DURATION_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)?$")


def _parse_duration_seconds(raw: str, *, default_s: float) -> float:
    s = (raw or "").strip()
    if not s:
        return float(default_s)
    m = _DURATION_RE.match(s)
    if not m:
        return float(default_s)
    try:
        n = float(m.group(1))
    except Exception:
        return float(default_s)
    unit = (m.group(2) or "").strip().lower()
    if not unit:
        return float(n)
    if unit in {"s", "sec", "secs", "second", "seconds"}:
        return float(n)
    if unit in {"m", "min", "mins", "minute", "minutes"}:
        return float(n) * 60.0
    if unit in {"h", "hr", "hrs", "hour", "hours"}:
        return float(n) * 3600.0
    if unit in {"d", "day", "days"}:
        return float(n) * 86400.0
    return float(default_s)


def _default_agent_state(*, full: str, base: str, role: str) -> dict[str, Any]:
    return {
        "version": 1,
        "created_at": util._now(),
        "updated_at": util._now(),
        "full": full,
        "base": base,
        "role": role,
        "status": C.STATE_STATUS_WORKING,
        "status_source": "init",
        "last_inbox_check_at": "",
        "last_inbox_unread": 0,
        "last_inbox_overflow": 0,
        "last_output_hash": "",
        "last_output_capture_at": "",
        "last_output_change_at": "",
        "auto_enter_last_sent_at": "",
        "auto_enter_last_reason": "",
        "auto_enter_count": 0,
        "idle_since": "",
        "idle_inbox_empty_at": "",
        "wakeup_scheduled_at": "",
        "wakeup_due_at": "",
        "wakeup_sent_at": "",
        "wakeup_reason": "",
        "stale_alert_sent_at": "",
        "stale_alert_msg_id": "",
        "stale_alert_reason": "",
    }


def _load_agent_state_unlocked(team_dir: Path, *, full: str, base: str, role: str) -> dict[str, Any]:
    path = _agent_state_path(team_dir, full=full)
    data = io_mod._read_json(path) if path.is_file() else {}
    if not data:
        data = _default_agent_state(full=full, base=base, role=role)
        io_mod._write_json_atomic(path, data)
        return data

    data.setdefault("version", 1)
    data.setdefault("created_at", util._now())
    data.setdefault("full", full)
    data.setdefault("base", base)
    data.setdefault("role", role)
    data.setdefault("status", C.STATE_STATUS_WORKING)
    data.setdefault("status_source", "init")
    data["updated_at"] = util._now()

    status = _normalize_agent_status(str(data.get("status", "")))
    if status not in C.STATE_STATUSES:
        status = C.STATE_STATUS_WORKING
    data["status"] = status
    data.setdefault("last_output_hash", "")
    data.setdefault("last_output_capture_at", "")
    data.setdefault("last_output_change_at", "")
    data.setdefault("auto_enter_last_sent_at", "")
    data.setdefault("auto_enter_last_reason", "")
    data.setdefault("auto_enter_count", 0)
    return data


def _write_agent_state(team_dir: Path, *, full: str, base: str, role: str, update: dict[str, Any]) -> dict[str, Any]:
    lock = _state_lock_path(team_dir)
    with io_mod._locked(lock):
        _ensure_share_layout(team_dir)
        data = _load_agent_state_unlocked(team_dir, full=full, base=base, role=role)
        for k, v in update.items():
            data[k] = v
        data["updated_at"] = util._now()
        io_mod._write_json_atomic(_agent_state_path(team_dir, full=full), data)
        return data


def _update_agent_state(
    team_dir: Path,
    *,
    full: str,
    base: str,
    role: str,
    updater,
) -> dict[str, Any]:
    lock = _state_lock_path(team_dir)
    with io_mod._locked(lock):
        _ensure_share_layout(team_dir)
        data = _load_agent_state_unlocked(team_dir, full=full, base=base, role=role)
        updater(data)
        data["updated_at"] = util._now()
        io_mod._write_json_atomic(_agent_state_path(team_dir, full=full), data)
        return data
