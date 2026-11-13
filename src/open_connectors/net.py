"""The one guarded road from the connector to a person's own server.

Every request the connector makes to an address a person typed goes through
:func:`client_for`. It:

* accepts only ``https`` on port 443, a public host name (no IP literal, no
  user name or password in the address, no query, no fragment);
* resolves the name once per request, refuses the request if ANY resolved
  address is not public unicast (private, loopback, link-local, carrier-grade
  NAT, multicast, reserved, cloud metadata, IPv6 forms embedding those);
* connects to the checked IP itself, with the ``Host`` header and the TLS name
  (SNI and certificate check) set to the typed host, so a DNS answer cannot
  change between the check and the connection;
* never follows a redirect, never reads proxy settings from the environment;
* caps time and response size.

Error messages are generic on purpose: they never name a resolved IP address.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import idna
from fastmcp.server.auth.ssrf import is_ip_allowed

DEFAULT_TIMEOUT = 10.0
PROBE_TIMEOUT = 5.0
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
PROBE_MAX_BYTES = 64 * 1024


class AddressError(ValueError):
    """The typed address cannot be used. ``code`` selects the person-facing words."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True)
class Origin:
    """A checked server origin: ``https://<host>`` plus an optional base path."""

    host: str          # ASCII (IDNA/UTS-46) host name, lower case, no trailing dot
    base_path: str     # "" or "/sub/path" without trailing slash

    @property
    def url(self) -> str:
        return f"https://{self.host}{self.base_path}"

    def join(self, path: str) -> str:
        return self.url + (path if path.startswith("/") else "/" + path)


def normalize_address(raw: str, strip_suffixes: tuple[str, ...] = (), root_only: bool = False) -> Origin:
    """Turn what a person typed into a checked :class:`Origin` (no network).

    ``erp.example.com`` becomes ``https://erp.example.com``. A trailing path that
    the person copied from the app's own pages (``strip_suffixes``, e.g. ``/odoo``)
    is removed. ``root_only`` (an app used at the host root): path, query and
    fragment of a pasted page address are dropped, only the host is kept.
    """
    text = (raw or "").strip()
    if not text:
        raise AddressError("empty")
    if any(ch.isspace() for ch in text):
        raise AddressError("invalid")
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise AddressError("invalid", str(exc)) from None
    if parts.scheme.lower() != "https":
        raise AddressError("not_https")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise AddressError("userinfo")
    if not root_only and (parts.query or parts.fragment or text.endswith("?") or text.endswith("#")):
        raise AddressError("query")
    try:
        port = parts.port
    except ValueError:
        raise AddressError("invalid") from None
    if port not in (None, 443):
        raise AddressError("port")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise AddressError("invalid")
    if _is_ip_literal(host):
        raise AddressError("ip_literal")
    try:
        ascii_host = idna.encode(host, uts46=True).decode("ascii").lower()
    except idna.IDNAError:
        raise AddressError("invalid") from None
    if "." not in ascii_host or ascii_host.endswith(".local") or ascii_host == "localhost" \
            or ascii_host.endswith(".localhost") or ascii_host.endswith(".internal"):
        raise AddressError("not_public")
    if root_only:
        return Origin(host=ascii_host, base_path="")
    path = parts.path or ""
    while path.endswith("/"):
        path = path[:-1]
    for suffix in strip_suffixes:
        if suffix and (path == suffix or path.endswith(suffix)):
            path = path[: -len(suffix)]
            break
    while path.endswith("/"):
        path = path[:-1]
    if ".." in path.split("/") or "//" in path or "\\" in path or "%" in path:
        raise AddressError("invalid")
    return Origin(host=ascii_host, base_path=path)


def _is_ip_literal(host: str) -> bool:
    candidate = host.strip("[]")
    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        pass
    # Integer, octal and hex forms that resolvers turn into IPv4 addresses.
    try:
        socket.inet_aton(candidate)
        return True
    except OSError:
        return False


async def resolve_public(host: str) -> list[str]:
    """Resolve ``host`` and return its addresses, or refuse if any is not public."""
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: socket.getaddrinfo(host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)),
            timeout=PROBE_TIMEOUT,
        )
    except (socket.gaierror, asyncio.TimeoutError, UnicodeError):
        raise AddressError("not_found") from None
    ips = list(dict.fromkeys(info[4][0] for info in infos))
    if not ips:
        raise AddressError("not_found")
    if not all(is_ip_allowed(ip) for ip in ips):
        raise AddressError("not_public")
    return ips


class PinnedTransport(httpx.AsyncBaseTransport):
    """Send each request to a freshly checked public IP of its own host.

    The request keeps its real host name for ``Host`` and for TLS; only the TCP
    destination is the checked IP. A request to any other scheme, port or an IP
    literal is refused before anything leaves the machine.
    """

    def __init__(self, *, denied_hosts: frozenset[str] = frozenset(), verify: bool | str = True,
                 allowed_hosts: frozenset[str] = frozenset()):
        self._inner = httpx.AsyncHTTPTransport(verify=verify, retries=0, http2=False)
        self._denied = frozenset(h.lower() for h in denied_hosts)
        # An app that talks to fixed hosts only (kSuite): any other host is refused before any DNS lookup.
        self._allowed = frozenset(h.lower() for h in allowed_hosts)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        host = (url.host or "").lower().rstrip(".")
        if url.scheme != "https" or (url.port not in (None, 443)) or not host or _is_ip_literal(host):
            raise AddressError("not_https")
        if self._allowed and host not in self._allowed:
            raise AddressError("not_public")
        if host in self._denied or any(host.endswith("." + d) for d in self._denied):
            raise AddressError("not_public")
        ips = await resolve_public(host)
        last_error: Exception | None = None
        for ip in ips:
            pinned = request.url.copy_with(host=ip, port=None)
            headers = httpx.Headers(request.headers)
            headers["Host"] = host
            extensions = dict(request.extensions)
            extensions["sni_hostname"] = host
            pinned_request = httpx.Request(
                request.method, pinned, headers=headers, stream=request.stream, extensions=extensions,
            )
            try:
                return await self._inner.handle_async_request(pinned_request)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc
                continue
        raise AddressError("unreachable", type(last_error).__name__ if last_error else "")

    async def aclose(self) -> None:
        await self._inner.aclose()


def client_for(
    *,
    timeout: float = DEFAULT_TIMEOUT,
    denied_hosts: frozenset[str] = frozenset(),
    headers: dict[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    allowed_hosts: frozenset[str] = frozenset(),
) -> httpx.AsyncClient:
    """An HTTP client that can only reach checked public https hosts.

    ``allowed_hosts``, when given, is the only set of hosts it may reach.
    ``transport`` is for tests only (an in-process fake server); production
    callers never pass it.
    """
    return httpx.AsyncClient(
        transport=transport or PinnedTransport(denied_hosts=denied_hosts, allowed_hosts=allowed_hosts),
        timeout=httpx.Timeout(timeout, connect=PROBE_TIMEOUT),
        follow_redirects=False,
        trust_env=False,
        headers=headers or {},
    )


async def read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    """Read a streamed response body, refusing anything larger than ``max_bytes``."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            raise AddressError("too_large")
        chunks.append(chunk)
    return b"".join(chunks)
