"""Structured shared-door Odoo credentials, with legacy plain-key compatibility."""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import json
import time
from typing import Any

VERSION = 1
ESTIMATED_LIFETIME_SECONDS = 90 * 24 * 60 * 60
WARNING_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class OdooCredential:
    api_key: str
    base_url: str
    database: str
    expires_at: int | None = None
    expiry_source: str = "unknown"

    def to_store(self) -> str:
        return json.dumps(
            {
                "version": VERSION,
                "api_key": self.api_key,
                "base_url": self.base_url.rstrip("/"),
                "database": self.database,
                "expires_at": self.expires_at,
                "expiry_source": self.expiry_source,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    def status(self, now: float | None = None) -> dict[str, Any]:
        current = time.time() if now is None else now
        if self.expires_at is None:
            return {
                "credential_expires_at": None,
                "credential_expiry_source": self.expiry_source,
                "credential_ends_soon": False,
                "credential_expired": False,
            }
        remaining = self.expires_at - current
        return {
            "credential_expires_at": dt.datetime.fromtimestamp(self.expires_at, dt.timezone.utc).isoformat(),
            "credential_expiry_source": self.expiry_source,
            "credential_ends_soon": 0 < remaining <= WARNING_SECONDS,
            "credential_expired": remaining <= 0,
            "credential_days_remaining": max(0, int(remaining // 86400)),
        }


def decode(value: str, *, base_url: str, database: str) -> OdooCredential:
    """Read a v1 row; old encrypted rows remain valid as plain API keys."""
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        payload = None
    if not isinstance(payload, dict) or payload.get("version") != VERSION:
        return OdooCredential(value, base_url.rstrip("/"), database)
    key = payload.get("api_key")
    row_url = payload.get("base_url")
    row_database = payload.get("database")
    expires_at = payload.get("expires_at")
    source = payload.get("expiry_source")
    if (
        not isinstance(key, str) or not key
        or not isinstance(row_url, str) or not row_url
        or not isinstance(row_database, str) or not row_database
        or (expires_at is not None and type(expires_at) is not int)
        or not isinstance(source, str)
    ):
        raise ValueError("The stored Odoo credential metadata is invalid.")
    return OdooCredential(key, row_url.rstrip("/"), row_database, expires_at, source)


def parse_odoo_datetime(value: object) -> int | None:
    if value in (None, False, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("invalid Odoo expiration date")
    text = value.strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp())


async def expiry_for_key(client: Any, uid: int, *, now: float | None = None) -> tuple[int | None, str]:
    """Read the one eBot key record when Odoo exposes it; otherwise use D249's 90-day bound.

    A returned false expiration is authoritative and intentionally creates no reminder.
    Missing, ambiguous or ACL-refused metadata is never guessed as a particular Odoo
    record; the conservative fallback starts at the successful connection time.
    """
    current = time.time() if now is None else now
    try:
        rows = await client.call(
            "res.users.apikeys",
            "search_read",
            {
                "domain": [["user_id", "=", uid], ["name", "=", "eBot"]],
                "fields": ["name", "user_id", "expiration_date", "create_date"],
                "limit": 2,
                "order": "create_date desc",
            },
            enforce_allowlist=False,
            safe_read=True,
        )
        if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
            return parse_odoo_datetime(rows[0].get("expiration_date")), "odoo"
    except Exception:
        pass
    return int(current + ESTIMATED_LIFETIME_SECONDS), "estimated-90-days"
