"""The operator's look is served safely, and the connect pages never talk about key expiry."""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from tests import test_flow as base


class Brand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        brand = root / "brand"; (brand / "fonts").mkdir(parents=True)
        (brand / "look.css").write_text("body{color:#123}")
        (brand / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
        (brand / "fonts" / "f.woff2").write_bytes(b"wOF2")
        (brand / "notes.toml").write_text("secret = 1")
        (root / "outside.css").write_text("x")
        conf = root / "c.toml"
        conf.write_text(f'public_url = "{base.PUBLIC}"\nstate_dir = "{root}/state"\nsource_url = "https://code.example.org/x"\n'
                        f'[brand]\ndir = "{brand}"\nstyles = ["look.css"]\nlogo = "logo.svg"\nname = "Example"\n')
        from open_connectors import config, server
        os.environ["OPEN_CONNECTORS_JOURNAL"] = f"{root}/journal.sqlite"
        cls.app = server.Application(config.load(conf), adapter=base.FakeOdooAdapter)
        cls.client = TestClient(cls.app, base_url=base.PUBLIC, follow_redirects=False); cls.client.__enter__()
        cls.t = base.FlowTest(); cls.t.client = cls.client

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        from open_connectors.pages import flow
        flow.BRAND.update(styles=(), logo="", name="")
        cls.tmp.cleanup()

    def test_brand_files_only(self):
        self.assertEqual(self.client.get("/brand/look.css").headers["content-type"].split(";")[0], "text/css")
        self.assertEqual(self.client.get("/brand/fonts/f.woff2").status_code, 200)
        for bad in ("/brand/notes.toml", "/brand/%2e%2e/outside.css", "/brand/../outside.css", "/brand//etc/passwd", "/brand/nope.css"):
            with self.subTest(bad=bad):
                self.assertEqual(self.client.get(bad).status_code, 404)

    def test_branded_pages_and_no_expiry_words(self):
        cid = self.t.register(); v, ch = base.pkce()
        r = self.client.get("/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": base.CLAUDE_CB,
                                                  "code_challenge": ch, "code_challenge_method": "S256"})
        loc = r.headers["location"]; p1 = self.client.get(loc).text
        self.assertIn('href="/brand/look.css"', p1)
        self.client.post(loc + "/server", data={"csrf": base.csrf_of(p1), "address": "odoo.example.org"})
        p2 = self.client.get(loc).text
        for html in (p1, p2):
            visible = re.sub(r"<style>.*?</style>|<[^>]+>", " ", html, flags=re.S).lower()
            self.assertNotRegex(visible, r"\bends?\b|expir|month|signed in as")


if __name__ == "__main__":
    unittest.main()
