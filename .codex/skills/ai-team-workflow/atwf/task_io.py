from __future__ import annotations

import argparse
import sys

from . import runtime


def _extract_task_file_from_text(task: str) -> str | None:
    raw = task.strip()
    if not raw:
        return None

    candidates = []
    if raw.startswith("任务描述：") or raw.startswith("任务描述:"):
        candidates.append(raw.split(":", 1)[1] if ":" in raw else raw.split("：", 1)[1])
    candidates.append(raw)

    for cand in candidates:
        p = cand.strip().strip('"').strip("'")
        if not p:
            continue
        if not p.startswith("/"):
            continue
        try:
            path = runtime._expand_path(p)
        except Exception:
            continue
        if path.is_file():
            return str(path)
    return None


def _read_task_content(args: argparse.Namespace) -> tuple[str | None, str | None]:
    task_file = str(getattr(args, "task_file", "") or "").strip()
    task_text = str(getattr(args, "task", "") or "").strip()

    if not task_file and task_text:
        guessed = _extract_task_file_from_text(task_text)
        if guessed:
            task_file = guessed
            task_text = ""

    stdin_text = ""
    if not sys.stdin.isatty():
        stdin_text = sys.stdin.read().strip()

    if task_file:
        path = runtime._expand_path(task_file)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            raise SystemExit(f"❌ failed to read task file: {path} ({e})")
        return content, str(path)

    if task_text:
        return task_text, None
    if stdin_text:
        return stdin_text, None
    return None, None

