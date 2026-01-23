from __future__ import annotations

import os
import subprocess

from . import io as io_mod


def _tmux_kill_session(name: str) -> None:
    name = (name or "").strip()
    if not name:
        return
    subprocess.run(["tmux", "kill-session", "-t", name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _tmux_running(session: str) -> bool:
    if not (session or "").strip():
        return False
    res = subprocess.run(["tmux", "has-session", "-t", session], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return res.returncode == 0


def _tmux_capture_tail(session: str, *, lines: int) -> str | None:
    if not (session or "").strip():
        return None
    n = int(lines) if int(lines) > 0 else 200
    start = f"-{n}"
    res = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", session, "-S", start],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if res.returncode != 0:
        return None
    return res.stdout


def _tmux_send_enter(session: str) -> bool:
    if not (session or "").strip():
        return False
    res = subprocess.run(
        ["tmux", "send-keys", "-t", session, "C-m"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return res.returncode == 0


def _tmux_self_full() -> str | None:
    pane = os.environ.get("TMUX_PANE", "").strip()
    if pane:
        res = io_mod._run(["tmux", "display-message", "-p", "-t", pane, "#S"])
        if res.returncode == 0:
            name = res.stdout.strip()
            if name:
                return name

    res2 = io_mod._run(["tmux", "display-message", "-p", "#S"])
    if res2.returncode != 0:
        return None
    name2 = res2.stdout.strip()
    return name2 or None

