import hashlib
import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from atwf import util  # noqa: E402


class UtilTests(unittest.TestCase):
    def test_text_digest_sha1_and_normalizes_newlines(self) -> None:
        raw = "a\r\nb\r"
        expected = hashlib.sha1("a\nb\n".encode("utf-8")).hexdigest()
        self.assertEqual(util._text_digest(raw), expected)

    def test_slugify_basic(self) -> None:
        self.assertEqual(util._slugify(" Hello, world! "), "Hello-world")

