"""The Claude Code plugin marketplace in the repository stays consistent with the packaged skill."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PluginTests(unittest.TestCase):
    def setUp(self):
        if not (ROOT / ".claude-plugin" / "marketplace.json").is_file():
            self.skipTest("not a source checkout (the plugin files are not in the sdist)")

    def test_marketplace_lists_the_plugin_by_the_manifest_name(self):
        mp = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertTrue(mp["name"] and mp["owner"]["name"])
        entry = mp["plugins"][0]
        manifest = json.loads((ROOT / "plugins" / "mail-notify" / ".claude-plugin" / "plugin.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(entry["name"], manifest["name"])  # install id and prefix must agree
        self.assertTrue((ROOT / entry["source"]).is_dir())
        self.assertNotIn("..", entry["source"])

    def test_plugin_skill_is_the_packaged_skill(self):
        packaged = (ROOT / "src" / "ccmail" / "skill" / "SKILL.md").read_text(encoding="utf-8")
        in_plugin = (ROOT / "plugins" / "mail-notify" / "skills" / "mail-notify" / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(packaged, in_plugin, "copy src/ccmail/skill/SKILL.md to the plugin folder")

    def test_plugin_version_follows_the_skill_marker(self):
        skill = (ROOT / "src" / "ccmail" / "skill" / "SKILL.md").read_text(encoding="utf-8")
        marker = re.search(r"ccmail-skill-version:\s*([0-9][^\s]*)", skill).group(1)
        manifest = json.loads((ROOT / "plugins" / "mail-notify" / ".claude-plugin" / "plugin.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], marker)


if __name__ == "__main__":
    unittest.main()
