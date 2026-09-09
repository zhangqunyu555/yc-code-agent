import json
import os
import tempfile
import unittest
from pathlib import Path

from yc_code_agent.tools import CommandRunner, Workspace, build_tools


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = Workspace(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_read_search_and_edit(self):
        (self.root / "code.py").write_text("alpha\nbeta\n", encoding="utf-8")
        self.assertEqual(self.workspace.read("code.py", 2), "2: beta")
        self.assertIn("code.py:1:alpha", self.workspace.search("alpha"))
        self.workspace.edit("code.py", "beta", "gamma")
        self.assertEqual((self.root / "code.py").read_text(), "alpha\ngamma\n")

    def test_escape_and_ambiguous_edit_are_rejected(self):
        with self.assertRaises(PermissionError):
            self.workspace.read("../outside")
        (self.root / "repeat.txt").write_text("x x", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.workspace.edit("repeat.txt", "x", "y")

    def test_symlink_escape_is_rejected(self):
        outside = Path(self.temporary.name).parent / "yc-agent-outside.txt"
        outside.write_text("secret", encoding="utf-8")
        try:
            os.symlink(outside, self.root / "link.txt")
            with self.assertRaises(PermissionError):
                self.workspace.read("link.txt")
        finally:
            outside.unlink(missing_ok=True)

    def test_registry_normalizes_errors(self):
        registry = build_tools(self.root, execution_mode="disabled")
        result = json.loads(registry.execute("unknown", {}))
        self.assertFalse(result["ok"])

    def test_command_allowlist(self):
        runner = CommandRunner(self.workspace, "local")
        with self.assertRaises(PermissionError):
            runner.run(["sh", "-c", "echo unsafe"])
        (self.root / "test_smoke.py").write_text(
            "import unittest\n\nclass Smoke(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        result = runner.run(["python3", "-m", "unittest", "discover", "-q"])
        self.assertTrue(result.startswith("exit_code=0"), result)


if __name__ == "__main__":
    unittest.main()
