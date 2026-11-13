"""Odoo: is this address a usable Odoo (step B), and whose key is this (step C)?

Step B never needs a key:

1. ``GET /web/version`` must answer JSON with ``version_info``. The first item is
   an int major version (19 or newer), or ``"saas~19"`` with a minor of at least 1.
   If ``/web/version`` is missing, ``POST /web/webclient/version_info`` is asked
   only to name the old version in the message.
2. An unauthenticated ``POST /json/2/res.users/context_get`` must answer 401 with
   ``WWW-Authenticate: Bearer``: proof that the JSON-2 API (used by every tool)
   is reachable on this address.

Step C checks the pasted key by asking Odoo who it belongs to
(``res.users/context_get`` then ``res.users/read``). A database name is asked
only when Odoo answers that it needs one.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from open_connectors import net

STRIP_SUFFIXES = ("/odoo", "/web", "/web/login", "/odoo/action-base.action_res_users_my")
USER_AGENT = "open-mcp-connectors (Odoo check)"
JUST_MADE_SECONDS = 30 * 60                  # a key made this recently is the one just pasted
NODB_MARKERS = ("NOT_FOUND_NODB", "database not found", "No database", "nodb")


class OdooCheckError(Exception):
    """A plain, person-facing refusal. ``code`` selects the words; ``detail`` fills them."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class OdooServer:
    origin: net.Origin
    version: str            # e.g. "19.0" or "saas~19.1"
    online_hint: bool       # *.odoo.com or saas~: warn about the Custom plan

    @property
    def url(self) -> str:
        return self.origin.url


@dataclass(frozen=True)
class OdooIdentity:
    uid: int
    name: str
    email: str
    companies: list[str] = field(default_factory=list)
    database: str = ""
    expires_at: int | None = None           # unix time; None = the key does not expire
    expiry_source: str = "unknown"          # "odoo" (read from Odoo) | "estimated" | "unknown"


def _parse_version(info: Any) -> tuple[bool, str]:
    """Return (usable, label) from ``version_info``; never compares version strings."""
    if not isinstance(info, list) or not info:
        return False, ""
    major = info[0]
    minor = info[1] if len(info) > 1 else 0
    if isinstance(major, int) and not isinstance(major, bool):
        return major >= 19, f"{major}.{minor if isinstance(minor, int) else 0}"
    if isinstance(major, str) and major.startswith("saas~"):
        try:
            saas_major = int(major.split("~", 1)[1])
        except ValueError:
            return False, major
        minor_int = minor if isinstance(minor, int) and not isinstance(minor, bool) else 0
        usable = saas_major > 19 or (saas_major == 19 and minor_int >= 1)
        return usable, f"saas~{saas_major}.{minor_int}"
    return False, str(major)


def _json_or_none(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


async def check_address(raw: str, *, transport: httpx.AsyncBaseTransport | None = None,
                        denied_hosts: frozenset[str] = frozenset()) -> OdooServer:
    """Step B for Odoo. Raises :class:`net.AddressError` or :class:`OdooCheckError`."""
    origin = net.normalize_address(raw, strip_suffixes=STRIP_SUFFIXES)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    async with net.client_for(timeout=net.PROBE_TIMEOUT, denied_hosts=denied_hosts,
                              headers=headers, transport=transport) as client:
        try:
            async with client.stream("GET", origin.join("/web/version")) as response:
                body = await net.read_capped(response, net.PROBE_MAX_BYTES)
                status = response.status_code
        except httpx.TimeoutException:
            raise OdooCheckError("timeout") from None
        except httpx.TransportError:
            raise OdooCheckError("unreachable") from None
        if 300 <= status < 400:
            raise OdooCheckError("redirect")
        payload = _json_or_none(body) if status == 200 else None
        if not isinstance(payload, dict) or "version_info" not in payload:
            old = await _old_version(client, origin) if status in (404, 405) else None
            if old:
                raise OdooCheckError("too_old", old)
            raise OdooCheckError("not_odoo")
        usable, label = _parse_version(payload.get("version_info"))
        if not usable:
            raise OdooCheckError("too_old", label or str(payload.get("version", "")))
        try:
            async with client.stream("POST", origin.join("/json/2/res.users/context_get"),
                                     json={}, headers={"Content-Type": "application/json"}) as response:
                await net.read_capped(response, net.PROBE_MAX_BYTES)
                status = response.status_code
                challenge = response.headers.get("www-authenticate", "")
        except httpx.TransportError:
            raise OdooCheckError("unreachable") from None
        if status != 401 or not challenge.lower().startswith("bearer"):
            raise OdooCheckError("no_api")
    online = origin.host.endswith(".odoo.com") or label.startswith("saas~")
    return OdooServer(origin=origin, version=label, online_hint=online)


async def _old_version(client: httpx.AsyncClient, origin: net.Origin) -> str | None:
    try:
        async with client.stream("POST", origin.join("/web/webclient/version_info"),
                                 json={"jsonrpc": "2.0", "params": {}},
                                 headers={"Content-Type": "application/json"}) as response:
            body = await net.read_capped(response, net.PROBE_MAX_BYTES)
            if response.status_code != 200:
                return None
    except (httpx.TransportError, net.AddressError):
        return None
    payload = _json_or_none(body)
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, dict):
        _, label = _parse_version(result.get("server_version_info"))
        return label or None
    return None


