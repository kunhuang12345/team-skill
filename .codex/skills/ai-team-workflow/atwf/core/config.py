from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _parse_simple_yaml_kv(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if not value:
            out[key] = ""
            continue
        if value[0] in {"'", '"'}:
            q = value[0]
            if value.endswith(q) and len(value) >= 2:
                out[key] = value[1:-1]
            else:
                out[key] = value[1:]
            continue
        if "#" in value:
            for i, ch in enumerate(value):
                if ch == "#" and (i == 0 or value[i - 1].isspace()):
                    value = value[:i].strip()
                    break
        out[key] = value.strip()
    return out


def _read_simple_yaml_kv(path: Path) -> dict[str, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return {}

    return _parse_simple_yaml_kv(raw)


def _read_yaml_or_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return {}

    raw_s = raw.strip()
    if not raw_s:
        return {}

    # Config files may be provided as JSON (twf supports *.json fallback) or
    # YAML. Prefer JSON when it clearly looks like JSON.
    if raw_s.startswith("{"):
        try:
            parsed = json.loads(raw_s)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # Best-effort fallback for environments without PyYAML or invalid YAML.
    return _parse_simple_yaml_kv(raw)


def _cfg_get(cfg: dict[str, Any], path: tuple[str, ...]) -> Any:
    cur: Any = cfg
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _cfg_get_str(cfg: dict[str, Any], *paths: tuple[str, ...], default: str = "") -> str:
    for p in paths:
        v = _cfg_get(cfg, p)
        if isinstance(v, str):
            return v.strip()
    return default


def _cfg_get_floatish(cfg: dict[str, Any], *paths: tuple[str, ...], default: float) -> float:
    for p in paths:
        v = _cfg_get(cfg, p)
        if v is None:
            continue
        try:
            return float(v)  # type: ignore[arg-type]
        except Exception:
            return float(default)
    return float(default)


def _cfg_get_intish(cfg: dict[str, Any], *paths: tuple[str, ...], default: int) -> int:
    for p in paths:
        v = _cfg_get(cfg, p)
        if v is None:
            continue
        try:
            return int(v)  # type: ignore[arg-type]
        except Exception:
            return int(default)
    return int(default)


def _cfg_get_boolish(cfg: dict[str, Any], *paths: tuple[str, ...], default: bool) -> bool:
    for p in paths:
        v = _cfg_get(cfg, p)
        if isinstance(v, bool):
            return v
        if v is None:
            continue
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            s = v.strip().lower()
            if s in {"1", "true", "yes", "y", "on"}:
                return True
            if s in {"0", "false", "no", "n", "off"}:
                return False
    return bool(default)


def _cfg_get_str_list(cfg: dict[str, Any], path: tuple[str, ...], *, default: tuple[str, ...]) -> list[str]:
    v = _cfg_get(cfg, path)
    if isinstance(v, list):
        out: list[str] = []
        for item in v:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        if out:
            return out
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return [s for s in default if s]

