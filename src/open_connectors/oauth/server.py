"""The sign-in server (OAuth 2.1 authorization server) and the gate in front of the tools.

One process serves one app on one host. Addresses:

* ``/mcp``                    the connector; the person types their server (step B)
* ``/<server-host>/mcp``      the connector for one named server (not for an app with no
                              server to pick, adapter ``NO_ADDRESS``: there only ``/mcp``)
* ``/.well-known/...``        discovery (RFC 8414, RFC 9728)
* ``/register``               dynamic client registration (RFC 7591)
* ``/authorize``              start of a sign-in → the pages under ``/connect/``
* ``/token``, ``/revoke``     tokens (PKCE S256, refresh rotation) and revocation (RFC 7009)

Every authorization response carries ``iss`` (RFC 9207). Every issued token is
bound to exactly one connection and one resource address.
"""

from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from open_connectors import net
from open_connectors.config import Settings
from open_connectors.oauth import clients as client_registry
from open_connectors.pages import flow as pages
from open_connectors.store import Connection, Store, StoreError, digest

LOG = logging.getLogger("open_connectors.oauth")
CURRENT: contextvars.ContextVar[Connection | None] = contextvars.ContextVar("open_connectors_connection", default=None)
MAX_KEY_ATTEMPTS = 5
# An app whose step C is a login flow in the app itself (adapter ``LOGIN_FLOW``): the waiting page
# refreshes itself; each refresh asks the app at most once per POLL_SECONDS per sign-in. The sign-ins
# waiting on one server share POLL_PER_HOST asks a minute FAIRLY: each one asks every
# max(POLL_SECONDS, 60 * waiting / POLL_PER_HOST) seconds, so many sign-ins slow every one of them down
# equally and none is starved. A sign-in counts as waiting while its page refreshed in the last
# WAITING_SECONDS. Starts have no shared per-server budget that others could use up: one sign-in may
# start at most MAX_LOGIN_STARTS login flows (then the paste fallback stays open), each visitor a few
# per 10 minutes, under the cap on open sign-ins.
POLL_SECONDS = 2.0
POLL_PER_HOST = 120
WAITING_SECONDS = 30.0
MAX_LOGIN_STARTS = 3
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class RateLimiter:
    """Small in-memory sliding-window limiter (one process per app)."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def allow(self, bucket: str, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        q = self._hits[(bucket, key)]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def client_ip(request: Request) -> str:
    # Never read X-Forwarded-For here: its first entry is whatever the visitor sent (most proxies
    # append). The server's proxy-header handling (--forwarded-allow-ips) has already replaced
    # request.client with the last address before the trusted proxies, which is the visitor.
    return (request.client.host if request.client else "") or "-"


def _b64url_sha256(value: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(value.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def _with_params(uri: str, params: dict[str, str]) -> str:
    parts = urlsplit(uri)
    query = parse_qsl(parts.query, keep_blank_values=True) + [(k, v) for k, v in params.items() if v is not None]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _origin(uri: str) -> str:
    parts = urlsplit(uri)
    return f"{parts.scheme}://{parts.netloc}"


class _Granted:
    """The login name a login flow granted, for ``secret()`` when the identity check failed."""

    def __init__(self, login_name: str):
        self.login_name = login_name


class AuthServer:
    def __init__(self, settings: Settings, store: Store, app: Any):
        """``app`` is the app adapter: ``.NAME``, ``.check_address``, ``.identify``, ``.open_url``, ``.remote_user``, ``.display``.

        Optional: ``LOGIN_FLOW`` with ``.start_login``, ``.poll_login``, ``.login_url`` and ``LOGIN_FLOW_SECONDS``
        (step C happens in the app itself, a pasted key is the fallback); ``EXTRA_FIELD`` (the name of the
        key step's second box, default ``database``); ``async on_disconnect(server_url, secret, *, denied_hosts)``
        (called when a connection is revoked or its code exchange is refused, and when a sign-in is cancelled,
        changed or finished by a paste after the app granted a secret: always on the server that granted it);
        ``NO_ADDRESS`` with ``SERVER_URL`` (an app that works by account: no address step, the sign-in starts at
        the key step on that one server, and there is nothing to change; then no ``check_address``);
        ``connection_allowed(connection)`` (False stops a stored connection at once, e.g. the operator's list changed).
        """
        self.s = settings
        self.store = store
        self.app = app
        self.limits = RateLimiter()
        self._waiting: dict[str, dict[str, float]] = {}     # server host -> {sign-in: last refresh}

    # ------------------------------------------------------------------ resources
    def parse_resource(self, resource: str) -> tuple[bool, str | None]:
        """(valid, fixed_server_host) for one of our resource addresses."""
        base = self.s.public_url
        if resource == f"{base}/mcp":
            return True, None
        if self._no_address():
            return False, None              # no server to pick: never /<host>/mcp
        if resource.startswith(base + "/") and resource.endswith("/mcp"):
            middle = resource[len(base) + 1:-len("/mcp")]
            if middle and "/" not in middle:
                try:
                    origin = net.normalize_address(middle)
                except net.AddressError:
                    return False, None
                if origin.base_path == "" and origin.host == middle.lower():
                    return True, origin.host
        return False, None

    def resource_for_path(self, path: str) -> str | None:
        resource = self.s.public_url + path
        ok, _ = self.parse_resource(resource)
        return resource if ok else None

    # ------------------------------------------------------------------ discovery
    def as_metadata(self) -> dict[str, Any]:
        base = self.s.public_url
        return {
            "issuer": self.s.issuer,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "registration_endpoint": f"{base}/register",
            "revocation_endpoint": f"{base}/revoke",
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "revocation_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
            "scopes_supported": ["offline_access"],
            "service_documentation": self.s.source_url,
        }

    async def as_metadata_route(self, request: Request) -> Response:
        return JSONResponse(self.as_metadata(), headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "max-age=300"})

    async def prm_route(self, request: Request) -> Response:
        tail = request.path_params.get("rest", "")
        resource = self.resource_for_path("/" + tail) if tail else f"{self.s.public_url}/mcp"
        if not resource:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return JSONResponse({
            "resource": resource,
            "authorization_servers": [self.s.issuer],
            "bearer_methods_supported": ["header"],
            "scopes_supported": [],
            "resource_name": pages.APP_NAMES.get(self.app.NAME, self.app.NAME),
            "resource_documentation": self.s.source_url,
        }, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "max-age=300"})

    # ------------------------------------------------------------------ registration (RFC 7591)
    async def register(self, request: Request) -> Response:
        ip = client_ip(request)
        if not self.limits.allow("register", ip, 20, 3600) or self.store.count_clients_since(time.time() - 86400) > 5000:
            return JSONResponse({"error": "temporarily_unavailable"}, status_code=429)
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            return self._reg_error("invalid_client_metadata", "body must be JSON")
        if not isinstance(body, dict):
            return self._reg_error("invalid_client_metadata", "body must be a JSON object")
        app_type = str(body.get("application_type") or "web")
        if app_type not in ("web", "native"):
            return self._reg_error("invalid_client_metadata", "application_type must be web or native")
        uris = body.get("redirect_uris")
        if not isinstance(uris, list) or not uris or len(uris) > 10 or not all(isinstance(u, str) and len(u) < 2000 for u in uris):
            return self._reg_error("invalid_redirect_uri", "redirect_uris must be a list of URIs")
        try:
            uris = [client_registry.validate_redirect_uri(u, app_type) for u in uris]
        except client_registry.ClientError as exc:
            return self._reg_error(exc.code, exc.description)
        if not self.s.other_assistants:
            known = {u for a in self.s.assistants for u in a.redirect_uris}
            if not all(u in known for u in uris):
                return self._reg_error("invalid_redirect_uri", "this connector accepts only the assistants its operator lists")
        method = str(body.get("token_endpoint_auth_method") or "client_secret_basic")
        if method not in ("none", "client_secret_post", "client_secret_basic"):
            return self._reg_error("invalid_client_metadata", "unsupported token_endpoint_auth_method")
        grants = body.get("grant_types") or ["authorization_code"]
        if not isinstance(grants, list) or not set(grants) <= {"authorization_code", "refresh_token"}:
            return self._reg_error("invalid_client_metadata", "unsupported grant_types")
        responses = body.get("response_types") or ["code"]
        if responses != ["code"]:
            return self._reg_error("invalid_client_metadata", "response_types must be [\"code\"]")
        name = "".join(ch for ch in str(body.get("client_name") or "")[:80] if ch.isprintable()).strip()
        client_id = "dcr-" + secrets.token_urlsafe(18)
        secret = secrets.token_urlsafe(32) if method != "none" else None
        meta = {"redirect_uris": uris, "client_name": name, "application_type": app_type,
                "token_endpoint_auth_method": method, "grant_types": grants, "response_types": ["code"]}
        self.store.save_client(client_id, "dcr", meta, digest(secret) if secret else None)
        out = {"client_id": client_id, "client_id_issued_at": int(time.time()), **meta}
        if secret:
            out.update(client_secret=secret, client_secret_expires_at=0)
        return JSONResponse(out, status_code=201, headers={"Cache-Control": "no-store"})

    @staticmethod
    def _reg_error(code: str, description: str) -> Response:
        return JSONResponse({"error": code, "error_description": description}, status_code=400)

    # ------------------------------------------------------------------ authorize
    def _error_page(self, request: Request, code: str, status: int = 400) -> Response:
        lang = pages.pick_language(request.headers.get("accept-language"))
        body = pages.error_page(lang=lang, app=self.app.NAME,
                                message=pages.error_text(code, lang, self.app.NAME), source_url=self.s.source_url)
        return HTMLResponse(body, status_code=status, headers={**SECURITY_HEADERS, "Content-Security-Policy": self._csp()})

    def _csp(self, form_target: str | None = None) -> str:
        form = "'self'" + (f" {form_target}" if form_target else "")
        return (f"default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self'; font-src 'self'; "
                f"form-action {form}; frame-ancestors 'none'; base-uri 'none'")

    def _client_redirect(self, uri: str, params: dict[str, str]) -> Response:
        return RedirectResponse(_with_params(uri, {**params, "iss": self.s.issuer}), status_code=303,
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    async def authorize(self, request: Request) -> Response:
        q = request.query_params
        ip = client_ip(request)
        if not self.limits.allow("authorize", ip, 60, 3600):
            return self._error_page(request, "too_many", 429)
        client_id = q.get("client_id", "")
        redirect_uri = q.get("redirect_uri", "")
        try:
            client = await client_registry.load_client(client_id, self.store, self.s)
        except client_registry.ClientError:
            return self._error_page(request, "bad_request")
        if not redirect_uri and len(client.redirect_uris) == 1:
            redirect_uri = client.redirect_uris[0]
        if not client_registry.redirect_allowed(client, redirect_uri):
            return self._error_page(request, "bad_request")
        # From here on, errors go back to the assistant (with state and iss).
        state = q.get("state", "")
        assistant = client_registry.known_assistant(self.s, client, redirect_uri)
        if assistant is None and not self.s.other_assistants:
            return self._error_page(request, "not_allowed", 403)
        if q.get("response_type") != "code":
            return self._client_redirect(redirect_uri, {"error": "unsupported_response_type", "state": state})
        challenge, method = q.get("code_challenge", ""), q.get("code_challenge_method", "")
        if not challenge or method != "S256" or not (43 <= len(challenge) <= 128):
            return self._client_redirect(redirect_uri, {"error": "invalid_request",
                                                        "error_description": "PKCE S256 is required", "state": state})
        resource = q.get("resource") or f"{self.s.public_url}/mcp"
        valid, fixed_host = self.parse_resource(resource)
        if not valid:
            return self._client_redirect(redirect_uri, {"error": "invalid_target", "state": state})
        if self.store.count_open_flows() >= self.s.max_open_flows:
            return self._error_page(request, "busy", 503)
        cookie_key = secrets.token_urlsafe(32)
        flow = {
            "client_id": client.client_id, "redirect_uri": redirect_uri, "state": state, "code_challenge": challenge,
            "resource": resource, "scope": q.get("scope", ""), "fixed_host": fixed_host,
            "verified_name": assistant.name if assistant else None, "claimed_name": client.client_name,
            "return_host": urlsplit(redirect_uri).netloc or redirect_uri.split(":", 1)[0],
            "csrf": secrets.token_urlsafe(24), "cookie_check": digest(cookie_key), "step": "server", "attempts": 0,
        }
        if self._no_address():
            # No server to pick: the sign-in starts at the key step, on the app's one server.
            flow.update(step="key", server_url=self.app.SERVER_URL, server_host=urlsplit(self.app.SERVER_URL).hostname or "")
        flow_id = self.store.new_flow(flow)
        response = RedirectResponse(f"/connect/{flow_id}", status_code=302, headers={"Cache-Control": "no-store"})
        response.set_cookie(self._cookie_name(flow_id), cookie_key, max_age=20 * 60, path="/", secure=True,
                            httponly=True, samesite="lax")
        return response

    @staticmethod
    def _cookie_name(flow_id: str) -> str:
        return "__Host-oc-" + hashlib.sha256(flow_id.encode()).hexdigest()[:16]

    # ------------------------------------------------------------------ the pages
    def _load_flow(self, request: Request) -> tuple[str, dict[str, Any], str] | Response:
        flow_id = request.path_params["flow_id"]
        flow = self.store.get_flow(flow_id)
        cookie_key = request.cookies.get(self._cookie_name(flow_id), "")
        if not flow or not cookie_key or not hmac.compare_digest(flow.get("cookie_check", ""), digest(cookie_key)):
            return self._error_page(request, "expired", 400)
        return flow_id, flow, cookie_key

    async def _checked_post(self, request: Request) -> tuple[str, dict[str, Any], str, Any] | Response:
        site = request.headers.get("sec-fetch-site")
        if site not in (None, "same-origin"):
            return self._error_page(request, "bad_request", 403)
        loaded = self._load_flow(request)
        if isinstance(loaded, Response):
            return loaded
        flow_id, flow, cookie_key = loaded
        form = await request.form()
        if not hmac.compare_digest(str(form.get("csrf", "")), flow.get("csrf", "")):
            return self._error_page(request, "expired", 400)
        return flow_id, flow, cookie_key, form

    def _asking(self, flow: dict[str, Any]) -> pages.Asking:
        return pages.Asking(verified_name=flow.get("verified_name"), claimed_name=flow.get("claimed_name") or "?",
                            return_host=flow.get("return_host") or "?")

    def _login_flow(self) -> bool:
        return bool(getattr(self.app, "LOGIN_FLOW", False))

    def _no_address(self) -> bool:
        return bool(getattr(self.app, "NO_ADDRESS", False))

    @property
    def _denied(self) -> frozenset[str]:
        return self.s.denied_hosts | {self.s.public_host}

    def _render(self, request: Request, flow_id: str, flow: dict[str, Any], error: str = "", value: str = "",
                status: int = 200) -> Response:
        lang = self._lang(request, flow)
        err = pages.error_text(error, lang, self.app.NAME, flow.get("error_detail", "")) if error else ""
        common = dict(lang=lang, app=self.app.NAME, flow_id=flow_id, csrf=flow["csrf"], asking=self._asking(flow),
                      source_url=self.s.source_url)
        form_target = None
        if flow["step"] == "server":
            body = pages.server_page(**common, value=value or "", error=err)
        elif flow["step"] == "key" and self._no_address():
            body = pages.token_page(**common, open_url=self.app.open_url(flow["server_url"]), error=err)
        elif flow["step"] == "key" and self._login_flow() and flow.get("paste"):
            body = pages.paste_page(**common, host=flow["server_host"], open_url=self.app.open_url(flow["server_url"]),
                                    user=flow.get("user", ""), error=err, can_change=not flow.get("fixed_host"))
        elif flow["step"] == "key" and self._login_flow():
            login = flow.get("login") or {}
            body = pages.grant_page(**common, host=flow["server_host"], can_change=not flow.get("fixed_host"),
                                    login_url=self.app.login_url(flow["server_url"], login.get("flow_token", "")))
        elif flow["step"] == "key":
            body = pages.key_page(**common, host=flow["server_host"], open_url=self.app.open_url(flow["server_url"]),
                                  online_hint=bool(flow.get("online_hint")), ask_database=bool(flow.get("ask_database")),
                                  database=flow.get("database", ""), error=err, can_change=not flow.get("fixed_host"))
        else:
            ident = flow["identity"]
            who = ident.get("label") or ident.get("name") or "?"
            body = pages.allow_page(**common, host=flow["server_host"], who=who)
            form_target = _origin(flow["redirect_uri"])
        headers = {**SECURITY_HEADERS, "Content-Security-Policy": self._csp(form_target)}
        return HTMLResponse(body, status_code=status, headers=headers)

    @staticmethod
    def _lang(request: Request, flow: dict[str, Any] | None = None) -> str:
        chosen = (flow or {}).get("lang")
        return chosen if chosen in pages.LANGS else pages.pick_language(request.headers.get("accept-language"))

    def _failed(self, request: Request, flow_id: str, flow: dict[str, Any], code: str, status: int = 400) -> Response:
        """Step 4 · Didn't connect: say why, Try again (the same step), Not now."""
        lang = self._lang(request, flow)
        body = pages.failed_page(lang=lang, app=self.app.NAME, flow_id=flow_id, csrf=flow["csrf"], asking=self._asking(flow),
                                 source_url=self.s.source_url, reason=pages.error_text(code, lang, self.app.NAME, flow.get("error_detail", "")),
                                 retry_url=f"/connect/{flow_id}")
        return HTMLResponse(body, status_code=status, headers={**SECURITY_HEADERS, "Content-Security-Policy": self._csp()})

    async def page(self, request: Request) -> Response:
        loaded = self._load_flow(request)
        if isinstance(loaded, Response):
            return loaded
        flow_id, flow, cookie_key = loaded
        chosen = request.query_params.get("lang")
        if chosen in pages.LANGS and chosen != flow.get("lang"):
            flow["lang"] = chosen
            self.store.update_flow(flow_id, flow)
        if flow["step"] == "server" and flow.get("fixed_host") and flow.get("verified_name") and not flow.get("auto_tried"):
            # A known assistant on a per-server address: the server is known, go straight to the key.
            flow["auto_tried"] = True
            self.store.update_flow(flow_id, flow)
            return await self._check_server(request, flow_id, flow, flow["fixed_host"])
        if flow["step"] == "key" and self._login_flow():
            if request.query_params.get("how") == "paste" and not flow.get("paste"):
                flow["paste"] = True                      # the fallback: paste a user name and app password
                self.store.update_flow(flow_id, flow)
            if not flow.get("paste"):
                return await self._grant_step(request, flow_id, flow, cookie_key)
        return self._render(request, flow_id, flow)

    async def _grant_step(self, request: Request, flow_id: str, flow: dict[str, Any], cookie_key: str) -> Response:
        """Step C in the app itself: start its login flow once, then look for the grant on each refresh."""
        host = flow["server_host"]
        login = flow.get("login")
        now = time.time()
        if not login:
            starts = int(flow.get("login_starts", 0))
            if starts >= MAX_LOGIN_STARTS:
                # No more login flows for this sign-in: the paste fallback stays open, with the reason.
                flow["paste"] = True
                self.store.update_flow(flow_id, flow)
                return self._render(request, flow_id, flow, error="login_limit", status=429)
            if not self.limits.allow("login-start", client_ip(request), 30, 600):
                return self._failed(request, flow_id, flow, "busy", status=429)
            try:
                started = await self.app.start_login(flow["server_url"], denied_hosts=self._denied)
            except (net.AddressError, self.app.CheckError) as exc:
                flow["error_detail"] = getattr(exc, "detail", "") or ""
                self.store.update_flow(flow_id, flow)
                return self._failed(request, flow_id, flow, exc.code)
            flow["login"] = {"flow_token": started.flow_token, "started": now, "polled": now, "server": flow["server_url"],
                             "poll": self.store.seal_flow_value(flow_id, cookie_key, {"poll_token": started.poll_token})}
            flow["login_starts"] = starts + 1
            self.store.update_flow(flow_id, flow)
            # The app's login flow lasts as long as a sign-in: keep this sign-in (and its cookie) open with it.
            keep = int(getattr(self.app, "LOGIN_FLOW_SECONDS", 20 * 60)) + 120
            self.store.extend_flow(flow_id, keep)
            response = self._render(request, flow_id, flow)
            response.set_cookie(self._cookie_name(flow_id), cookie_key, max_age=keep, path="/", secure=True,
                                httponly=True, samesite="lax")
            return response
        if now - float(login.get("started", 0)) > float(getattr(self.app, "LOGIN_FLOW_SECONDS", 20 * 60)):
            flow["login"] = None                     # Try again starts a new login flow
            self._stop_waiting(host, flow_id)
            self.store.update_flow(flow_id, flow)
            return self._failed(request, flow_id, flow, "login_expired")
        if now - float(login.get("polled", 0)) < self._poll_interval(host, flow_id, now):
            return self._render(request, flow_id, flow)
        login["polled"] = now
        self.store.update_flow(flow_id, flow)
        try:
            poll_token = self.store.open_flow_value(flow_id, cookie_key, login.get("poll", ""))["poll_token"]
            granted = await self.app.poll_login(flow["server_url"], poll_token, denied_hosts=self._denied)
        except (StoreError, KeyError, net.AddressError, self.app.CheckError) as exc:
            flow["login"] = None
            self._stop_waiting(host, flow_id)
            flow["error_detail"] = getattr(exc, "detail", "") or ""
            self.store.update_flow(flow_id, flow)
            return self._failed(request, flow_id, flow, getattr(exc, "code", "login_failed"))
        if granted is None:
            return self._render(request, flow_id, flow)  # still waiting
        login_name, key = granted
        flow["login"] = None                         # the app hands its answer out once
        self._stop_waiting(host, flow_id)
        try:
            identity = await self.app.identify(flow["server_url"], key, login_name, denied_hosts=self._denied)
        except (net.AddressError, self.app.CheckError) as exc:
            # Granted but unusable: do not leave the new secret alive in the app.
            await self._disconnect(flow["server_url"], self.app.secret(key, _Granted(login_name)))
            flow["error_detail"] = getattr(exc, "detail", "") or ""
            self.store.update_flow(flow_id, flow)
            return self._failed(request, flow_id, flow, exc.code)
        # The app made this secret for us: delete it on cancel, on the server that made it (never another).
        flow.update(granted=True, granted_server=flow["server_url"])
        return self._identified(request, flow_id, flow, cookie_key, key, identity)

    def _poll_interval(self, host: str, flow_id: str, now: float) -> float:
        """Seconds between two asks for this sign-in: its fair share of the server's budget."""
        for name, waiting in list(self._waiting.items()):      # forget sign-ins whose page went away
            for other, last in list(waiting.items()):
                if now - last > WAITING_SECONDS:
                    del waiting[other]
            if not waiting:
                del self._waiting[name]
        seen = self._waiting.setdefault(host, {})
        seen[flow_id] = now
        return max(POLL_SECONDS, 60.0 * len(seen) / POLL_PER_HOST)

    def _stop_waiting(self, host: str, flow_id: str) -> None:
        seen = self._waiting.get(host)
        if seen is not None:
            seen.pop(flow_id, None)
            if not seen:
                self._waiting.pop(host, None)

    async def _drop_pending_login(self, flow_id: str, flow: dict[str, Any], cookie_key: str) -> None:
        """Leaving the app's login flow while it is open (Change, Not now, or a pasted app password instead).

        The app may already have made the secret (the person clicked Grant access a moment ago, before the
        next refresh asked): ask once more, and delete whatever comes back. Never fails the caller.
        """
        login = flow.get("login")
        flow["login"] = None
        if not login or not self._login_flow():
            return
        server_url = login.get("server") or flow.get("server_url", "")
        self._stop_waiting(flow.get("server_host", ""), flow_id)
        try:
            poll_token = self.store.open_flow_value(flow_id, cookie_key, login.get("poll", ""))["poll_token"]
            granted = await self.app.poll_login(server_url, poll_token, denied_hosts=self._denied)
        except Exception as exc:              # leaving goes ahead whatever the app answers
            LOG.info("pending login not collected app=%s error=%s", self.app.NAME, type(exc).__name__)
            return
        if granted:
            login_name, key = granted
            await self._disconnect(server_url, self.app.secret(key, _Granted(login_name)))

    async def _drop_granted(self, flow_id: str, flow: dict[str, Any], cookie_key: str) -> None:
        """Delete a secret the app granted for this sign-in, on the server that granted it, and forget it."""
        granted = None
        if flow.get("granted") and flow.get("granted_server"):
            try:
                granted = self.store.flow_secret(flow_id, cookie_key)
            except StoreError:
                granted = None
        server_url = flow.get("granted_server")
        flow.update(granted=False, granted_server=None)
        if granted and server_url:
            await self._disconnect(server_url, granted)

    async def post_server(self, request: Request) -> Response:
        checked = await self._checked_post(request)
        if isinstance(checked, Response):
            return checked
        flow_id, flow, _, form = checked
        if flow["step"] != "server" or self._no_address():
            return RedirectResponse(f"/connect/{flow_id}", status_code=303)
        if not self.limits.allow("probe", client_ip(request), 30, 600):
            return self._render(request, flow_id, flow, error="too_many", status=429)
        return await self._check_server(request, flow_id, flow, str(form.get("address", ""))[:300])

    async def _check_server(self, request: Request, flow_id: str, flow: dict[str, Any], typed: str) -> Response:
        try:
            # The operator's list is checked BEFORE any network contact with the typed host.
            checked = net.normalize_address(typed, strip_suffixes=getattr(self.app, "STRIP_SUFFIXES", ()),
                                            root_only=bool(getattr(self.app, "ROOT_ONLY", False)))
            if not self.s.server_allowed(checked.host):
                flow["error_detail"] = ""
                return self._render(request, flow_id, flow, error="not_listed", value=typed, status=403)
        except net.AddressError as exc:
            return self._render(request, flow_id, flow, error=exc.code, value=typed, status=400)
        try:
            server = await self.app.check_address(typed, denied_hosts=self._denied)
        except (net.AddressError, self.app.CheckError) as exc:
            flow["error_detail"] = getattr(exc, "detail", "") or ""
            return self._render(request, flow_id, flow, error=exc.code, value=typed, status=400)
        fixed = flow.get("fixed_host")
        if fixed and server.origin.host != fixed:
            flow["error_detail"] = fixed
            return self._render(request, flow_id, flow, error="wrong_server", value=typed, status=400)
        flow.update(step="key", server_url=server.url, server_host=server.origin.host,
                    online_hint=server.online_hint, server_version=server.version, error_detail="")
        self.store.update_flow(flow_id, flow)
        return RedirectResponse(f"/connect/{flow_id}", status_code=303)

    async def post_key(self, request: Request) -> Response:
        checked = await self._checked_post(request)
        if isinstance(checked, Response):
            return checked
        flow_id, flow, cookie_key, form = checked
        if flow["step"] != "key":
            return RedirectResponse(f"/connect/{flow_id}", status_code=303)
        flow["attempts"] = int(flow.get("attempts", 0)) + 1
        if flow["attempts"] > MAX_KEY_ATTEMPTS or not self.limits.allow("key", client_ip(request), 20, 600):
            self.store.update_flow(flow_id, flow)
            return self._failed(request, flow_id, flow, "too_many", status=429)
        key = str(form.get("key", ""))
        extra_field = getattr(self.app, "EXTRA_FIELD", "database")
        database = str(form.get(extra_field, ""))[:255 if extra_field == "user" else 120]
        try:
            identity = await self.app.identify(flow["server_url"], key, database, denied_hosts=self._denied)
        except self.app.CheckError as exc:
            if extra_field == "user":
                flow["user"] = database
            else:
                flow["database"] = database
            flow["error_detail"] = getattr(exc, "detail", "") or ""
            if exc.code == "needs_database":
                flow["ask_database"] = True          # not a failure: one more field on the same step
                self.store.update_flow(flow_id, flow)
                return self._render(request, flow_id, flow, error=exc.code, status=400)
            self.store.update_flow(flow_id, flow)
            return self._failed(request, flow_id, flow, exc.code)
        except net.AddressError as exc:
            self.store.update_flow(flow_id, flow)
            return self._failed(request, flow_id, flow, exc.code)
        # Pasted while the app's login flow was still open: a Grant access clicked meanwhile is not needed.
        await self._drop_pending_login(flow_id, flow, cookie_key)
        flow.update(granted=False, granted_server=None)   # a pasted secret is the person's own: never deleted on cancel
        return self._identified(request, flow_id, flow, cookie_key, key, identity)

    def _identified(self, request: Request, flow_id: str, flow: dict[str, Any], cookie_key: str, key: str,
                    identity: Any) -> Response:
        """Step C succeeded: hold the secret for this browser, then "Allow?" (unknown assistant) or connect."""
        self.store.put_flow_secret(flow_id, cookie_key, self.app.secret(key, identity))
        shown = self.app.display(identity)
        flow.update(identity=shown, remote_user=self.app.remote_user(identity), error_detail="")
        if not flow.get("verified_name"):
            flow["step"] = "allow"            # an assistant we do not know: one explicit yes
        else:
            self.store.update_flow(flow_id, flow)
            return self._finish(flow_id, flow, cookie_key, request)   # pressing Connect was the consent
        self.store.update_flow(flow_id, flow)
        return RedirectResponse(f"/connect/{flow_id}", status_code=303)

    async def post_change(self, request: Request) -> Response:
        checked = await self._checked_post(request)
        if isinstance(checked, Response):
            return checked
        flow_id, flow, cookie_key, _ = checked
        # Change is offered on the address and key steps only; a stale page of a later step changes nothing.
        if not flow.get("fixed_host") and flow["step"] in ("server", "key") and not self._no_address():
            await self._drop_pending_login(flow_id, flow, cookie_key)
            await self._drop_granted(flow_id, flow, cookie_key)
            self.store.clear_flow_secret(flow_id)    # nothing found on one server is kept for another
            flow.update(step="server", error_detail="", ask_database=False, login=None, paste=False)
            self.store.update_flow(flow_id, flow)
        return RedirectResponse(f"/connect/{flow_id}", status_code=303)

    async def post_allow(self, request: Request) -> Response:
        checked = await self._checked_post(request)
        if isinstance(checked, Response):
            return checked
        flow_id, flow, cookie_key, _ = checked
        if flow["step"] != "allow":
            return RedirectResponse(f"/connect/{flow_id}", status_code=303)
        return self._finish(flow_id, flow, cookie_key, request)

    def _finish(self, flow_id: str, flow: dict[str, Any], cookie_key: str, request: Request | None = None) -> Response:
        try:
            secret = self.store.flow_secret(flow_id, cookie_key)
        except StoreError:
            secret = None
        if not secret:
            return HTMLResponse(pages.error_page(lang="en", app=self.app.NAME, message=pages.error_text("expired", "en", self.app.NAME),
                                                 source_url=self.s.source_url), status_code=400, headers=SECURITY_HEADERS)
        code = self.store.connect(app=self.app.NAME, server=flow["server_url"], remote_user=flow["remote_user"],
                                  client_id=flow["client_id"], resource=flow["resource"], display=flow["identity"],
                                  secret=secret, redirect_uri=flow["redirect_uri"],
                                  code_challenge=flow["code_challenge"], scope=flow.get("scope", ""))
        self.store.end_flow(flow_id)
        back = _with_params(flow["redirect_uri"], {"code": code, "state": flow.get("state", ""), "iss": self.s.issuer})
        lang = self._lang(request, flow) if request is not None else "en"
        who = flow["identity"].get("label") or flow["identity"].get("name") or ""
        body = pages.connected_page(lang=lang, app=self.app.NAME, asking=self._asking(flow), source_url=self.s.source_url,
                                    who=who, back_url=back)
        # Step 3 · Connected: shown for a moment, then back to the assistant with the code.
        response = HTMLResponse(body, headers={**SECURITY_HEADERS, "Content-Security-Policy": self._csp(_origin(flow["redirect_uri"]))})
        response.delete_cookie(self._cookie_name(flow_id), path="/", secure=True, httponly=True, samesite="lax")
        LOG.info("connection created app=%s", self.app.NAME)
        return response

    async def post_cancel(self, request: Request) -> Response:
        checked = await self._checked_post(request)
        if isinstance(checked, Response):
            return checked
        flow_id, flow, cookie_key, _ = checked
        await self._drop_pending_login(flow_id, flow, cookie_key)   # nothing stays behind in the app
        await self._drop_granted(flow_id, flow, cookie_key)
        self.store.end_flow(flow_id)
        response = self._client_redirect(flow["redirect_uri"], {"error": "access_denied", "state": flow.get("state", "")})
        response.delete_cookie(self._cookie_name(flow_id), path="/", secure=True, httponly=True, samesite="lax")
        return response

    # ------------------------------------------------------------------ the operator's look
    BRAND_TYPES = {".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2",
                   ".woff": "font/woff", ".txt": "text/plain"}

    async def brand_file(self, request: Request) -> Response:
        """Serve one file of the operator's brand folder (stylesheets, logo, fonts); nothing else."""
        root = self.s.brand_dir
        name = request.path_params.get("name", "")
        if not root or not name or ".." in name.split("/") or name.startswith("/"):
            return Response(status_code=404)
        path = (root / name).resolve()
        kind = self.BRAND_TYPES.get(path.suffix.lower())
        if not kind or not path.is_file() or root.resolve() not in path.parents:
            return Response(status_code=404)
        return Response(path.read_bytes(), media_type=kind,
                        headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"})

    # ------------------------------------------------------------------ token endpoint
    async def _client_auth(self, request: Request, form: Any) -> client_registry.ClientInfo | Response:
        client_id, secret = str(form.get("client_id", "")), str(form.get("client_secret", ""))
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                raw = base64.b64decode(header[6:].strip()).decode("utf-8")
                from urllib.parse import unquote
                cid, _, sec = raw.partition(":")
                client_id, secret = unquote(cid), unquote(sec)
            except (ValueError, UnicodeDecodeError):
                return self._token_error("invalid_client", 401)
        try:
            client = await client_registry.load_client(client_id, self.store, self.s)
        except client_registry.ClientError:
            return self._token_error("invalid_client", 401)
        if client.auth_method != "none":
            if not secret or not client.secret_hash or not hmac.compare_digest(client.secret_hash, digest(secret)):
                return self._token_error("invalid_client", 401)
        return client

    @staticmethod
    def _token_error(code: str, status: int = 400, description: str = "") -> Response:
        body = {"error": code}
        if description:
            body["error_description"] = description
        return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    def _token_response(self, access: str, refresh: str, ttl: int, scope: str) -> Response:
        return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": ttl,
                             "refresh_token": refresh, "scope": scope},
                            headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    async def token(self, request: Request) -> Response:
        if not self.limits.allow("token", client_ip(request), 120, 60):
            return self._token_error("slow_down", 429)
        form = await request.form()
        client = await self._client_auth(request, form)
        if isinstance(client, Response):
            return client
        grant_type = form.get("grant_type")
        if grant_type == "authorization_code":
            grant = self.store.take_code(str(form.get("code", "")))
            if grant is None:
                return self._token_error("invalid_grant")
            # The code is used up: on any refusal below, its connection can never be opened again. Drop it,
            # with the app's disconnect (the app password granted moments ago must not stay alive).
            if grant.client_id != client.client_id or str(form.get("redirect_uri", grant.redirect_uri)) != grant.redirect_uri:
                await self._drop_connection(grant)
                return self._token_error("invalid_grant")
            verifier = str(form.get("code_verifier", ""))
            if not (43 <= len(verifier) <= 128) or not hmac.compare_digest(_b64url_sha256(verifier), grant.code_challenge):
                await self._drop_connection(grant)
                return self._token_error("invalid_grant", description="PKCE verification failed")
            resource = form.get("resource")
            if resource and resource != grant.resource:
                await self._drop_connection(grant)
                return self._token_error("invalid_target")
            access, refresh, ttl = self.store.issue_tokens(grant)
            return self._token_response(access, refresh, ttl, grant.scope)
        if grant_type == "refresh_token":
            grant = self.store.rotate_refresh(str(form.get("refresh_token", "")), client.client_id)
            if grant is None:
                return self._token_error("invalid_grant")
            resource = form.get("resource")
            if resource and resource != grant.resource:
                return self._token_error("invalid_target")
            access, refresh, ttl = self.store.issue_tokens(grant)
            return self._token_response(access, refresh, ttl, grant.scope)
        return self._token_error("unsupported_grant_type")

    async def revoke(self, request: Request) -> Response:
        form = await request.form()
        client = await self._client_auth(request, form)
        if isinstance(client, Response):
            return client
        token = str(form.get("token", ""))
        connection = self.store.connection_for_token(token)
        self.store.revoke(token)
        if connection is not None:
            await self._disconnect(connection.server, connection.secret)
        return Response(status_code=200, headers={"Cache-Control": "no-store"})

    async def _drop_connection(self, grant: Any) -> None:
        try:
            connection = self.store.get_connection(grant)
        except StoreError:
            connection = None
        self.store.delete_connection(grant.conn_id)
        if connection is not None:
            await self._disconnect(connection.server, connection.secret)

    async def _disconnect(self, server_url: str, secret: dict[str, Any]) -> None:
        """The app's optional disconnect hook (e.g. delete the app password). Never fails the caller."""
        hook = getattr(self.app, "on_disconnect", None)
        if hook is None:
            return
        try:
            done = await hook(server_url, secret, denied_hosts=self._denied)
        except Exception as exc:              # a disconnect goes ahead whatever the app answers
            LOG.warning("disconnect hook failed app=%s error=%s", self.app.NAME, type(exc).__name__)
            return
        LOG.info("disconnect hook app=%s removed=%s", self.app.NAME, "yes" if done else "no")

    # ------------------------------------------------------------------ routes
    def routes(self) -> list[Route]:
        return [
            Route("/.well-known/oauth-authorization-server", self.as_metadata_route, methods=["GET"]),
            Route("/.well-known/oauth-protected-resource", self.prm_route, methods=["GET"]),
            Route("/.well-known/oauth-protected-resource/{rest:path}", self.prm_route, methods=["GET"]),
            Route("/register", self.register, methods=["POST"]),
            Route("/authorize", self.authorize, methods=["GET"]),
            Route("/token", self.token, methods=["POST"]),
            Route("/revoke", self.revoke, methods=["POST"]),
            Route("/brand/{name:path}", self.brand_file, methods=["GET"]),
            Route("/connect/{flow_id}", self.page, methods=["GET"]),
            Route("/connect/{flow_id}/server", self.post_server, methods=["POST"]),
            Route("/connect/{flow_id}/key", self.post_key, methods=["POST"]),
            Route("/connect/{flow_id}/allow", self.post_allow, methods=["POST"]),
            Route("/connect/{flow_id}/change", self.post_change, methods=["POST"]),
            Route("/connect/{flow_id}/cancel", self.post_cancel, methods=["POST"]),
        ]


class ResourceGate:
    """ASGI middleware in front of the MCP app: bearer token → connection, or 401.

    It accepts ``/mcp`` and ``/<server-host>/mcp``, checks the token was issued for
    exactly that address, opens the connection's secret with the token, puts the
    connection in :data:`CURRENT` for the tools, and hands the request on as ``/mcp``.
    """

    def __init__(self, inner: Callable[..., Awaitable[None]], auth: AuthServer, other: Callable[..., Awaitable[None]]):
        self.inner = inner
        self.auth = auth
        self.other = other

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.other(scope, receive, send)
            return
        path = scope.get("path", "")
        resource = self.auth.resource_for_path(path) if path.endswith("/mcp") else None
        if not resource:
            await self.other(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        origin = headers.get("origin")
        if origin and not origin.startswith("https://"):
            await JSONResponse({"error": "forbidden_origin"}, status_code=403)(scope, receive, send)
            return
        prm = f"{self.auth.s.public_url}/.well-known/oauth-protected-resource{path}"
        authz = headers.get("authorization", "")
        token = authz[7:].strip() if authz.lower().startswith("bearer ") else ""
        grant = self.auth.store.check_access(token) if token else None
        connection = None
        if grant is not None and grant.resource == resource:
            try:
                connection = self.auth.store.get_connection(grant)
            except StoreError:
                connection = None
        if connection is not None and connection.server:
            # A server taken off the operator's list stops working at once.
            host = (urlsplit(connection.server).hostname or "").lower()
            if not self.auth.s.server_allowed(host):
                connection = None
        allowed = getattr(self.auth.app, "connection_allowed", None)
        if connection is not None and allowed is not None and not allowed(connection):
            connection = None                    # e.g. the account was taken off the operator's list
        if connection is None:
            challenge = f'Bearer resource_metadata="{prm}"'
            if token:
                challenge += ', error="invalid_token"'
            await JSONResponse({"error": "invalid_token" if token else "unauthorized"}, status_code=401,
                               headers={"WWW-Authenticate": challenge})(scope, receive, send)
            return
        reset = CURRENT.set(connection)
        try:
            inner_scope = dict(scope)
            inner_scope["path"] = "/mcp"
            inner_scope["raw_path"] = b"/mcp"
            await self.inner(inner_scope, receive, send)
        finally:
            CURRENT.reset(reset)
