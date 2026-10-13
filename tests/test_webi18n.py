"""Web app language: catalog integrity, the language the daemon resolves, and the static files being served."""
import json
import os
import re
import unittest
from pathlib import Path

from ccmail import i18n
from helpers import TmpHome

WEB = Path(__file__).resolve().parents[1] / "src" / "ccmail" / "web"


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def js_strings(path: Path):
    """('"key"': '"value"') pairs and [regex, replacement] pairs written in a catalog file."""
    text = path.read_text(encoding="utf-8")
    lit = r'"((?:[^"\\\r\n]|\\.)*)"'
    pairs = re.findall(r"(?:^|[\s{,])" + lit + r"\s*:\s*" + lit, text)
    pats = re.findall(r"\[\s*" + lit + r"\s*,\s*" + lit + r"\s*\]", text)
    un = lambda s: json.loads('"' + s + '"')  # noqa: E731
    return [(un(k), un(v)) for k, v in pairs], [(un(a), un(b)) for a, b in pats]


class CatalogTests(unittest.TestCase):
    def load(self):
        exact, pats = [], []
        for name in ("lang-en.js", "lang-en-app.js"):
            e, p = js_strings(WEB / name)
            exact += e
            pats += p
        return exact, pats

    def test_catalog_is_substantial_and_free_of_duplicates_with_different_values(self):
        exact, pats = self.load()
        self.assertGreater(len(exact), 400)
        self.assertGreater(len(pats), 80)
        seen = {}
        for k, v in exact:
            if k in seen:
                self.assertEqual(seen[k], v, k)
            seen[k] = v

    def test_every_key_exists_in_the_page_sources(self):
        """A key that no longer appears in index.html / app.js is a stale entry (or a typo): it would never match."""
        exact, _ = self.load()
        daemon = "".join(f.read_text(encoding="utf-8") for f in WEB.parent.glob("*.py"))  # texts the daemon sends
        src = norm((WEB / "index.html").read_text(encoding="utf-8") + (WEB / "app.js").read_text(encoding="utf-8")
                   + daemon)
        src = src.replace("\'", "'").replace("&rsquo;", "'").replace("&amp;", "&").replace("&laquo;", "«") \
                 .replace("&raquo;", "»").replace("&middot;", "·").replace("&hellip;", "…")
        missing = [k for k, _ in exact if norm(k) not in src]
        self.assertEqual(missing, [], f"{len(missing)} catalog keys not found in the page sources")

    def test_patterns_compile_and_replacements_fit(self):
        _, pats = self.load()
        for rx, to in pats:
            c = re.compile(rx, re.S)
            used = [int(x) for x in re.findall(r"\$(\d)", to)]
            self.assertLessEqual(max(used or [0]), c.groups, (rx, to))

    def test_values_keep_placeholders_of_the_original(self):
        exact, _ = self.load()
        for k, v in exact:
            self.assertEqual(k.count("«"), v.count("«") + 0 if "«" in v else k.count("«"), k)
            self.assertTrue(v.strip(), k)


class ResolveTests(TmpHome):
    def setUp(self):
        super().setUp()
        self.old = os.environ.pop("CCMAIL_LANG", None)
        for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
            os.environ.pop(var, None)
        import tempfile
        self._cd = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._cd.cleanup)
        self.claude = Path(self._cd.name)
        self._old_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude)
        i18n._claude_cache.update(key=None, value=None)

    def tearDown(self):
        if self.old is not None:
            os.environ["CCMAIL_LANG"] = self.old
        if self._old_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._old_cfg
        super().tearDown()

    def write(self, value):
        (self.claude / "settings.json").write_text(json.dumps({"language": value, "hooks": {"x": 1}}), encoding="utf-8")

    def test_auto_follows_claude_code_language(self):
        for value, want in (("italian", "it"), ("Italiano", "it"), ("English", "en"), ("en", "en")):
            self.write(value)
            i18n._claude_cache.update(key=None, value=None)
            self.assertEqual(i18n.resolve({"language": "auto"}), want, value)

    def test_unknown_claude_language_falls_back_to_the_system(self):
        self.write("french")
        os.environ["LANG"] = "it_IT.UTF-8"
        self.assertEqual(i18n.resolve({"language": "auto"}), "it")
        os.environ["LANG"] = "en_US.UTF-8"
        self.assertEqual(i18n.resolve({"language": "auto"}), "en")

    def test_explicit_setting_and_env_override_win(self):
        self.write("italian")
        self.assertEqual(i18n.resolve({"language": "en"}), "en")
        os.environ["CCMAIL_LANG"] = "en"
        self.assertEqual(i18n.resolve({"language": "auto"}), "en")

    def test_missing_or_broken_settings_file(self):
        os.environ["LANG"] = "en_GB.UTF-8"
        self.assertEqual(i18n.resolve({"language": "auto"}), "en")
        (self.claude / "settings.json").write_text("{not json", encoding="utf-8")
        i18n._claude_cache.update(key=None, value=None)
        self.assertEqual(i18n.resolve({"language": "auto"}), "en")


class StaticTests(unittest.TestCase):
    def test_scripts_are_listed_in_order_and_servable(self):
        from ccmail import server
        html = (WEB / "index.html").read_text(encoding="utf-8")
        order = [html.index(f'src="/{n}"') for n in ("lang.js", "lang-en.js", "lang-en-app.js", "app.js")]
        self.assertEqual(order, sorted(order))
        for n in ("lang.js", "lang-en.js", "lang-en-app.js"):
            self.assertTrue(server._STATIC_NAME.match("/" + n), n)
            self.assertTrue((WEB / n).is_file())


if __name__ == "__main__":
    unittest.main()
