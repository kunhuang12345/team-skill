from __future__ import annotations

from functools import lru_cache
from typing import Any

from . import config as config_mod
from . import constants as C
from . import policy as policy_mod
from . import runtime


@lru_cache(maxsize=1)
def _inbox_max_unread_per_thread() -> int:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    n = config_mod._cfg_get_intish(cfg, ("team", "messaging", "inbox", "max_unread_per_thread"), default=C.INBOX_MAX_UNREAD_DEFAULT)
    if n < 1:
        n = 1
    if n > 100:
        n = 100
    return n


@lru_cache(maxsize=1)
def _state_inbox_check_interval_s() -> float:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    n = config_mod._cfg_get_floatish(cfg, ("team", "state", "inbox_check_interval"), default=C.STATE_INBOX_CHECK_INTERVAL_DEFAULT)
    if n < 5:
        n = 5.0
    if n > 3600:
        n = 3600.0
    return float(n)


@lru_cache(maxsize=1)
def _state_idle_wake_delay_s() -> float:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    n = config_mod._cfg_get_floatish(cfg, ("team", "state", "idle_wake_delay"), default=C.STATE_IDLE_WAKE_DELAY_DEFAULT)
    if n < 5:
        n = 5.0
    if n > 3600:
        n = 3600.0
    return float(n)


@lru_cache(maxsize=1)
def _state_watch_interval_s() -> float:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    n = config_mod._cfg_get_floatish(cfg, ("team", "state", "watch_interval"), default=C.STATE_WATCH_INTERVAL_DEFAULT)
    if n < 5:
        n = 5.0
    if n > 3600:
        n = 3600.0
    return float(n)


@lru_cache(maxsize=1)
def _state_activity_window_s() -> float:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    n = config_mod._cfg_get_floatish(cfg, ("team", "state", "activity_window"), default=C.STATE_ACTIVITY_WINDOW_DEFAULT)
    if n < 10:
        n = 10.0
    if n > 3600:
        n = 3600.0
    return float(n)


@lru_cache(maxsize=1)
def _state_active_grace_period_s() -> float:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    n = config_mod._cfg_get_floatish(cfg, ("team", "state", "active_grace_period"), default=C.STATE_ACTIVE_GRACE_PERIOD_DEFAULT)
    if n < 0:
        n = 0.0
    if n > 3600:
        n = 3600.0
    return float(n)


@lru_cache(maxsize=1)
def _state_activity_capture_lines() -> int:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    n = config_mod._cfg_get_intish(cfg, ("team", "state", "activity_capture_lines"), default=C.STATE_ACTIVITY_CAPTURE_LINES_DEFAULT)
    if n < 20:
        n = 20
    if n > 5000:
        n = 5000
    return int(n)


@lru_cache(maxsize=1)
def _state_auto_enter_enabled() -> bool:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    return config_mod._cfg_get_boolish(cfg, ("team", "state", "auto_enter", "enabled"), default=C.STATE_AUTO_ENTER_ENABLED_DEFAULT)


@lru_cache(maxsize=1)
def _state_auto_enter_cooldown_s() -> float:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    n = config_mod._cfg_get_floatish(cfg, ("team", "state", "auto_enter", "cooldown"), default=C.STATE_AUTO_ENTER_COOLDOWN_DEFAULT)
    if n < 0:
        n = 0.0
    if n > 3600:
        n = 3600.0
    return float(n)


@lru_cache(maxsize=1)
def _state_auto_enter_tail_window_lines() -> int:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    n = config_mod._cfg_get_intish(
        cfg, ("team", "state", "auto_enter", "tail_window_lines"), default=C.STATE_AUTO_ENTER_TAIL_WINDOW_LINES_DEFAULT
    )
    if n < 10:
        n = 10
    if n > 1000:
        n = 1000
    return int(n)


