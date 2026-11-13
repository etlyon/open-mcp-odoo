"""Settings. Everything about a particular operator lives here, never in code.

One process serves one app on its own public host, e.g. ``https://odoo.<domain>``:
the connector address is ``<public_url>/mcp`` (the person types their server on
the sign-in page) or ``<public_url>/<their-server-host>/mcp`` (server already
named, so one assistant can hold two servers of the same app). An app that
works by account, with no server to pick (kSuite), has ``<public_url>/mcp`` only.
The sign-in server's issuer is ``public_url`` itself.

The file is TOML (see ``config.example.toml``). Nothing in it is secret, and
the connector has no master key at all (see ``store.py``).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

APPS = ("odoo",)


@dataclass(frozen=True)
class Assistant:
    """An assistant the operator names plainly on the "Allow?" screen."""

    name: str
    redirect_uris: tuple[str, ...] = ()       # exact callback URIs it uses with DCR
    cimd_hosts: tuple[str, ...] = ()          # hosts of its client metadata document URLs
    loopback: bool = False                    # native app on this computer (any port)


@dataclass(frozen=True)
class Settings:
    public_url: str
    state_dir: Path
    source_url: str
    app: str = "odoo"
    other_assistants: bool = True
    maker_label: str = "open source"          # shown under the name, e.g. "by <maker> · open source"
    brand_dir: Path | None = None             # operator's look: stylesheets, logo, fonts (served at /brand/)
    brand_styles: tuple[str, ...] = ()
    brand_logo: str = ""
    brand_name: str = ""
    assistants: tuple[Assistant, ...] = ()
    denied_hosts: frozenset[str] = field(default_factory=frozenset)
    allowed_servers: frozenset[str] = field(default_factory=frozenset)   # empty = any public server
    allowed_mail_domains: frozenset[str] = field(default_factory=frozenset)   # kSuite: empty = any mailbox
    send_recipient_domains: frozenset[str] = field(default_factory=frozenset)  # kSuite: empty = no mail leaves

    def server_allowed(self, host: str) -> bool:
        return not self.allowed_servers or host.lower() in self.allowed_servers
    max_open_flows: int = 500
    languages: tuple[str, ...] = ("en", "fr", "de")

    @property
    def issuer(self) -> str:
        return self.public_url

    @property
    def public_host(self) -> str:
        return urlsplit(self.public_url).hostname or ""

    def resource_url(self, server_host: str | None = None) -> str:
        if server_host:
            return f"{self.public_url}/{server_host}/mcp"
        return f"{self.public_url}/mcp"


DEFAULT_ASSISTANTS = (
    Assistant(name="Claude", redirect_uris=("https://claude.ai/api/mcp/auth_callback",), cimd_hosts=("claude.ai",)),
    Assistant(name="Claude Code", cimd_hosts=("claude.ai",), loopback=True),
    Assistant(name="ChatGPT", redirect_uris=("https://chatgpt.com/connector_platform_oauth_redirect",),
              cimd_hosts=("chatgpt.com", "openai.com")),
)


def _check_public_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment or parts.username:
        raise ValueError("public_url must be a plain https URL")
    if parts.path not in ("", "/"):
        raise ValueError("public_url must have no path")
    return f"https://{parts.netloc}"


def _server_host(value: str) -> str:
    """A listed server as its ASCII host name (same rules as an address a person types)."""
    from open_connectors.net import normalize_address
    return normalize_address(value).host


MAX_RECIPIENT_DOMAINS = 20


def _mail_domain(value: str, setting: str = "allowed_mail_domains") -> str:
    """A listed mail domain as its lower-case ASCII (IDNA) name: at least two labels, no '@', '/' or '\\'."""
    import idna
    text = value.strip().rstrip(".").lower()
    if not text or len(text) > 253 or any(ch in text for ch in "@/\\:*") or any(ch.isspace() for ch in text):
        raise ValueError(f"{setting}: not a mail domain: {value!r}")
    try:
        ascii_name = idna.encode(text, uts46=True).decode("ascii").lower()
    except idna.IDNAError:
        raise ValueError(f"{setting}: not a mail domain: {value!r}") from None
    if len(ascii_name.split(".")) < 2:
        raise ValueError(f"{setting}: not a mail domain: {value!r}")
    return ascii_name


def _recipient_domains(values: object) -> frozenset[str]:
    """kSuite's send_recipient_domains: at most 20 exact mail domains (no wildcard, no subdomain match)."""
    if not isinstance(values, list):
        raise ValueError("send_recipient_domains must be a list of mail domains")
    domains = frozenset(_mail_domain(str(d), "send_recipient_domains") for d in values)
    if len(domains) > MAX_RECIPIENT_DOMAINS:
        raise ValueError(f"send_recipient_domains: at most {MAX_RECIPIENT_DOMAINS} domains")
    return domains


