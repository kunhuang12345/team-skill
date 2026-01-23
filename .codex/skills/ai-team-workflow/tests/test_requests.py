import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from atwf import requests  # noqa: E402
from atwf import state_store  # noqa: E402


class RequestsTests(unittest.TestCase):
    def test_resolve_request_id_numeric_maps_to_req_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            team_dir = Path(td)
            state_store._ensure_share_layout(team_dir)
            (team_dir / "requests" / "req-1").mkdir(parents=True, exist_ok=True)
            self.assertEqual(requests._resolve_request_id(team_dir, "1"), "req-1")

    def test_request_response_path_slugifies_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            team_dir = Path(td)
            state_store._ensure_share_layout(team_dir)
            resp = requests._request_response_path(team_dir, request_id="req-1", target_base="Dev A")
            self.assertTrue(str(resp).endswith("Dev-A.md"))
