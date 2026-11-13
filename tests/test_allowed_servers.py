"""Decision 2: an operator can limit the connector to listed servers."""

from __future__ import annotations

import dataclasses
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from starlette.testclient import TestClient

from tests import test_flow as base

CONTACTED: list[str] = []


def recording_handler(request: httpx.Request) -> httpx.Response:
    CONTACTED.append(request.url.host)
    return base.fake_odoo_handler(request)


class ListedAdapter(base.FakeOdooAdapter):
    @staticmethod
    async def check_address(typed, *, denied_hosts=frozenset()):
        from open_connectors.apps.odoo import probe
        return await probe.check_address(typed, transport=httpx.MockTransport(recording_handler), denied_hosts=denied_hosts)


class AllowedServers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        conf = Path(cls.tmp.name) / "config.toml"
        conf.write_text(f'public_url = "{base.PUBLIC}"\nstate_dir = "{cls.tmp.name}/state"\n'
                        f'source_url = "https://code.example.org/x"\nallowed_servers = ["https://Odoo.Example.org/"]\n')
        from open_connectors import config, server
        cls.settings = config.load(conf)
        os.environ["OPEN_CONNECTORS_JOURNAL"] = f"{cls.tmp.name}/journal.sqlite"
        cls.application = server.Application(cls.settings, adapter=ListedAdapter)
        from open_connectors.apps.odoo import tools
        tools.client.transport = base.FAKE_ODOO
        cls.client = TestClient(cls.application, base_url=base.PUBLIC, follow_redirects=False)
        cls.client.__enter__()
        cls.t = base.FlowTest()
        cls.t.client = cls.client

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.tmp.cleanup()

    def test_listing_is_normalised(self):
        self.assertEqual(self.settings.allowed_servers, frozenset({"odoo.example.org"}))

    def test_unlisted_refused_without_contact(self):
        client_id = self.t.register()
        verifier, challenge = base.pkce()
        r = self.client.get("/authorize", params={"response_type": "code", "client_id": client_id,
                                                  "redirect_uri": base.CLAUDE_CB, "code_challenge": challenge,
                                                  "code_challenge_method": "S256"})
        page = self.client.get(r.headers["location"]).text
        CONTACTED.clear()
        bad = self.client.post(r.headers["location"] + "/server", data={"csrf": base.csrf_of(page), "address": "other.example.org"})
        self.assertEqual(bad.status_code, 403)
        self.assertIn("only with the servers its operator lists", bad.text)
        self.assertEqual(CONTACTED, [])       # the unlisted host was never contacted

    def test_listed_works_then_delisting_stops_it(self):
        client_id = self.t.register()
        verifier, q, _, _ = self.t.sign_in(client_id)
        tok = self.t.tokens(client_id, q["code"][0], verifier).json()
        self.assertEqual(self.t.initialize(tok["access_token"]).status_code, 200)
        auth = self.application.auth
        original = auth.s
        try:
            auth.s = dataclasses.replace(original, allowed_servers=frozenset({"elsewhere.example.org"}))
            self.assertEqual(self.t.initialize(tok["access_token"]).status_code, 401)
        finally:
            auth.s = original
        self.assertEqual(self.t.initialize(tok["access_token"]).status_code, 200)


if __name__ == "__main__":
    unittest.main()