async def identify(server: OdooServer, api_key: str, database: str = "", *,
                   transport: httpx.AsyncBaseTransport | None = None,
                   denied_hosts: frozenset[str] = frozenset()) -> OdooIdentity:
    """Step C for Odoo: whose key is this? Raises :class:`OdooCheckError`.

    ``needs_database`` is raised when Odoo hosts several databases and cannot
    pick one from the address; the page then asks for its name once.
    """
    key = (api_key or "").strip()
    if not key or any(ch.isspace() for ch in key) or len(key) > 512:
        raise OdooCheckError("bad_key")
    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": f"bearer {key}",
        "Content-Type": "application/json; charset=utf-8",
    }
    db = (database or "").strip()
    if db:
        headers["X-Odoo-Database"] = db
    async with net.client_for(timeout=net.DEFAULT_TIMEOUT, denied_hosts=denied_hosts,
                              headers=headers, transport=transport) as client:
        context = await _call(client, server.origin, "res.users", "context_get", {})
        uid = context.get("uid") if isinstance(context, dict) else None
        if not isinstance(uid, int) or isinstance(uid, bool):
            raise OdooCheckError("not_odoo")
        rows = await _call(client, server.origin, "res.users", "read",
                           {"ids": [uid], "fields": ["name", "email", "company_ids"]})
        row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
        companies: list[str] = []
        company_ids = row.get("company_ids") or []
        if isinstance(company_ids, list) and company_ids:
            try:
                found = await _call(client, server.origin, "res.company", "read",
                                    {"ids": [c for c in company_ids if isinstance(c, int)][:20], "fields": ["name"]})
                companies = [str(c.get("name", "")) for c in found if isinstance(c, dict) and c.get("name")]
            except OdooCheckError:
                companies = []
        expires_at, expiry_source = await _key_expiry(client, server.origin, uid)
    name = str(row.get("name") or "").strip()
    email = row.get("email")
    return OdooIdentity(uid=uid, name=name, email=str(email).strip() if isinstance(email, str) else "",
                        companies=companies, database=db, expires_at=expires_at, expiry_source=expiry_source)


def _parse_odoo_datetime(value: object) -> int | None:
    if value in (None, False, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("invalid Odoo date")
    parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp())


async def _key_expiry(client: httpx.AsyncClient, origin: net.Origin, uid: int) -> tuple[int | None, str]:
    """When does the pasted key end? The key may have any name.

    Odoo does not let a person read which key row a pasted key is (its index is not an
    API field), but it does show their own key rows with creation time and end date.
    The key a person pastes is the one they just made: exactly one of their keys
    created in the last JUST_MADE_SECONDS → its date (no date = never ends). Otherwise
    the key is treated as open-ended (most keys are): no date, no reminder.
    """
    try:
        rows = await _call(client, origin, "res.users.apikeys", "search_read", {
            "domain": [["user_id", "=", uid]],
            "fields": ["expiration_date", "create_date"],
            "limit": 20, "order": "create_date desc"})
        if isinstance(rows, list):
            now = time.time()
            recent = [r for r in rows if isinstance(r, dict) and r.get("create_date")
                      and now - (_parse_odoo_datetime(r["create_date"]) or 0) <= JUST_MADE_SECONDS]
            if len(recent) == 1:
                return _parse_odoo_datetime(recent[0].get("expiration_date")), "odoo"
    except (OdooCheckError, ValueError):
        pass
    return None, "unknown"


async def _call(client: httpx.AsyncClient, origin: net.Origin, model: str, method: str, payload: dict) -> Any:
    try:
        async with client.stream("POST", origin.join(f"/json/2/{model}/{method}"), json=payload) as response:
            body = await net.read_capped(response, 1024 * 1024)
            status = response.status_code
    except httpx.TimeoutException:
        raise OdooCheckError("timeout") from None
    except httpx.TransportError:
        raise OdooCheckError("unreachable") from None
    if status == 200:
        data = _json_or_none(body)
        if data is None:
            raise OdooCheckError("not_odoo")
        return data
    text = body[:2000].decode("utf-8", "replace")
    if status == 404 and any(marker.lower() in text.lower() for marker in NODB_MARKERS):
        raise OdooCheckError("needs_database")
    if status in (401, 403):
        raise OdooCheckError("key_refused")
    if 300 <= status < 400:
        raise OdooCheckError("redirect")
    raise OdooCheckError("odoo_error", str(status))
