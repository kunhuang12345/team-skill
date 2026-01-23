from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from . import config as config_mod
from . import project
from . import registry as registry_mod
from . import runtime
from . import tmux as tmux_mod
from . import twf as twf_mod
from . import util


def _cap_watch_session_name(project_root: Path) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", project_root.name or "project").strip("-") or "project"
    digest = hashlib.sha1(str(project_root).encode("utf-8")).hexdigest()[:8]
    return f"cap-watch-{base[:24]}-{digest}"


def _resolve_cap_cmd(*, twf: Path, twf_cfg: dict[str, Any]) -> Path | None:
    raw = os.environ.get("TWF_ACCOUNT_POOL_CMD", "").strip() or config_mod._cfg_get_str(
        twf_cfg,
        ("twf", "account_pool", "cmd"),
        ("twf_account_pool_cmd",),
        default="",
    )
    tmux_skill_dir = twf.resolve().parents[1]

    if raw:
        p = Path(os.path.expanduser(raw))
        if not p.is_absolute():
            p = (tmux_skill_dir / p).resolve()
        else:
            p = p.resolve()
        if p.is_file():
            return p

    skills_dir = tmux_skill_dir.parent
    sibling = skills_dir / "codex-account-pool" / "scripts" / "cap"
    if sibling.is_file():
        return sibling.resolve()

    global_path = Path.home() / ".codex" / "skills" / "codex-account-pool" / "scripts" / "cap"
    if global_path.is_file():
        return global_path.resolve()

    return None


def _normalize_auth_strategy(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in {"rr", "round_robin", "round-robin"}:
        return "team_cycle"
    if s in {"least_used", "least-used"}:
        return "balanced"
    return s


def _watch_idle_session_name(project_root: Path, *, team_dir: Path) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", project_root.name or "project").strip("-") or "project"
    digest = hashlib.sha1(f"{project_root}|{team_dir}".encode("utf-8")).hexdigest()[:8]
    return f"atwf-watch-idle-{base[:20]}-{digest}"


def _restart_watch_idle_team(*, twf: Path, team_dir: Path, registry: Path) -> None:
    session = _watch_idle_session_name(project._expected_project_root(), team_dir=team_dir)
    tmux_mod._tmux_kill_session(session)
    _ensure_watch_idle_team(twf=twf, team_dir=team_dir, registry=registry)


def _ensure_watch_idle_team(*, twf: Path, team_dir: Path, registry: Path) -> None:
    reg = registry_mod._load_registry(registry)
    members = reg.get("members")
    if not isinstance(members, list) or not any(isinstance(m, dict) and str(m.get("full", "")).strip() for m in members):
        return

    session = _watch_idle_session_name(project._expected_project_root(), team_dir=team_dir)
    if tmux_mod._tmux_running(session):
        return

    exports: list[str] = []
    exports.append(f"export AITWF_DIR={shlex.quote(str(team_dir))};")
    exports.append(f"export AITWF_TWF={shlex.quote(str(twf))};")
    twf_cfg = os.environ.get("TWF_CODEX_CMD_CONFIG", "").strip()
    if twf_cfg:
        exports.append(f"export TWF_CODEX_CMD_CONFIG={shlex.quote(twf_cfg)};")

    cmd_parts = [
        "bash",
        str(runtime._atwf_wrapper_path()),
        "watch-idle",
    ]
    cmd_line = " ".join(shlex.quote(p) for p in cmd_parts)
    launch = "".join(exports) + f"exec {cmd_line}"

    res = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, "-c", str(project._expected_project_root()), "bash", "-lc", launch],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if res.returncode != 0:
        util._eprint(f"⚠️ failed to start atwf watch-idle tmux session: {session}")
        return
    util._eprint(f"🛰️ atwf watch-idle started: {session}")


