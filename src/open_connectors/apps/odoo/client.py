"""Minimal asynchronous client for Odoo 19's External JSON-2 API.

``OdooClient`` speaks Odoo's JSON-2 API. ``ConnectionOdooClient`` is what the
tools use: the same client, built per call from the calling connection's own
server address, database and API key, and always sent through the guarded
road in :mod:`open_connectors.net` (checked public IP, no proxy, no redirect).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from open_connectors import identity, net
from open_connectors.apps.odoo.credential import OdooCredential


class OdooAPIError(RuntimeError):
    """Safe, classified Odoo failure without server tracebacks or response bodies."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "odoo_error",
        status_code: int | None = None,
        retryable: bool = False,
        source_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.retryable = retryable
        self.source_type = source_type


# A retry is permitted only for these code-reviewed, side-effect-free calls.
# Keeping the set here makes a mistaken ``safe_read=True`` on a write fail
# before any request is sent.
SAFE_RETRY_METHODS = frozenset(
    {
        "check_access_rights",
        "context_get",
        "fields_get",
        "formatted_read_group",
        "get_self_status",
        "has_access",
        "read",
        "search_count",
        "search_read",
    }
)
READ_ATTEMPTS = 2

_FIXED_ERROR_MESSAGES = {
    400: "Odoo rejected the request",
    401: "Authentication rejected",
    403: "Access denied",
    404: "Requested Odoo method was not found",
    405: "Requested Odoo method is not available",
    409: "Odoo reported a request conflict",
    422: "Odoo rejected the request",
    429: "Odoo rate limit reached",
    502: "Odoo is temporarily unavailable",
    503: "Odoo is temporarily unavailable",
    504: "Odoo is temporarily unavailable",
}


def _status_classification(status_code: int) -> tuple[str, bool]:
    if status_code in {400, 422}:
        return "invalid_request", False
    if status_code == 401:
        return "credential_rejected", False
    if status_code == 403:
        return "acl_denied", False
    if status_code == 404:
        return "compatibility_missing", False
    if status_code == 405:
        return "compatibility_missing", False
    if status_code == 409:
        return "request_conflict", False
    if status_code == 429:
        return "rate_limited", True
    if status_code in {502, 503, 504}:
        return "unreachable", True
    if 400 <= status_code < 500:
        return "request_refused", False
    return "odoo_error", False


def _safe_error_message(status_code: int) -> str:
    if status_code >= 500:
        return "Odoo is temporarily unavailable"
    if status_code in _FIXED_ERROR_MESSAGES:
        return _FIXED_ERROR_MESSAGES[status_code]
    if 400 <= status_code < 500:
        return "Odoo refused the request"
    return "Odoo request failed"


def _request_error(exc: httpx.TransportError | OdooAPIError) -> OdooAPIError:
    if isinstance(exc, OdooAPIError):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        message = "Odoo request timed out."
    else:
        message = "Could not reach the Odoo API."
    return OdooAPIError(
        message,
        category="unreachable",
        retryable=True,
        source_type=type(exc).__name__,
    )


