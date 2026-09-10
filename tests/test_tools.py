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

    def test_read_caps_large_files_and_points_to_next_range(self):
        (self.root / "large.py").write_text("\n".join(str(index) for index in range(500)), encoding="utf-8")
        result = self.workspace.read("large.py")
        self.assertIn("truncated at 400 lines", result)
        self.assertIn("start_line=401", result)
        self.assertNotIn("500: 499", result)

    def test_list_write_and_capability_policy(self):
        (self.root / "code.py").write_text("old", encoding="utf-8")
        self.workspace.write("new.py", "created\n")
        self.assertEqual(self.workspace.list_files(), "code.py\nnew.py")
        registry = build_tools(self.root, execution_mode="disabled", allowed_tools={"list_files", "read"})
        self.assertEqual({item["function"]["name"] for item in registry.specs()}, {"list_files", "read"})
        self.assertFalse(json.loads(registry.execute("write", {"path": "x", "content": "x"}))["ok"])

    def test_write_scope_blocks_protected_files(self):
        (self.root / "solution.py").write_text("old", encoding="utf-8")
        (self.root / "test_solution.py").write_text("protected", encoding="utf-8")
        registry = build_tools(
            self.root,
            execution_mode="disabled",
            writable_paths={"solution.py"},
        )
        allowed = json.loads(registry.execute("edit", {"path": "solution.py", "old": "old", "new": "new"}))
        blocked = json.loads(registry.execute("edit", {"path": "test_solution.py", "old": "protected", "new": "fake"}))
        self.assertTrue(allowed["ok"])
        self.assertFalse(blocked["ok"])
        self.assertEqual((self.root / "test_solution.py").read_text(), "protected")

    def test_write_scope_does_not_follow_an_allowed_symlink(self):
        (self.root / "protected.py").write_text("secret", encoding="utf-8")
        os.symlink(self.root / "protected.py", self.root / "solution.py")
        registry = build_tools(self.root, execution_mode="disabled", writable_paths={"solution.py"})
        result = json.loads(registry.execute("edit", {"path": "solution.py", "old": "secret", "new": "changed"}))
        self.assertFalse(result["ok"])
        self.assertEqual((self.root / "protected.py").read_text(), "secret")

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
