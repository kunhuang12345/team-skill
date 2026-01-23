from __future__ import annotations

import hashlib
import re
import sys
from datetime import datetime


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_iso_dt(raw: str) -> datetime | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _slugify(raw: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", (raw or "").strip())
    s = "-".join(seg for seg in s.split("-") if seg)
    return s or "unknown"


def _text_digest(raw: str) -> str:
    s = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()
