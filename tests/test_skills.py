import tempfile
import unittest
from pathlib import Path

from melonclaw.services.skills import SkillCatalog


class SkillCatalogTests(unittest.TestCase):
    def test_lists_frontmatter_and_uses_first_heading_as_display_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "alpha-skill").mkdir()
            (root / "alpha-skill" / "SKILL.md").write_text(
                "---\n"
                "name: alpha-skill\n"
                "description: Alpha description\n"
                "---\n"
                "# Alpha display name skill\n",
                encoding="utf-8",
            )
            catalog = SkillCatalog(root)

            items = catalog.list()

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].id, "alpha-skill")
            self.assertEqual(items[0].display_name, "Alpha display name")
            self.assertEqual(items[0].description, "Alpha description")
            self.assertEqual(items[0].virtual_path, "/skills/alpha-skill/SKILL.md")
            self.assertEqual(
                items[0].public_dict(),
                {
                    "id": "alpha-skill",
                    "display_name": "Alpha display name",
                    "description": "Alpha description",
                },
            )

    def test_skips_missing_and_mismatched_skill_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "without-skill-file").mkdir()
            (root / "bad-name").mkdir()
            (root / "bad-name" / "SKILL.md").write_text(
                "---\nname: another-name\ndescription: invalid\n---\n",
                encoding="utf-8",
            )
            (root / ".hidden").mkdir()
            (root / ".hidden" / "SKILL.md").write_text(
                "---\nname: hidden\ndescription: invalid\n---\n",
                encoding="utf-8",
            )

            self.assertEqual(SkillCatalog(root).list(), [])

    def test_humanizes_id_when_skill_has_no_heading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tushare-fetcher").mkdir()
            (root / "tushare-fetcher" / "SKILL.md").write_text(
                "---\n"
                "name: tushare-fetcher\n"
                "description: Fetch financial data\n"
                "---\n",
                encoding="utf-8",
            )

            items = SkillCatalog(root).list()

            self.assertEqual(items[0].display_name, "Tushare Fetcher")


if __name__ == "__main__":
    unittest.main()
