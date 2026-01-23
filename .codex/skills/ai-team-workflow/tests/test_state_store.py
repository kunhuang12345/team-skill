import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from atwf.core import constants as C  # noqa: E402
from atwf.state import state_store  # noqa: E402


class StateStoreTests(unittest.TestCase):
    def test_parse_duration_seconds_units(self) -> None:
        self.assertEqual(state_store._parse_duration_seconds("10", default_s=1.0), 10.0)
        self.assertEqual(state_store._parse_duration_seconds("2s", default_s=1.0), 2.0)
        self.assertEqual(state_store._parse_duration_seconds("3m", default_s=1.0), 180.0)
        self.assertEqual(state_store._parse_duration_seconds("1h", default_s=1.0), 3600.0)
        self.assertEqual(state_store._parse_duration_seconds("1d", default_s=1.0), 86400.0)
        self.assertEqual(state_store._parse_duration_seconds("bad", default_s=7.0), 7.0)

    def test_normalize_agent_status_aliases(self) -> None:
        self.assertEqual(state_store._normalize_agent_status("busy"), C.STATE_STATUS_WORKING)
        self.assertEqual(state_store._normalize_agent_status("drain"), C.STATE_STATUS_DRAINING)
        self.assertEqual(state_store._normalize_agent_status("standby"), C.STATE_STATUS_IDLE)
