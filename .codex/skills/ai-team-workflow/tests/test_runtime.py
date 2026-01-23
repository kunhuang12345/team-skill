import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from atwf.core import runtime  # noqa: E402


class RuntimeTests(unittest.TestCase):
    def test_substitute_atwf_paths_replaces_placeholders(self) -> None:
        raw = "cmd={{ATWF_CMD}} cfg={{ATWF_CONFIG}}"
        out = runtime._substitute_atwf_paths(raw)
        self.assertNotIn("{{ATWF_CMD}}", out)
        self.assertNotIn("{{ATWF_CONFIG}}", out)
        self.assertIn("atwf_config.yaml", out)