def load(path: str | os.PathLike[str] | None = None) -> Settings:
    path = path or os.environ.get("OPEN_CONNECTORS_CONFIG", "/etc/open-mcp-odoo/config.toml")
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    assistants: list[Assistant] = []
    if raw.get("default_assistants", True):
        assistants.extend(DEFAULT_ASSISTANTS)
    for item in raw.get("assistant", []):
        assistants.append(Assistant(
            name=str(item["name"]),
            redirect_uris=tuple(str(u) for u in item.get("redirect_uris", [])),
            cimd_hosts=tuple(str(h).lower() for h in item.get("cimd_hosts", [])),
            loopback=bool(item.get("loopback", False)),
        ))
    for a in assistants:
        for uri in a.redirect_uris:
            parts = urlsplit(uri)
            if parts.scheme != "https" or not parts.hostname or parts.fragment:
                raise ValueError(f"assistant {a.name}: redirect URIs must be exact https URLs")
    app = str(raw.get("app", "odoo"))
    if app != "odoo":
        raise ValueError(f"this repository is open-mcp-odoo; app must be 'odoo', got {app!r}")
    if app == "ksuite" and raw.get("allowed_servers"):
        raise ValueError("allowed_servers does not apply to kSuite (there is no server to pick): use allowed_mail_domains")
    if app != "ksuite" and raw.get("allowed_mail_domains"):
        raise ValueError("allowed_mail_domains applies to kSuite only")
    if app != "ksuite" and raw.get("send_recipient_domains"):
        raise ValueError("send_recipient_domains applies to kSuite only")
    source_url = str(raw.get("source_url", "")).strip()
    if not source_url.startswith("https://"):
        raise ValueError("source_url (the public source code, AGPL-3.0 section 13) must be an https URL")
    return Settings(
        public_url=_check_public_url(str(raw["public_url"])),
        state_dir=Path(raw.get("state_dir", "/var/lib/open-mcp-odoo")),
        source_url=source_url,
        app=app,
        other_assistants=bool(raw.get("other_assistants", True)),
        maker_label=str(raw.get("maker_label", "open source"))[:80],
        brand_dir=Path(raw["brand"]["dir"]) if raw.get("brand", {}).get("dir") else None,
        brand_styles=tuple(str(x) for x in raw.get("brand", {}).get("styles", [])),
        brand_logo=str(raw.get("brand", {}).get("logo", "")),
        brand_name=str(raw.get("brand", {}).get("name", ""))[:60],
        assistants=tuple(assistants),
        denied_hosts=frozenset(str(h).lower() for h in raw.get("denied_hosts", [])),
        allowed_servers=frozenset(_server_host(str(h)) for h in raw.get("allowed_servers", [])),
        allowed_mail_domains=frozenset(_mail_domain(str(d)) for d in raw.get("allowed_mail_domains", [])),
        send_recipient_domains=_recipient_domains(raw.get("send_recipient_domains", [])),
        max_open_flows=int(raw.get("max_open_flows", 500)),
    )