@lru_cache(maxsize=1)
def _state_auto_enter_patterns() -> list[str]:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    patterns = config_mod._cfg_get_str_list(cfg, ("team", "state", "auto_enter", "patterns"), default=C.STATE_AUTO_ENTER_PATTERNS_DEFAULT)
    out: list[str] = []
    for p in patterns:
        s = (p or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _normalize_drive_mode(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in {"on", "enable", "enabled", "true", "1", "run", "running"}:
        return C.DRIVE_MODE_RUNNING
    if s in {"off", "disable", "disabled", "false", "0", "standby", "idle"}:
        return C.DRIVE_MODE_STANDBY
    return s


@lru_cache(maxsize=1)
def _drive_mode_config_default() -> str:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    raw_mode = config_mod._cfg_get_str(cfg, ("team", "drive", "mode"), default="")
    if raw_mode.strip():
        mode = _normalize_drive_mode(raw_mode)
        return mode if mode in C.DRIVE_MODES else C.DRIVE_MODE_DEFAULT
    enabled = config_mod._cfg_get_boolish(
        cfg,
        ("team", "drive", "enabled"),
        default=(True if C.DRIVE_MODE_DEFAULT == C.DRIVE_MODE_RUNNING else False),
    )
    return C.DRIVE_MODE_RUNNING if enabled else C.DRIVE_MODE_STANDBY


def _drive_mode_config_hot() -> str:
    """
    Drive mode is controlled by config and must be hot-reloaded by the watcher.

    Requirement: only `team.drive.mode` is treated as authoritative and is re-read
    each watcher tick. Other config values remain cached and require watcher restart.
    """
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    raw_mode = config_mod._cfg_get_str(cfg, ("team", "drive", "mode"), default="")
    if raw_mode.strip():
        mode = _normalize_drive_mode(raw_mode)
        return mode if mode in C.DRIVE_MODES else C.DRIVE_MODE_DEFAULT
    return _drive_mode_config_default()


@lru_cache(maxsize=1)
def _drive_driver_role() -> str:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    raw = config_mod._cfg_get_str(cfg, ("team", "drive", "driver_role"), default=C.DRIVE_DRIVER_ROLE_DEFAULT)
    role = policy_mod._norm_role(raw) or policy_mod._policy().root_role
    if role not in policy_mod._policy().enabled_roles:
        role = policy_mod._policy().root_role
    return role


@lru_cache(maxsize=1)
def _drive_backup_role() -> str:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    raw = config_mod._cfg_get_str(cfg, ("team", "drive", "backup_role"), default=C.DRIVE_BACKUP_ROLE_DEFAULT)
    role = policy_mod._norm_role(raw) or policy_mod._norm_role(C.DRIVE_BACKUP_ROLE_DEFAULT)
    if role not in policy_mod._policy().enabled_roles:
        role = policy_mod._policy().root_role
    return role


@lru_cache(maxsize=1)
def _drive_unit_role() -> str:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    raw = config_mod._cfg_get_str(cfg, ("team", "drive", "unit_role"), default=C.DRIVE_UNIT_ROLE_DEFAULT)
    role = policy_mod._norm_role(raw)
    if not role:
        return ""
    return role if role in policy_mod._policy().enabled_roles else ""


@lru_cache(maxsize=1)
def _drive_cooldown_s() -> float:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    n = config_mod._cfg_get_floatish(cfg, ("team", "drive", "cooldown"), default=C.DRIVE_COOLDOWN_DEFAULT)
    if n < 0:
        n = 0.0
    if n > 86400:
        n = 86400.0
    return float(n)


@lru_cache(maxsize=1)
def _state_wake_message() -> str:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    msg = config_mod._cfg_get_str(cfg, ("team", "state", "wake_message"), default=C.STATE_WAKE_MESSAGE_DEFAULT)
    resolved = msg.strip() or C.STATE_WAKE_MESSAGE_DEFAULT
    return runtime._substitute_atwf_paths(resolved).strip()


@lru_cache(maxsize=1)
def _state_reply_wake_message() -> str:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    msg = config_mod._cfg_get_str(cfg, ("team", "state", "reply_wake_message"), default=C.STATE_REPLY_WAKE_MESSAGE_DEFAULT)
    resolved = msg.strip() or C.STATE_REPLY_WAKE_MESSAGE_DEFAULT
    return runtime._substitute_atwf_paths(resolved).strip()


@lru_cache(maxsize=1)
def _request_deadline_s() -> float:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    n = config_mod._cfg_get_floatish(cfg, ("team", "reply", "deadline"), default=C.REQUEST_DEADLINE_DEFAULT_S)
    if n < 60:
        n = 60.0
    if n > 86400:
        n = 86400.0
    return float(n)


@lru_cache(maxsize=1)
def _request_block_snooze_default_s() -> float:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    n = config_mod._cfg_get_floatish(cfg, ("team", "reply", "blocked_snooze"), default=C.REQUEST_BLOCK_SNOOZE_DEFAULT_S)
    if n < 30:
        n = 30.0
    if n > 86400:
        n = 86400.0
    return float(n)


@lru_cache(maxsize=1)
def _state_working_stale_threshold_s() -> float:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    n = config_mod._cfg_get_floatish(cfg, ("team", "state", "working_stale_threshold"), default=C.STATE_WORKING_STALE_THRESHOLD_DEFAULT)
    if n < 0:
        n = 0.0
    if n > 86400:
        n = 86400.0
    return float(n)


@lru_cache(maxsize=1)
def _state_working_alert_cooldown_s() -> float:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    n = config_mod._cfg_get_floatish(cfg, ("team", "state", "working_alert_cooldown"), default=C.STATE_WORKING_ALERT_COOLDOWN_DEFAULT)
    if n < 0:
        n = 0.0
    if n > 86400:
        n = 86400.0
    return float(n)

