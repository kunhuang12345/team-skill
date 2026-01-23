from __future__ import annotations

import re
from pathlib import Path

from . import runtime


def _templates_check_files() -> list[Path]:
    templates = runtime._templates_dir()
    files = sorted([p for p in templates.glob("*.md") if p.is_file()])
    cfg = runtime._config_file()
    if cfg.is_file():
        files.append(cfg)
    return files


def _template_lint_issues() -> list[str]:
    issues: list[str] = []

    def line_of(text: str, pos: int) -> int:
        if pos < 0:
            pos = 0
        return text[:pos].count("\n") + 1

    for path in _templates_check_files():
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            issues.append(f"{path}: failed to read ({e})")
            continue

        legacy_atwf = ".codex/skills/ai-team-workflow/scripts/atwf"
        if legacy_atwf in raw:
            ln = line_of(raw, raw.find(legacy_atwf))
            issues.append(f"{path}:{ln}: hardcoded .codex path detected; use {{ATWF_CMD}} instead")

        legacy_cfg = ".codex/skills/ai-team-workflow/scripts/atwf_config.yaml"
        if legacy_cfg in raw:
            ln = line_of(raw, raw.find(legacy_cfg))
            issues.append(f"{path}:{ln}: hardcoded config path detected; use {{ATWF_CONFIG}} instead")

        m = re.search(r"`atwf\\s+", raw)
        if m:
            issues.append(f"{path}:{line_of(raw, m.start())}: bare `atwf <subcmd>` detected; use `{{ATWF_CMD}} <subcmd>`")

    return issues


def _validate_templates_or_die() -> None:
    issues = _template_lint_issues()
    if not issues:
        return
    joined = "\n".join(f"- {s}" for s in issues)
    raise SystemExit(
        "❌ templates validation failed (portability rule).\n"
        "Fix the templates/config to use `{{ATWF_CMD}}` / `{{ATWF_CONFIG}}` placeholders.\n"
        f"{joined}"
    )


def _template_for_role(role: str) -> Path:
    from . import policy as policy_mod

    role = policy_mod._require_role(role)
    p = runtime._templates_dir() / f"{role}.md"
    if not p.is_file():
        raise SystemExit(f"❌ missing template for role={role}: {p}")
    return p


def _render_template(raw: str, *, role: str, full: str, base: str, registry: Path, team_dir: Path) -> str:
    rendered = (
        (raw or "")
        .replace("{{ROLE}}", role)
        .replace("{{FULL_NAME}}", full)
        .replace("{{BASE_NAME}}", base)
        .replace("{{REGISTRY_PATH}}", str(registry))
        .replace("{{TEAM_DIR}}", str(team_dir))
        .replace("{{SKILL_DIR}}", str(runtime._skill_dir().resolve()))
    )
    return runtime._substitute_atwf_paths(rendered)


def _render_drive_template(template: str, *, iso_ts: str, msg_id: str, extra: dict[str, str] | None = None) -> str:
    s = (template or "").replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("{{iso_ts}}", iso_ts)
    s = s.replace("{{msg_id}}", msg_id)
    s = s.replace("{{open_cmd}}", f"{runtime._atwf_cmd()} inbox-open {msg_id}")
    if extra:
        for k, v in extra.items():
            key = str(k or "").strip()
            if not key:
                continue
            s = s.replace(f"{{{{{key}}}}}", str(v))
    return runtime._substitute_atwf_paths(s)

