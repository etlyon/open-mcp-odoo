"""Against a REAL Odoo 19 (a throwaway test instance), when one is given.

Set OC_TEST_ODOO (e.g. http://192.0.2.10:8069, a private test server) and
OC_TEST_ODOO_KEY_FILE (a file holding an API key named "Assistant"). The
connector only ever calls public https servers, so this test reaches the
private test server through a transport that maps https://odoo19.test to it;
the address safety itself is tested elsewhere. Skipped when not configured.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import httpx

TEST_URL = os.environ.get("OC_TEST_ODOO", "")
KEY_FILE = os.environ.get("OC_TEST_ODOO_KEY_FILE", "")
FAKE_HOST = "odoo19.test"


class MapToTestServer(httpx.AsyncBaseTransport):
    """https://odoo19.test/... → OC_TEST_ODOO/... (test only)."""

    def __init__(self, target: str):
        self.target = httpx.URL(target)
        self.inner = httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == FAKE_HOST, request.url
        url = request.url.copy_with(scheme=self.target.scheme, host=self.target.host, port=self.target.port)
        headers = httpx.Headers(request.headers)
        headers["host"] = f"{self.target.host}:{self.target.port}"
        return await self.inner.handle_async_request(
            httpx.Request(request.method, url, headers=headers, stream=request.stream, extensions=request.extensions))


@unittest.skipUnless(TEST_URL and KEY_FILE, "no test Odoo configured")
class RealOdoo(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from open_connectors.apps.odoo import probe
        self.probe = probe
        self.transport = MapToTestServer(TEST_URL)
        self.key = Path(KEY_FILE).read_text().strip()

    async def test_address_check(self):
        server = await self.probe.check_address(f"https://{FAKE_HOST}/odoo", transport=self.transport)
        self.assertTrue(server.version.startswith("19"))

    async def test_identity_and_expiry_from_odoo(self):
        server = await self.probe.check_address(FAKE_HOST, transport=self.transport)
        who = await self.probe.identify(server, self.key, transport=self.transport)
        self.assertGreater(who.uid, 0)
        self.assertTrue(who.name)
        # A key made in the last 30 minutes gives Odoo's own date; otherwise the end is unknown
        # and the key is treated as open-ended (no date, no reminder).
        self.assertIn(who.expiry_source, ("odoo", "unknown"))
        if who.expiry_source == "odoo":
            self.assertIsNotNone(who.expires_at)
        else:
            self.assertIsNone(who.expires_at)

    async def test_wrong_key_refused(self):
        server = await self.probe.check_address(FAKE_HOST, transport=self.transport)
        with self.assertRaises(self.probe.OdooCheckError) as ctx:
            await self.probe.identify(server, "0" * 40, transport=self.transport)
        self.assertEqual(ctx.exception.code, "key_refused")

    async def test_tools_on_real_odoo(self):
        """The carried-over tools, through the connection client, against the real JSON-2 API."""
        from open_connectors.apps.odoo import tools
        from open_connectors.oauth.server import CURRENT
        from open_connectors.store import Connection
        conn = Connection(conn_id="real-test", app="odoo", server=f"https://{FAKE_HOST}", remote_user="2",
                          client_id="t", resource="r", display={}, secret={"api_key": self.key, "database": ""})
        token = CURRENT.set(conn)
        saved = tools.client.transport
        tools.client.transport = self.transport
        try:
            who = json.loads(await tools.odoo_whoami())
            self.assertGreater(who["uid"], 0)
            count = json.loads(await tools.odoo_search_count(tools.SearchInput(model="res.partner", domain=[])))
            self.assertIsInstance(count.get("count", count), (int, dict))
            status = json.loads(await tools.odoo_connection_status())
            self.assertEqual(status["status"], "connected", status)
        finally:
            tools.client.transport = saved
            CURRENT.reset(token)


if __name__ == "__main__":
    unittest.main()
