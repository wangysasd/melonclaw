import tempfile
import unittest
from pathlib import Path

from deepagents.middleware import FilesystemMiddleware
from deepagents.middleware.filesystem import _route_host_path_prompt
from langgraph.store.memory import InMemoryStore

from melonclaw.backend.factory import build_agent_backend


class BackendFactoryTests(unittest.TestCase):
    def test_permission_rules_are_accepted_with_execution_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            backend, _, permissions = build_agent_backend(
                Path(directory), memory_store=InMemoryStore()
            )

            # Deep Agents 0.7 rejects permissions on a default backend that
            # supports execute unless every rule is scoped to a route.
            FilesystemMiddleware(backend=backend, _permissions=permissions)

            write_result = backend.write("/.artifacts/check.txt", "ok")
            read_result = backend.read("/.artifacts/check.txt")

            self.assertIsNone(write_result.error)
            self.assertEqual(read_result.file_data["content"], "ok")
            self.assertEqual(
                (Path(directory) / ".artifacts" / "check.txt").read_text(
                    encoding="utf-8"
                ),
                "ok",
            )

    def test_workspace_internal_routes_omit_host_paths_for_stable_prompt_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills_dir = root / "skills-snapshot"
            skills_dir.mkdir()
            conv_a = root / "conversations" / "conv-aaa"
            conv_b = root / "conversations" / "conv-bbb"
            backend_a, _, _ = build_agent_backend(
                conv_a,
                memory_store=InMemoryStore(),
                skill_dirs=(("/skills/", skills_dir),),
            )
            backend_b, _, _ = build_agent_backend(
                conv_b,
                memory_store=InMemoryStore(),
                skill_dirs=(("/skills/", skills_dir),),
            )

            prompt_a = _route_host_path_prompt(backend_a)
            prompt_b = _route_host_path_prompt(backend_b)

            self.assertEqual(prompt_a, prompt_b)
            self.assertNotIn("conv-aaa", prompt_a)
            self.assertNotIn("conv-bbb", prompt_b)
            self.assertIn("`/skills/` ->", prompt_a)
            self.assertIn("- `/.attachments/`", prompt_a)
            self.assertIn("- `/.artifacts/`", prompt_a)


if __name__ == "__main__":
    unittest.main()
