#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def _main(argv: list[str]) -> int:
    # Allow running from anywhere: ensure the skill root (one level above scripts/)
    # is on sys.path so `import atwf` resolves to the bundled package.
    skill_dir = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(skill_dir))
    from atwf.cli import main

    return int(main(argv))


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
