import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from atwf.core import config  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_parse_simple_yaml_kv_quotes_comments_and_empty(self) -> None:
        raw = """
        # comment
        foo: bar
        quoted: "baz"
        spaced: 'a b'
        trail: value # inline comment
        empty:
        """
        parsed = config._parse_simple_yaml_kv(raw)
        self.assertEqual(parsed["foo"], "bar")
        self.assertEqual(parsed["quoted"], "baz")
        self.assertEqual(parsed["spaced"], "a b")
        self.assertEqual(parsed["trail"], "value")
        self.assertIn("empty", parsed)
        self.assertEqual(parsed["empty"], "")

    def test_cfg_get_boolish_string_variants(self) -> None:
        cfg = {"a": {"b": "yes"}, "c": {"d": "0"}, "e": {"f": True}}
        self.assertTrue(config._cfg_get_boolish(cfg, ("a", "b"), default=False))
        self.assertFalse(config._cfg_get_boolish(cfg, ("c", "d"), default=True))
        self.assertTrue(config._cfg_get_boolish(cfg, ("e", "f"), default=False))

    def test_read_yaml_or_json_json_prefers_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cfg.json"
            p.write_text('{"x": 1, "y": "z"}', encoding="utf-8")
            cfg = config._read_yaml_or_json(p)
            self.assertEqual(cfg.get("x"), 1)
            self.assertEqual(cfg.get("y"), "z")
