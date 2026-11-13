"""End to end in one process: discovery → registration → sign-in pages → tokens → tools.

The person's Odoo is an in-process fake (no network, no real key).
"""

from __future__ import annotations

import base64
import datetime as dt
import html as htmllib
import hashlib
import json
import os
import re
import secrets
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from starlette.testclient import TestClient

PUBLIC = "https://odoo.connector.test"
CLAUDE_CB = "https://claude.ai/api/mcp/auth_callback"
GOOD_KEY = "test-key-" + "x" * 30
KEY_ENDS_SOON = {"on": False}   # the fake's key record: made just now, ending in 5 days


def fake_odoo_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/web/version":
        return httpx.Response(200, json={"version_info": [19, 0, 0, "final", 0, ""], "version": "19.0"})
    if path.startswith("/json/2/"):
        auth = request.headers.get("authorization", "")
        if not auth:
            return httpx.Response(401, headers={"WWW-Authenticate": "Bearer"}, json={})
        if auth.lower() != f"bearer {GOOD_KEY}":
            return httpx.Response(401, json={"name": "werkzeug.exceptions.Unauthorized"})
        model_method = path[len("/json/2/"):]
        if model_method == "res.users/context_get":
            return httpx.Response(200, json={"uid": 7, "lang": "en_US", "tz": "Europe/Zurich"})
        if model_method == "res.users/read":
            return httpx.Response(200, json=[{"id": 7, "name": "Test Person", "email": "t@example.org",
                                              "login": "t@example.org", "active": True, "company_ids": [1]}])
        if model_method == "res.company/read":
            return httpx.Response(200, json=[{"id": 1, "name": "Example Co"}])
        if model_method == "res.partner/search_count":
            return httpx.Response(200, json=42)
        if model_method == "res.users.apikeys/search_read":
            if not KEY_ENDS_SOON["on"]:
                return httpx.Response(200, json=[])
            now = dt.datetime.now(dt.timezone.utc)
            return httpx.Response(200, json=[{"id": 3, "name": "anything",
                                              "expiration_date": (now + dt.timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S"),
                                              "create_date": (now - dt.timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")}])
    return httpx.Response(404, text="not found")


FAKE_ODOO = httpx.MockTransport(fake_odoo_handler)


class FakeOdooAdapter:
    """The real Odoo adapter, wired to the fake Odoo instead of the network."""

    from open_connectors.apps.odoo import adapter as _real
    NAME = _real.NAME
    CheckError = _real.CheckError
    open_url = staticmethod(_real.open_url)
    secret = staticmethod(_real.secret)
    display = staticmethod(_real.display)
    remote_user = staticmethod(_real.remote_user)

    @staticmethod
    async def check_address(typed, *, denied_hosts=frozenset()):
        from open_connectors.apps.odoo import probe
        return await probe.check_address(typed, transport=FAKE_ODOO, denied_hosts=denied_hosts)

    @staticmethod
    async def identify(server_url, key, database, *, denied_hosts=frozenset()):
        from open_connectors import net
        from open_connectors.apps.odoo import probe
        server = probe.OdooServer(origin=net.normalize_address(server_url), version="", online_hint=False)
        return await probe.identify(server, key, database, transport=FAKE_ODOO, denied_hosts=denied_hosts)


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def csrf_of(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def back_link(html: str) -> str:
    """The 'Back to …' link of the connected step (carries code, state, iss)."""
    return htmllib.unescape(re.search(r'<a class="primary" href="([^"]+)"', html).group(1))


class FlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        conf = Path(cls.tmp.name) / "config.toml"
        conf.write_text(f'public_url = "{PUBLIC}"\nstate_dir = "{cls.tmp.name}/state"\n'
                        f'source_url = "https://code.example.org/open-connectors"\nmaker_label = "by Example · open source"\n')
        from open_connectors import config, server
        settings = config.load(conf)
        os.environ["OPEN_CONNECTORS_JOURNAL"] = f"{cls.tmp.name}/journal.sqlite"
        cls.application = server.Application(settings, adapter=FakeOdooAdapter)
        from open_connectors.apps.odoo import tools
        tools.client.transport = FAKE_ODOO
        cls.client = TestClient(cls.application, base_url=PUBLIC, follow_redirects=False)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.tmp.cleanup()

    # ------------------------------------------------------------------ helpers
    def register(self, redirect=CLAUDE_CB, name="Claude"):
        r = self.client.post("/register", json={"redirect_uris": [redirect], "client_name": name,
                                                "token_endpoint_auth_method": "none",
                                                "grant_types": ["authorization_code", "refresh_token"]})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["client_id"]

    def sign_in(self, client_id, resource=f"{PUBLIC}/mcp", redirect=CLAUDE_CB, address="odoo.example.org"):
        verifier, challenge = pkce()
        r = self.client.get("/authorize", params={"response_type": "code", "client_id": client_id,
                                                  "redirect_uri": redirect, "code_challenge": challenge,
                                                  "code_challenge_method": "S256", "state": "st-1", "resource": resource,
                                                  "scope": "read write send"})
        self.assertEqual(r.status_code, 302, r.text)
        page_path = r.headers["location"]
        page = self.client.get(page_path)
        self.assertEqual(page.status_code, 200)
        r = self.client.post(page_path + "/server", data={"csrf": csrf_of(page.text), "address": address},
                             headers={"sec-fetch-site": "same-origin"})
        self.assertEqual(r.status_code, 303, r.text)
        key_page = self.client.get(page_path)
        r = self.client.post(page_path + "/key", data={"csrf": csrf_of(key_page.text), "key": GOOD_KEY})
        if r.status_code == 303 and r.headers["location"].startswith("/connect/"):   # not verified: one Allow step
            extra = self.client.get(page_path)
            r = self.client.post(page_path + "/allow", data={"csrf": csrf_of(extra.text)})
        self.assertEqual(r.status_code, 200, r.text)                                 # step 3 · connected
        self.assertIn('class="ok"', r.text)
        back = urlsplit(back_link(r.text))
        q = parse_qs(back.query)
        return verifier, q, key_page.text, page_path

    def tokens(self, client_id, code, verifier, redirect=CLAUDE_CB, resource=None):
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                "code_verifier": verifier, "client_id": client_id}
        if resource:
            data["resource"] = resource
        return self.client.post("/token", data=data)

    def mcp(self, token, method, params=None, path="/mcp", rid=1):
        return self.client.post(path, json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}},
                                headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
                                         "MCP-Protocol-Version": "2025-11-25"})

    def initialize(self, token, path="/mcp"):
        return self.mcp(token, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                              "clientInfo": {"name": "test", "version": "1"}}, path=path)

    # ------------------------------------------------------------------ tests
    def test_discovery(self):
        meta = self.client.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(meta["issuer"], PUBLIC)
        self.assertTrue(meta["authorization_response_iss_parameter_supported"])
        self.assertTrue(meta["client_id_metadata_document_supported"])
        self.assertEqual(meta["code_challenge_methods_supported"], ["S256"])
        prm = self.client.get("/.well-known/oauth-protected-resource/mcp").json()
        self.assertEqual(prm["resource"], f"{PUBLIC}/mcp")
        prm2 = self.client.get("/.well-known/oauth-protected-resource/erp.example.org/mcp").json()
        self.assertEqual(prm2["resource"], f"{PUBLIC}/erp.example.org/mcp")
        self.assertEqual(self.client.get("/.well-known/oauth-protected-resource/bad/path/mcp").status_code, 404)

    def test_unauthenticated_mcp_gets_challenge(self):
        r = self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r.status_code, 401)
        self.assertIn(f'resource_metadata="{PUBLIC}/.well-known/oauth-protected-resource/mcp"', r.headers["www-authenticate"])

    def test_full_flow_and_tools(self):
        client_id = self.register()
        verifier, q, key_html, _ = self.sign_in(client_id)
        self.assertEqual(q["state"], ["st-1"])
        self.assertEqual(q["iss"], [PUBLIC])
        self.assertIn("Connect Odoo to Claude", key_html)
        self.assertIn("Connecting to <b>odoo.example.org</b>", key_html)
        self.assertIn("Add API Key", key_html)
        self.assertIn('placeholder="Paste your key here"', key_html)
        self.assertNotIn("Assistant", key_html)                  # no naming rule for the key
        page_wo_foot = re.sub(r'<p class="foot">.*?</p>', " ", key_html, flags=re.S)   # the foot's "{App} connector" is the owner's wording
        visible = re.sub(r"<style>.*?</style>|<[^>]+>", " ", page_wo_foot, flags=re.S).lower()
        self.assertNotRegex(visible, r"\bmcp\b|plugin|connector")   # D386: never these words for people
        t = self.tokens(client_id, q["code"][0], verifier, resource=f"{PUBLIC}/mcp")
        self.assertEqual(t.status_code, 200, t.text)
        tok = t.json()
        self.assertEqual(self.tokens(client_id, q["code"][0], verifier).status_code, 400)  # a code works once
        r = self.initialize(tok["access_token"])
        self.assertEqual(r.status_code, 200, r.text)
        listed = self.mcp(tok["access_token"], "tools/list", rid=2).json()
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertIn("odoo_search_count", names)
        self.assertNotIn("odoo_connect", names)
        call = self.mcp(tok["access_token"], "tools/call", {"name": "odoo_whoami", "arguments": {}}, rid=3).json()
        self.assertFalse(call["result"]["isError"])
        self.assertEqual(json.loads(call["result"]["content"][0]["text"])["uid"], 7)
        status = self.mcp(tok["access_token"], "tools/call", {"name": "odoo_connection_status", "arguments": {}}, rid=4).json()
        self.assertEqual(json.loads(status["result"]["content"][0]["text"])["identity"]["display_name"], "Test Person")
        # The token is bound to /mcp: refused on another resource address.
        other = self.initialize(tok["access_token"], path="/erp.example.org/mcp")
        self.assertEqual(other.status_code, 401)
        # Refresh rotates; the old refresh token then kills the family.
        r1 = self.client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                              "client_id": client_id})
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertEqual(self.initialize(tok["access_token"]).status_code, 401)
        new = r1.json()
        self.assertEqual(self.initialize(new["access_token"]).status_code, 200)
        r2 = self.client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                              "client_id": client_id})
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(self.initialize(new["access_token"]).status_code, 401)

    def test_key_expiry_shown_and_reminded(self):
        KEY_ENDS_SOON["on"] = True
        try:
            client_id = self.register()
            verifier, challenge = pkce()
            r = self.client.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": CLAUDE_CB,
                                                      "code_challenge": challenge, "code_challenge_method": "S256",
                                                      "scope": "read write send"})
            path = r.headers["location"]
            page = self.client.get(path).text
            self.client.post(path + "/server", data={"csrf": csrf_of(page), "address": "odoo.example.org"})
            r = self.client.post(path + "/key", data={"csrf": csrf_of(page), "key": GOOD_KEY})
            self.assertEqual(r.status_code, 200)                              # connected step, no end-date page
            self.assertNotRegex(r.text.lower(), r"ends on|expir|month")
            q = parse_qs(urlsplit(back_link(r.text)).query)
            tok = self.tokens(client_id, q["code"][0], verifier).json()
        finally:
            KEY_ENDS_SOON["on"] = False
        self.initialize(tok["access_token"])
        call = self.mcp(tok["access_token"], "tools/call", {"name": "odoo_whoami", "arguments": {}}, rid=9).json()
        texts = [c["text"] for c in call["result"]["content"]]
        self.assertTrue(any("key ends on" in t and "connect Odoo again" in t for t in texts), texts)
        status = self.mcp(tok["access_token"], "tools/call", {"name": "odoo_connection_status", "arguments": {}}, rid=10).json()
        body = json.loads(status["result"]["content"][0]["text"])
        self.assertTrue(body["credential_ends_soon"])
        self.assertEqual(body["credential_expiry_source"], "odoo")

    def test_change_goes_back_to_the_address(self):
        client_id = self.register()
        verifier, challenge = pkce()
        r = self.client.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": CLAUDE_CB,
                                                  "code_challenge": challenge, "code_challenge_method": "S256"})
        path = r.headers["location"]
        page = self.client.get(path).text
        self.client.post(path + "/server", data={"csrf": csrf_of(page), "address": "odoo.example.org"})
        self.assertIn("Connecting to <b>odoo.example.org</b>", self.client.get(path).text)
        self.client.post(path + "/change", data={"csrf": csrf_of(page)})
        back = self.client.get(path).text
        self.assertIn("Odoo web address", back)
        self.assertIn('placeholder="erp.example.com"', back)

    def test_known_assistant_with_named_server_skips_the_address(self):
        client_id = self.register()
        verifier, challenge = pkce()
        r = self.client.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": CLAUDE_CB,
                                                  "code_challenge": challenge, "code_challenge_method": "S256",
                                                  "resource": f"{PUBLIC}/odoo.example.org/mcp"})
        first = self.client.get(r.headers["location"])
        self.assertEqual(first.status_code, 303)                     # straight on to the key page
        page = self.client.get(r.headers["location"]).text
        self.assertIn("Connecting to <b>odoo.example.org</b>", page)
        self.assertNotIn(">Change<", page)                            # the address is fixed for this connection

    def test_wrong_key_shows_didnt_connect(self):
        client_id = self.register()
        verifier, challenge = pkce()
        r = self.client.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": CLAUDE_CB,
                                                  "code_challenge": challenge, "code_challenge_method": "S256"})
        path = r.headers["location"]; page = self.client.get(path).text
        self.client.post(path + "/server", data={"csrf": csrf_of(page), "address": "odoo.example.org"})
        bad = self.client.post(path + "/key", data={"csrf": csrf_of(page), "key": "wrong-key-000000000000"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("Odoo didn’t connect. Nothing was changed.".replace("’", "'"), bad.text.replace("&#x27;", "'"))
        self.assertIn('class="fail"', bad.text)
        self.assertIn(f'href="{path}"', bad.text)                       # Try again = the key step

    def test_language_switch_sticks(self):
        client_id = self.register()
        verifier, challenge = pkce()
        r = self.client.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": CLAUDE_CB,
                                                  "code_challenge": challenge, "code_challenge_method": "S256"})
        path = r.headers["location"]
        fr = self.client.get(path + "?lang=fr").text
        self.assertIn("Connecter Odoo à Claude", fr)
        self.assertIn("Adresse web de votre Odoo", self.client.get(path).text)   # stays French without the parameter

    def test_revoke_is_disconnect(self):
        client_id = self.register()
        verifier, q, _, _ = self.sign_in(client_id)
        tok = self.tokens(client_id, q["code"][0], verifier).json()
        self.assertEqual(self.client.post("/revoke", data={"token": tok["refresh_token"], "client_id": client_id}).status_code, 200)
        self.assertEqual(self.initialize(tok["access_token"]).status_code, 401)

    def test_wrong_pkce_and_wrong_redirect(self):
        client_id = self.register()
        _, q, _, _ = self.sign_in(client_id)
        self.assertEqual(self.tokens(client_id, q["code"][0], "w" * 50).status_code, 400)
        r = self.client.get("/authorize", params={"response_type": "code", "client_id": client_id,
                                                  "redirect_uri": "https://evil.example/cb", "code_challenge": "c" * 43,
                                                  "code_challenge_method": "S256"})
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("location", r.headers)

    def test_unverified_assistant_and_fixed_server(self):
        client_id = self.register(redirect="https://assistant.example/cb", name="Claude")  # self-named "Claude"
        verifier, challenge = pkce()
        r = self.client.get("/authorize", params={"response_type": "code", "client_id": client_id,
                                                  "redirect_uri": "https://assistant.example/cb", "code_challenge": challenge,
                                                  "code_challenge_method": "S256", "resource": f"{PUBLIC}/odoo.example.org/mcp"})
        page = self.client.get(r.headers["location"]).text
        self.assertIn("It is not an assistant we know", page)
        self.assertIn("(assistant.example)", page)
        self.assertIn('id="address"', page)          # unverified: the person types the server
        r = self.client.post(r.headers["location"] + "/server",
                             data={"csrf": csrf_of(page), "address": "other.example.org"})
        self.assertEqual(r.status_code, 400)          # must match the server named in the address
        self.assertIn("This connection is for odoo.example.org", r.text)
        ok = self.client.post(r.request.url.path, data={"csrf": csrf_of(page), "address": "odoo.example.org"})
        key_page = self.client.get(ok.headers["location"]).text
        r = self.client.post(ok.headers["location"] + "/key", data={"csrf": csrf_of(key_page), "key": GOOD_KEY})
        allow = self.client.get(r.headers["location"]).text
        self.assertIn("Allow “Claude”?", allow)
        self.assertIn("It is not an assistant we know: continue only if you added it yourself.", allow)
        self.assertIn("(assistant.example)", allow)

    def test_cross_site_post_refused(self):
        client_id = self.register()
        verifier, challenge = pkce()
        r = self.client.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": CLAUDE_CB,
                                                  "code_challenge": challenge, "code_challenge_method": "S256"})
        page = self.client.get(r.headers["location"])
        bad = self.client.post(r.headers["location"] + "/server", data={"csrf": csrf_of(page.text), "address": "odoo.example.org"},
                               headers={"sec-fetch-site": "cross-site"})
        self.assertEqual(bad.status_code, 403)
        self.assertEqual(page.headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])


if __name__ == "__main__":
    unittest.main()
