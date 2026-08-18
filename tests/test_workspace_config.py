import json
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from scripts.mythify_workspace import cmd_workspace_show, load_workspace_config


class WorkspaceConfigTests(unittest.TestCase):
    def workspace(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        state = root / ".mythify"
        state.mkdir()
        (root / ".git").mkdir()
        return temporary, root, state

    def test_merges_local_repository_path_and_strengthens_boundaries(self):
        temporary, root, state = self.workspace()
        self.addCleanup(temporary.cleanup)
        (root / "service" / ".git").mkdir(parents=True)
        (state / "workspace.json").write_text(json.dumps({
            "version": 1,
            "task_isolation": "none",
            "frozen_paths": ["protocol"],
            "authorization": {"external_writes_require_explicit_scope": True},
            "repositories": [{"id": "app", "path": ".", "primary": True, "allowed_paths": ["scripts"]}],
        }))
        (state / "workspace.local.json").write_text(json.dumps({
            "task_isolation": "worktree",
            "frozen_paths": ["docs"],
            "repositories": [{"id": "app", "path": "service"}],
        }))
        result = load_workspace_config(state)
        self.assertEqual(result["configuration"]["task_isolation"], "worktree")
        self.assertEqual(result["configuration"]["frozen_paths"], ["protocol", "docs"])
        self.assertEqual(result["configuration"]["repositories"][0]["resolved_path"], str((root / "service").resolve()))
        self.assertEqual(result["mutation"], "none")

    def test_local_config_cannot_weaken_shared_guards(self):
        temporary, _, state = self.workspace()
        self.addCleanup(temporary.cleanup)
        (state / "workspace.json").write_text(json.dumps({
            "task_isolation": "worktree",
            "authorization": {"approval": True},
            "repositories": [{"id": "app", "path": "."}],
        }))
        (state / "workspace.local.json").write_text(json.dumps({"task_isolation": "none", "authorization": {"approval": False}}))
        with self.assertRaisesRegex(ValueError, "may not weaken task_isolation"):
            load_workspace_config(state)

    def test_paths_cannot_escape_workspace_root(self):
        temporary, _, state = self.workspace()
        self.addCleanup(temporary.cleanup)
        (state / "workspace.json").write_text(json.dumps({"repositories": [{"id": "outside", "path": ".."}]}))
        with self.assertRaisesRegex(ValueError, "escapes workspace root"):
            load_workspace_config(state)

    def test_malformed_repository_collection_fails_cleanly(self):
        temporary, _, state = self.workspace()
        self.addCleanup(temporary.cleanup)
        (state / "workspace.json").write_text(json.dumps({"repositories": {"app": "."}}))
        with self.assertRaisesRegex(ValueError, "repositories must be an array"):
            load_workspace_config(state)

    def test_command_outputs_valid_json_without_mutation(self):
        temporary, _, state = self.workspace()
        self.addCleanup(temporary.cleanup)
        (state / "workspace.json").write_text(json.dumps({"repositories": [{"id": "app", "path": ".", "primary": True}]}))
        output = StringIO()
        with redirect_stdout(output):
            code = cmd_workspace_show(Namespace(json_output=True), state)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "valid")


if __name__ == "__main__":
    unittest.main()