def _ensure_cap_watch_team(*, twf: Path, team_dir: Path, registry: Path) -> None:
    reg = registry_mod._load_registry(registry)
    members = reg.get("members")
    if not isinstance(members, list) or not any(isinstance(m, dict) and str(m.get("full", "")).strip() for m in members):
        return

    try:
        twf_cfg_path = twf_mod._resolve_twf_config_path(twf)
        twf_cfg = config_mod._read_yaml_or_json(twf_cfg_path) if twf_cfg_path else {}
    except Exception:
        twf_cfg = {}

    enabled = config_mod._cfg_get_boolish(twf_cfg, ("twf", "account_pool", "enabled"), ("twf_use_account_pool",), default=False)
    if not enabled:
        return

    strategy_raw = config_mod._cfg_get_str(
        twf_cfg,
        ("twf", "account_pool", "auth_team", "strategy"),
        ("twf_auth_team_strategy",),
        default="",
    )
    strategy = _normalize_auth_strategy(strategy_raw)
    if strategy != "team_cycle":
        return

    watch_enabled = config_mod._cfg_get_boolish(twf_cfg, ("twf", "account_pool", "watch_team", "enabled"), default=True)
    if not watch_enabled:
        return

    auth_dir_raw = config_mod._cfg_get_str(
        twf_cfg,
        ("twf", "account_pool", "auth_team", "dir"),
        ("twf_auth_team_dir",),
        default="",
    )
    if not auth_dir_raw:
        util._eprint("⚠️ account_pool enabled but auth_team.dir is empty; not starting cap watch-team")
        return
    auth_dir = runtime._expand_path(auth_dir_raw)
    if not auth_dir.is_dir():
        util._eprint(f"⚠️ auth_team.dir is not a directory: {auth_dir} (not starting cap watch-team)")
        return

    auth_glob = config_mod._cfg_get_str(
        twf_cfg,
        ("twf", "account_pool", "auth_team", "glob"),
        ("twf_auth_team_glob",),
        default="auth.json*",
    )

    interval = config_mod._cfg_get_floatish(twf_cfg, ("twf", "account_pool", "watch_team", "interval"), default=180.0)
    grace = config_mod._cfg_get_floatish(twf_cfg, ("twf", "account_pool", "watch_team", "grace"), default=300.0)
    max_retries = config_mod._cfg_get_intish(twf_cfg, ("twf", "account_pool", "watch_team", "max_retries"), default=10)
    needle = config_mod._cfg_get_str(
        twf_cfg,
        ("twf", "account_pool", "watch_team", "needle"),
        default="You've hit your usage limit.",
    )
    message = config_mod._cfg_get_str(
        twf_cfg,
        ("twf", "account_pool", "watch_team", "message"),
        default="Task continues. If you are waiting for a reply, please ignore this message.",
    )

    cap_cmd = _resolve_cap_cmd(twf=twf, twf_cfg=twf_cfg)
    if not cap_cmd:
        util._eprint("⚠️ account_pool enabled but codex-account-pool/cap not found; not starting cap watch-team")
        return

    session = _cap_watch_session_name(project._expected_project_root())
    if tmux_mod._tmux_running(session):
        return

    exports: list[str] = []
    exports.append(f"export CAP_STATUS_SESSION_PREFIX={shlex.quote(session)};")
    for k in ("CAP_STATE_FILE", "CAP_SOURCES", "CAP_STRATEGY"):
        v = os.environ.get(k, "").strip()
        if v:
            exports.append(f"export {k}={shlex.quote(v)};")
    twf_cfg_path_env = os.environ.get("TWF_CODEX_CMD_CONFIG", "").strip()
    if twf_cfg_path_env:
        exports.append(f"export TWF_CODEX_CMD_CONFIG={shlex.quote(twf_cfg_path_env)};")

    cmd_parts = [
        "bash",
        str(cap_cmd),
        "watch-team",
        str(auth_dir),
        "--glob",
        auth_glob,
        "--registry",
        str(registry),
        "--interval",
        str(interval),
        "--grace",
        str(grace),
        "--max-retries",
        str(max_retries),
        "--needle",
        needle,
        "--message",
        message,
    ]
    cmd_line = " ".join(shlex.quote(p) for p in cmd_parts)
    launch = "".join(exports) + f"exec {cmd_line}"

    res = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, "-c", str(project._expected_project_root()), "bash", "-lc", launch],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if res.returncode != 0:
        util._eprint(f"⚠️ failed to start cap watch-team tmux session: {session}")
        return
    util._eprint(f"🛰️ cap watch-team started: {session}")

