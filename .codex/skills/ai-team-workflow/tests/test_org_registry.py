import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from atwf.state import org  # noqa: E402
from atwf.state import registry  # noqa: E402


class OrgRegistryTests(unittest.TestCase):
    def test_tree_children_roots_and_subtree(self) -> None:
        data = {
            "members": [
                {"full": "coord-a", "role": "coord", "updated_at": "2026-01-01T00:00:00"},
                {"full": "coord-b", "role": "coord", "updated_at": "2026-01-02T00:00:00"},
                {"full": "admin-1", "role": "admin", "parent": "coord-b", "updated_at": "2026-01-02T00:01:00"},
                {"full": "dev-1", "role": "dev", "parent": "admin-1", "updated_at": "2026-01-02T00:02:00"},
            ]
        }

        children = org._tree_children(data)
        self.assertEqual(children["coord-b"], ["admin-1"])
        self.assertEqual(children["admin-1"], ["dev-1"])

        roots = org._tree_roots(data)
        self.assertEqual(roots, ["coord-b", "coord-a"])

        subtree = org._subtree_fulls(data, "coord-b")
        self.assertCountEqual(subtree, ["coord-b", "admin-1", "dev-1"])

        self.assertEqual(org._members_by_role(data, "coord"), ["coord-a", "coord-b"])

    def test_registry_resolve_member_prefers_exact_full(self) -> None:
        data = {"members": [{"full": "dev-1", "base": "dev", "updated_at": "2026-01-01T00:00:00"}]}
        self.assertEqual(registry._resolve_member(data, "dev-1")["full"], "dev-1")
        self.assertEqual(registry._resolve_member(data, "dev")["full"], "dev-1")
