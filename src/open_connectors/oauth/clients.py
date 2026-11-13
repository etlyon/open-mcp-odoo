"""Who is asking: the assistant (OAuth client) behind a sign-in.

Two ways an assistant identifies itself:

* **Client ID Metadata Document (CIMD)**: its ``client_id`` is an https URL of
  a JSON document it publishes. Its host is verifiable, so the "Allow?" screen
  can name it plainly when the operator lists that host.
* **Dynamic Client Registration (DCR, RFC 7591)**: it registered itself here.
  Its ``client_name`` is self-chosen, so it is shown as *not verified* unless
  the exact return address is one the operator lists for a known assistant.

Return addresses (redirect URIs) always match exactly. The only exception is
a native app's loopback address (``http://localhost`` / ``127.0.0.1`` /
``[::1]``), where the port is ignored, as RFC 8252 requires.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from open_connectors import net
from open_connectors.config import Assistant, Settings
from open_connectors.store import Store

CIMD_TTL = 60 * 60
CIMD_MAX_BYTES = 16 * 1024
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")


class ClientError(Exception):
    def __init__(self, code: str, description: str):
        super().__init__(description)
        self.code = code
        self.description = description


@dataclass(frozen=True)
class ClientInfo:
    client_id: str
    kind: str                       # 'cimd' | 'dcr'
    redirect_uris: tuple[str, ...]
    client_name: str
    application_type: str           # 'web' | 'native'
    auth_method: str                # 'none' | 'client_secret_post' | 'client_secret_basic'
    secret_hash: str | None = None


def is_loopback(uri: str) -> bool:
    parts = urlsplit(uri)
    return parts.scheme == "http" and (parts.hostname or "") in LOOPBACK_HOSTS


def _same_loopback(a: str, b: str) -> bool:
    pa, pb = urlsplit(a), urlsplit(b)
    return (is_loopback(a) and is_loopback(b) and pa.hostname == pb.hostname
            and pa.path == pb.path and pa.query == pb.query)


def redirect_allowed(client: ClientInfo, redirect_uri: str) -> bool:
    if not redirect_uri or "#" in redirect_uri:
        return False
    for registered in client.redirect_uris:
        if redirect_uri == registered:
            return True
        if client.application_type == "native" and _same_loopback(redirect_uri, registered):
            return True
    return False


def validate_redirect_uri(uri: str, application_type: str) -> str:
    parts = urlsplit(uri)
    if not parts.scheme or "*" in uri or parts.fragment or parts.username or parts.password:
        raise ClientError("invalid_redirect_uri", "redirect URIs must be exact, without wildcard, fragment or user info")
    if parts.scheme == "https" and parts.hostname:
        return uri
    if is_loopback(uri) and application_type == "native":
        return uri
    if parts.scheme not in ("http", "https", "javascript", "data", "file") and application_type == "native" and "." in parts.scheme:
        return uri   # private-use URI scheme of a native app (RFC 8252 §7.1), e.g. com.example.app:/cb
    raise ClientError("invalid_redirect_uri", "redirect URIs must use https (or loopback http for native apps)")


def known_assistant(settings: Settings, client: ClientInfo, redirect_uri: str) -> Assistant | None:
    """The operator-listed assistant this sign-in verifiably comes from, if any."""
    if client.kind == "cimd":
        host = (urlsplit(client.client_id).hostname or "").lower()
        for assistant in settings.assistants:
            if host and any(host == h or host.endswith("." + h) for h in assistant.cimd_hosts):
                if is_loopback(redirect_uri) and not assistant.loopback:
                    continue
                return assistant
        return None
    for assistant in settings.assistants:
        if redirect_uri in assistant.redirect_uris:
            return assistant
    return None


def _auth_methods(doc: dict[str, Any]) -> list[str]:
    methods: list[str] = []
    single = doc.get("token_endpoint_auth_method")
    if isinstance(single, str):
        methods.append(single)
    plural = doc.get("token_endpoint_auth_methods_supported")
    if isinstance(plural, list):
        methods.extend(m for m in plural if isinstance(m, str))
    return methods or ["none"]


def is_cimd_client_id(client_id: str) -> bool:
    parts = urlsplit(client_id)
    return parts.scheme == "https" and bool(parts.hostname) and parts.path not in ("", "/")


async def _fetch_cimd(client_id: str, settings: Settings) -> dict[str, Any]:
    try:
        async with net.client_for(timeout=net.PROBE_TIMEOUT, denied_hosts=settings.denied_hosts,
                                  headers={"Accept": "application/json"}) as client:
            async with client.stream("GET", client_id) as response:
                body = await net.read_capped(response, CIMD_MAX_BYTES)
                if response.status_code != 200:
                    raise ClientError("invalid_client", "the client metadata document could not be fetched")
    except (net.AddressError, httpx.TransportError):
        raise ClientError("invalid_client", "the client metadata document could not be fetched") from None
    try:
        doc = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ClientError("invalid_client", "the client metadata document is not JSON") from None
    if not isinstance(doc, dict) or doc.get("client_id") != client_id:
        raise ClientError("invalid_client", "the client metadata document does not name itself")
    return doc


async def load_client(client_id: str, store: Store, settings: Settings) -> ClientInfo:
    if not client_id or len(client_id) > 2048:
        raise ClientError("invalid_client", "unknown client")
    if is_cimd_client_id(client_id):
        cached = store.get_client(client_id)
        doc: dict[str, Any] | None = None
        if cached and cached[0] == "cimd" and time.time() - float(cached[1].get("_fetched", 0)) < CIMD_TTL:
            doc = cached[1]
        if doc is None:
            doc = await _fetch_cimd(client_id, settings)
            doc = {**doc, "_fetched": time.time()}
            store.save_client(client_id, "cimd", doc)
        methods = _auth_methods(doc)
        if "none" not in methods:
            raise ClientError("invalid_client", "this connector accepts metadata-document clients as public clients only")
        uris = doc.get("redirect_uris")
        if not isinstance(uris, list) or not all(isinstance(u, str) for u in uris) or not uris:
            raise ClientError("invalid_client", "the client metadata document has no redirect URIs")
        app_type = "native" if any(is_loopback(u) for u in uris) else str(doc.get("application_type") or "web")
        return ClientInfo(client_id=client_id, kind="cimd", redirect_uris=tuple(uris),
                          client_name=str(doc.get("client_name") or urlsplit(client_id).hostname),
                          application_type=app_type, auth_method="none")
    cached = store.get_client(client_id)
    if not cached or cached[0] != "dcr":
        raise ClientError("invalid_client", "unknown client")
    meta = cached[1]
    return ClientInfo(client_id=client_id, kind="dcr", redirect_uris=tuple(meta.get("redirect_uris", [])),
                      client_name=str(meta.get("client_name") or "Unnamed assistant"),
                      application_type=str(meta.get("application_type") or "web"),
                      auth_method=str(meta.get("token_endpoint_auth_method") or "none"),
                      secret_hash=cached[2])
