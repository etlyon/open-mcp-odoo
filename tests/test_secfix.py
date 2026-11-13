"""Security fixes that are the same in every connector (SECFIX13).

No network. No credential file.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastmcp.exceptions import ToolError

from open_connectors import net, policy
from open_connectors.config import DEFAULT_ASSISTANTS, Settings
from open_connectors.oauth.clients import ClientError, ClientInfo, cimd_client_redirects, known_assistant
from open_connectors.oauth.server import narrow_scope
from open_connectors.pages.flow import error_text
from open_connectors.throttle import Throttle


def _settings() -> Settings:
    return Settings(
        public_url="https://connector.example.org",
        state_dir=Path("/tmp"),
        source_url="https://example.org/source",
        assistants=DEFAULT_ASSISTANTS,
    )


def _cimd(host: str) -> ClientInfo:
    return ClientInfo(
        client_id=f"https://{host}/client.json",
        kind="cimd",
        redirect_uris=("https://assistant.example/cb",),
        client_name="Claude",
        application_type="web",
        auth_method="none",
    )


class Ctx:
    request_context = None

    def __init__(self) -> None:
        self.request_state = None
        self.input_responses = None
        self.session = type("S", (), {})()
        self.session.client_capabilities = type("C", (), {"elicitation": None})()


class Tool:
    name = "odoo_delete_records"


class SharedSecurity(unittest.IsolatedAsyncioTestCase):
    def test_cimd_host_matches_exactly(self):
        settings = _settings()
        self.assertIsNone(known_assistant(settings, _cimd("evil.claude.ai"), "https://assistant.example/cb"))
        self.assertIsNone(known_assistant(settings, _cimd("notclaude.ai"), "https://assistant.example/cb"))
        self.assertIsNone(known_assistant(settings, _cimd("claude.ai.evil.example"), "https://assistant.example/cb"))
        named = known_assistant(settings, _cimd("claude.ai"), "https://assistant.example/cb")
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named.name, "Claude")

    def test_cimd_redirects_are_validated_and_native_only_when_every_uri_is(self):
        mixed, kind = cimd_client_redirects(["https://claude.ai/cb", "http://127.0.0.1:9/cb"])
        self.assertEqual(kind, "web")
        self.assertEqual(len(mixed), 2)
        native, kind = cimd_client_redirects(["http://127.0.0.1/cb", "com.example.app:/cb"])
        self.assertEqual(kind, "native")
        self.assertEqual(len(native), 2)
        with self.assertRaises(ClientError):
            cimd_client_redirects(["javascript:alert(1)"])
        with self.assertRaises(ClientError):
            cimd_client_redirects(["https://evil.example/cb#frag"])
        with self.assertRaises(ClientError):
            cimd_client_redirects(["http://evil.example/cb"])

    def test_scope_is_not_widened(self):
        self.assertEqual(narrow_scope(""), "")
        self.assertEqual(narrow_scope("offline_access"), "offline_access")
        self.assertEqual(narrow_scope("read write admin"), "read write")
        self.assertEqual(narrow_scope("send read"), "send read")
        self.assertTrue(policy.scope_allows("send", "read"))
        self.assertTrue(policy.scope_allows("write", "read"))
        self.assertFalse(policy.scope_allows("read", "write"))
        self.assertFalse(policy.scope_allows("write", "send"))
        self.assertFalse(policy.scope_allows("", "read"))
        self.assertFalse(policy.scope_allows("offline_access", "read"))

    def test_policy_file_is_loaded_and_fails_closed(self):
        saved = os.environ.get("OPEN_CONNECTORS_ACCOUNTS_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["OPEN_CONNECTORS_ACCOUNTS_DIR"] = tmp
                self.assertEqual(policy.policy_for_account("acct1").deny, frozenset())
                folder = Path(tmp) / "acct1"
                folder.mkdir()
                (folder / "policy.yaml").write_text("nope: true\n", encoding="utf-8")
                with self.assertRaises(policy.PolicyError):
                    policy.policy_for_account("acct1")
                (folder / "policy.yaml").write_text("deny:\n  - dangerous_tool\n", encoding="utf-8")
                self.assertEqual(policy.policy_for_account("acct1").deny, frozenset({"dangerous_tool"}))
                with self.assertRaises(policy.PolicyError):
                    policy.policy_for_account("../acct1")
        finally:
            if saved is None:
                os.environ.pop("OPEN_CONNECTORS_ACCOUNTS_DIR", None)
            else:
                os.environ["OPEN_CONNECTORS_ACCOUNTS_DIR"] = saved

    def test_pinned_transport_ignores_ssl_cert_file(self):
        saved = os.environ.get("SSL_CERT_FILE")
        os.environ["SSL_CERT_FILE"] = "/no/such/open-connectors-cert.pem"
        try:
            transport = net.PinnedTransport()
        finally:
            if saved is None:
                os.environ.pop("SSL_CERT_FILE", None)
            else:
                os.environ["SSL_CERT_FILE"] = saved
        inner = transport._inner
        self.assertIs(getattr(inner, "_trust_env", False), False)

    def test_throttle_is_per_key(self):
        clock = {"now": 1000.0}
        limiter = Throttle(limit=2, window=60, clock=lambda: clock["now"])
        self.assertEqual(limiter.take("one"), 0.0)
        self.assertEqual(limiter.take("one"), 0.0)
        self.assertGreater(limiter.take("one"), 0)
        self.assertEqual(limiter.take("two"), 0.0)

    def test_version_detail_is_a_short_number(self):
        text = error_text("too_old", "en", "odoo", "19.0-20251222 <script>alert(1)</script>")
        self.assertIn("19.0", text)
        self.assertNotIn("script", text)
        self.assertNotIn("20251222", text)
        hub = error_text("nc_too_old", "en", "nextcloud", "not a version")
        self.assertNotIn("not a version", hub)

    async def test_ask_does_not_issue_a_token(self):
        middleware = policy.AccountToolPolicy(app="odoo", connect_tool="odoo_connect")
        with self.assertRaises(ToolError) as missing:
            await middleware._ask(None, None, Ctx(), Tool(), "acct", "fp", None, {})
        self.assertIn("nothing ran", str(missing.exception))
        self.assertNotIn("confirm_token", str(missing.exception))
        with self.assertRaises(ToolError) as forged:
            await middleware._ask(None, None, Ctx(), Tool(), "acct", "fp", "1." + "ab" * 32, {})
        self.assertIn("confirm_token", str(forged.exception))
        self.assertIn("nothing ran", str(forged.exception))


if __name__ == "__main__":
    unittest.main()