class OdooClient:
    def __init__(
        self,
        *,
        base_url: str,
        database: str,
        api_key: str,
        allowed_models: set[str],
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.database = database
        self.api_key = api_key
        self.allowed_models = allowed_models
        self.timeout = timeout
        self.transport = transport

    def require_model(self, model: str) -> None:
        if not model or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_." for char in model):
            raise OdooAPIError("Invalid Odoo model name.")
        if "*" not in self.allowed_models and model not in self.allowed_models:
            allowed = ", ".join(sorted(self.allowed_models))
            raise OdooAPIError(f"Model '{model}' is not allowed. Allowed models: {allowed}")

    async def version(self) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            return await self._request_json(
                client, "GET", f"{self.base_url}/web/version", attempts=READ_ATTEMPTS
            )

    async def call(
        self,
        model: str,
        method: str,
        payload: dict[str, Any],
        *,
        enforce_allowlist: bool = True,
        safe_read: bool = False,
    ) -> Any:
        if enforce_allowlist:
            self.require_model(model)
        elif not model or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_." for char in model
        ):
            raise OdooAPIError("Invalid Odoo model name.")
        if not method.replace("_", "").isalnum():
            raise OdooAPIError("Invalid Odoo method name.")
        if safe_read and method not in SAFE_RETRY_METHODS:
            raise OdooAPIError("This Odoo operation is not approved for automatic read retry.")

        model_path = quote(model, safe=".")
        method_path = quote(method, safe="_")
        headers = {
            "Authorization": f"bearer {self.api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "open-mcp-connectors (Odoo)",
        }
        if self.database:
            headers["X-Odoo-Database"] = self.database
        attempts = READ_ATTEMPTS if method in SAFE_RETRY_METHODS else 1
        async with httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            return await self._request_json(
                client,
                "POST",
                f"{self.base_url}/json/2/{model_path}/{method_path}",
                attempts=attempts,
                headers=headers,
                json=payload,
            )

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        attempts: int,
        **kwargs: Any,
    ) -> Any:
        for attempt in range(attempts):
            try:
                return self._decode(await client.request(method, url, **kwargs))
            except net.AddressError as exc:
                raise OdooAPIError(
                    "Could not reach this Odoo safely.", category="unreachable", source_type=exc.code
                ) from None
            except (httpx.TransportError, OdooAPIError) as exc:
                error = _request_error(exc)
            if attempt + 1 >= attempts or not error.retryable:
                raise error from None
        raise AssertionError("unreachable")

    @staticmethod
    def _decode(response: httpx.Response) -> Any:
        category, retryable = _status_classification(response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise OdooAPIError(
                f"Odoo returned non-JSON HTTP {response.status_code}.",
                category=category if response.is_error else "invalid_response",
                status_code=response.status_code,
                retryable=retryable,
            ) from exc

        if response.is_error:
            raise OdooAPIError(
                f"Odoo API HTTP {response.status_code}: {_safe_error_message(response.status_code)}",
                category=category,
                status_code=response.status_code,
                retryable=retryable,
            )
        return payload


REFUSED_STORED_KEY = (
    "Odoo refused this connection's API key; it has probably expired or been deleted in Odoo. "
    "Connect Odoo again in your assistant to give a new key."
)

DENIED_HOSTS: frozenset[str] = frozenset()   # set once at start from the settings


class ConnectionOdooClient(OdooClient):
    """OdooClient for the calling connection: its own server, database and API key, on every call."""

    def __init__(self, *, allowed_models: set[str], app: str = "odoo", timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(base_url="", database="", api_key="", allowed_models=allowed_models,
                         timeout=timeout, transport=transport)
        self.app = app

    def credential_for_current_account(self) -> OdooCredential:
        try:
            connection = identity.current_connection()
        except identity.IdentityRefused as exc:
            raise OdooAPIError(str(exc), category="not_connected") from None
        secret = connection.secret
        key = secret.get("api_key")
        if not isinstance(key, str) or not key:
            raise OdooAPIError("This connection holds no Odoo key; connect Odoo again.", category="not_connected")
        expires_at = secret.get("expires_at")
        return OdooCredential(api_key=key, base_url=connection.server, database=str(secret.get("database") or ""),
                              expires_at=expires_at if isinstance(expires_at, int) else None,
                              expiry_source=str(secret.get("expiry_source") or "unknown"))

    def for_current_account(self) -> OdooClient:
        credential = self.credential_for_current_account()
        return OdooClient(
            base_url=credential.base_url,
            database=credential.database,
            api_key=credential.api_key,
            allowed_models=self.allowed_models,
            timeout=self.timeout,
            transport=self.transport or net.PinnedTransport(denied_hosts=DENIED_HOSTS),
        )

    async def version(self) -> dict[str, Any]:
        return await self.for_current_account().version()

    async def call(
        self,
        model: str,
        method: str,
        payload: dict[str, Any],
        *,
        enforce_allowlist: bool = True,
        safe_read: bool = False,
    ) -> Any:
        account_client = self.for_current_account()
        try:
            return await account_client.call(
                model,
                method,
                payload,
                enforce_allowlist=enforce_allowlist,
                safe_read=safe_read,
            )
        except OdooAPIError as exc:
            if exc.status_code == 401 or str(exc).startswith("Odoo API HTTP 401: "):
                raise OdooAPIError(
                    f"{exc} ({REFUSED_STORED_KEY})",
                    category=exc.category,
                    status_code=exc.status_code,
                    retryable=exc.retryable,
                    source_type=exc.source_type,
                ) from None
            raise
