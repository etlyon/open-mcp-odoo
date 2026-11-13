"""Odoo-only security fixes (SECFIX13). No network. No credential file."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

import httpx

from open_connectors import net
from open_connectors.apps.odoo import tools
from open_connectors.apps.odoo.client import OdooAPIError, OdooClient
from open_connectors.apps.odoo.credential import OdooCredential
from open_connectors.apps.odoo.guards import read_context, require_readable_fields, require_readable_model
from open_connectors.apps.odoo.tools import DEFAULT_ALLOWED_MODELS, DEFAULT_MUTABLE_MODELS
from open_connectors.oauth.server import CURRENT
from open_connectors.store import Connection


class BigBody(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{" + b" " * (net.DEFAULT_MAX_BYTES + 8) + b"}", request=request)


class OdooSecurity(unittest.IsolatedAsyncioTestCase):
    def test_default_models_are_an_explicit_list(self):
        self.assertNotIn("*", DEFAULT_ALLOWED_MODELS)
        self.assertNotIn("*", DEFAULT_MUTABLE_MODELS)
        self.assertIn("res.partner", DEFAULT_ALLOWED_MODELS)
        self.assertIn("res.partner", DEFAULT_MUTABLE_MODELS)
        self.assertNotIn("res.users", DEFAULT_ALLOWED_MODELS)
        self.assertTrue(DEFAULT_MUTABLE_MODELS < DEFAULT_ALLOWED_MODELS)

    def test_reads_refuse_blocked_models_fields_and_context(self):
        with self.assertRaises(OdooAPIError):
            require_readable_model("res.users")
        with self.assertRaises(OdooAPIError):
            require_readable_model("mail.message")
        with self.assertRaises(OdooAPIError):
            require_readable_fields(["signup_token"])
        with self.assertRaises(OdooAPIError):
            require_readable_fields(["name", "message_ids"])
        self.assertEqual(read_context({"lang": "fr_FR", "tz": "Europe/Zurich"}),
                         {"lang": "fr_FR", "tz": "Europe/Zurich"})
        with self.assertRaises(OdooAPIError):
            read_context({"lang": "fr_FR", "bin_size": True})

    async def test_odoo_answer_is_capped(self):
        client = OdooClient(base_url="https://odoo.example.org", database="db", api_key="k",
                            allowed_models={"res.partner"}, transport=BigBody())
        with self.assertRaises(OdooAPIError) as caught:
            await client.call("res.partner", "search_read", {"domain": [], "fields": ["name"]})
        self.assertEqual(caught.exception.category, "unreachable")

    async def test_internal_tools_do_not_bypass_blocked_model_reads(self):
        class Fake:
            def __init__(self):
                self.calls = []

            def require_model(self, model):
                return None

            def credential_for_current_account(self):
                return OdooCredential("not-a-real-key", "https://odoo.example", "db")

            async def call(self, model, method, payload, **options):
                self.calls.append((model, method, payload, options))
                if (model, method) == ("res.users", "context_get"):
                    return {"uid": 7, "lang": "en_US", "danger": "must-not-leave"}
                if (model, method) == ("res.partner", "read"):
                    return [{"id": 3, "display_name": "Test record"}]
                raise AssertionError(f"unexpected privileged read: {model}.{method}")

        fake = Fake()
        saved = tools.client
        tools.client = fake
        token = CURRENT.set(Connection(
            conn_id="c1", app="odoo", server="https://odoo.example", remote_user="db:7",
            client_id="test", resource="test", display={"name": "Test Person", "email": "t@example.test"},
            secret={"api_key": "not-a-real-key", "database": "db"}, scope="read write",
        ))
        try:
            who = json.loads(await tools.odoo_whoami())
            self.assertEqual(who, {"uid": 7, "lang": "en_US"})
            status = json.loads(await tools.odoo_connection_status())
            self.assertEqual(status["identity"]["display_name"], "Test Person")
            catalog = json.loads(await tools.odoo_model_catalog(tools.ModelCatalogInput(query="partner")))
            self.assertEqual([row["model"] for row in catalog["models"]], ["res.partner"])
            preview = json.loads(await tools._impl_odoo_schedule_activity(
                tools.ScheduleActivityInput(
                    model="res.partner", record_id=3, summary="Follow up", execute=False,
                )
            ))
            self.assertEqual(preview["assigned_user"], {"id": 7, "name": "Test Person"})
        finally:
            CURRENT.reset(token)
            tools.client = saved
        self.assertTrue(fake.calls)
        self.assertFalse(any(
            model in {"ir.model", "res.users.apikeys"}
            or (model == "res.users" and method != "context_get")
            for model, method, _payload, _options in fake.calls
        ))

    async def test_delete_reference_checks_use_the_blocked_model_guard(self):
        with self.assertRaises(OdooAPIError):
            await tools._records_absent(SimpleNamespace(model="res.users", ids=[7], reason="test"))
        self.assertEqual(
            await tools._records_ref_state(SimpleNamespace(model="mail.message", ids=[9])),
            {"error": "OdooAPIError"},
        )


if __name__ == "__main__":
    unittest.main()
