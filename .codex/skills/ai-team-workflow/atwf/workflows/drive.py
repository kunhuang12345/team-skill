from __future__ import annotations

from ..core import config as config_mod
from ..core import templates
from ..core import runtime


def _drive_message_body(*, iso_ts: str, msg_id: str, extra: dict[str, str] | None = None) -> str:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    raw = config_mod._cfg_get_str(cfg, ("team", "drive", "message", "body"), default="")
    if raw.strip():
        return templates._render_drive_template(raw, iso_ts=iso_ts, msg_id=msg_id, extra=extra).rstrip() + "\n"
    default = (
        "[DRIVE] team stalled: ALL IDLE + INBOX EMPTY\n"
        "- detected_at: {{iso_ts}}\n"
        "- meaning: no one is driving work. This is an ABNORMAL STALL.\n"
        "\n"
        "1) Diagnose now:\n"
        "- atwf state\n"
        "- atwf list\n"
        "- atwf inbox (your own inbox)\n"
        "\n"
        'Summarize why the team reached "all idle + inbox empty", find the root cause, then re-drive the team back to work.\n'
    )
    return templates._render_drive_template(default, iso_ts=iso_ts, msg_id=msg_id, extra=extra)


def _drive_message_summary(*, iso_ts: str, msg_id: str, extra: dict[str, str] | None = None) -> str:
    cfg = config_mod._read_yaml_or_json(runtime._config_file())
    raw = config_mod._cfg_get_str(cfg, ("team", "drive", "message", "summary"), default="")
    if raw.strip():
        return templates._render_drive_template(raw, iso_ts=iso_ts, msg_id=msg_id, extra=extra).rstrip() + "\n"
    default = (
        "[DRIVE] team stalled: ALL IDLE + INBOX EMPTY\n"
        "inbox id={{msg_id}} (open: {{open_cmd}})\n"
        "Action: diagnose root cause, then re-drive the team back to work.\n"
    )
    return templates._render_drive_template(default, iso_ts=iso_ts, msg_id=msg_id, extra=extra)
