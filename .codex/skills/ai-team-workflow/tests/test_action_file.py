import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
import importlib


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

cli_main = importlib.import_module("atwf.cli.main")  # noqa: E402


def _write_registry(team_dir: Path) -> None:
    # Minimal registry for a single direct action: admin -> dev.
    (team_dir / "registry.json").write_text(
        """
{
  "version": 1,
  "members": [
    {"full": "admin-REQ-1", "base": "admin-REQ-1", "role": "admin"},
    {"full": "dev-REQ-1", "base": "dev-REQ-1", "role": "dev"}
  ]
}
""".lstrip(),
        encoding="utf-8",
    )


class ActionFileTests(unittest.TestCase):
    def test_action_reads_file_body(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            team_dir = Path(td)
            _write_registry(team_dir)
            msg_path = team_dir / "msg.md"
            msg_path.write_text("hello\n", encoding="utf-8")

            os.environ["AITWF_DIR"] = str(team_dir)
            try:
                args = SimpleNamespace(
                    targets=["dev-REQ-1"],
                    role=None,
                    subtree=None,
                    include_excluded=False,
                    notify=False,
                    as_target="admin-REQ-1",
                    message=None,
                    message_file=str(msg_path),
                )
                with redirect_stdout(io.StringIO()):
                    rc = cli_main.cmd_action(args)  # type: ignore[arg-type]
                self.assertEqual(rc, 0)

                inbox_root = team_dir / "inbox"
                md_files = list(inbox_root.rglob("*.md"))
                self.assertEqual(len(md_files), 1)
                content = md_files[0].read_text(encoding="utf-8")
                self.assertIn("hello", content)
                self.assertTrue((team_dir / "tmp").is_dir())
            finally:
                os.environ.pop("AITWF_DIR", None)

    def test_action_rejects_dash_message(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            team_dir = Path(td)
            _write_registry(team_dir)

            os.environ["AITWF_DIR"] = str(team_dir)
            try:
                args = SimpleNamespace(
                    targets=["dev-REQ-1"],
                    role=None,
                    subtree=None,
                    include_excluded=False,
                    notify=False,
                    as_target="admin-REQ-1",
                    message="-",
                    message_file=None,
                )
                with self.assertRaises(SystemExit):
                    with redirect_stdout(io.StringIO()):
                        cli_main.cmd_action(args)  # type: ignore[arg-type]
                self.assertEqual(list((team_dir / "inbox").rglob("*.md")), [])
            finally:
                os.environ.pop("AITWF_DIR", None)

    def test_action_stage_requires_req_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            team_dir = Path(td)
            _write_registry(team_dir)

            os.environ["AITWF_DIR"] = str(team_dir)
            try:
                args_missing = SimpleNamespace(
                    targets=["dev-REQ-1"],
                    role=None,
                    subtree=None,
                    include_excluded=False,
                    notify=False,
                    as_target="admin-REQ-1",
                    message="stage: warmup\nreq_id: REQ-1\nreq_root: /tmp/x\n",
                    message_file=None,
                )
                with self.assertRaises(SystemExit):
                    with redirect_stdout(io.StringIO()):
                        cli_main.cmd_action(args_missing)  # type: ignore[arg-type]

                args_ok = SimpleNamespace(
                    targets=["dev-REQ-1"],
                    role=None,
                    subtree=None,
                    include_excluded=False,
                    notify=False,
                    as_target="admin-REQ-1",
                    message="stage: warmup\nreq_id: REQ-1\ndocs_dir: /tmp/docs\nreq_root: /tmp/x\n",
                    message_file=None,
                )
                with redirect_stdout(io.StringIO()):
                    rc = cli_main.cmd_action(args_ok)  # type: ignore[arg-type]
                self.assertEqual(rc, 0)
            finally:
                os.environ.pop("AITWF_DIR", None)

    def test_action_stage_rejects_placeholders_and_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            team_dir = Path(td)
            _write_registry(team_dir)

            os.environ["AITWF_DIR"] = str(team_dir)
            try:
                args_relative = SimpleNamespace(
                    targets=["dev-REQ-1"],
                    role=None,
                    subtree=None,
                    include_excluded=False,
                    notify=False,
                    as_target="admin-REQ-1",
                    message="stage: warmup\nreq_id: REQ-1\ndocs_dir: ./docs\nreq_root: /tmp/x\n",
                    message_file=None,
                )
                with self.assertRaises(SystemExit):
                    with redirect_stdout(io.StringIO()):
                        cli_main.cmd_action(args_relative)  # type: ignore[arg-type]
                self.assertEqual(list((team_dir / "inbox").rglob("*.md")), [])

                args_placeholder = SimpleNamespace(
                    targets=["dev-REQ-1"],
                    role=None,
                    subtree=None,
                    include_excluded=False,
                    notify=False,
                    as_target="admin-REQ-1",
                    message="stage: warmup\nreq_id: REQ-1\ndocs_dir: /tmp/docs\nreq_root: ${REQ_ROOT}\n",
                    message_file=None,
                )
                with self.assertRaises(SystemExit):
                    with redirect_stdout(io.StringIO()):
                        cli_main.cmd_action(args_placeholder)  # type: ignore[arg-type]
                self.assertEqual(list((team_dir / "inbox").rglob("*.md")), [])
            finally:
                os.environ.pop("AITWF_DIR", None)
