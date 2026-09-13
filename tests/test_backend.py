import tempfile
import unittest
from pathlib import Path

from deepagents.middleware import FilesystemMiddleware
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


if __name__ == "__main__":
    unittest.main()
