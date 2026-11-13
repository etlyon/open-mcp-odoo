"""Odoo's part of the sign-in: what the pages call for steps B and C."""

from __future__ import annotations

from typing import Any

from open_connectors import net
from open_connectors.apps.odoo import probe

NAME = "odoo"
CheckError = probe.OdooCheckError
STRIP_SUFFIXES = probe.STRIP_SUFFIXES


async def check_address(typed: str, *, denied_hosts: frozenset[str] = frozenset()) -> probe.OdooServer:
    return await probe.check_address(typed, denied_hosts=denied_hosts)


async def identify(server_url: str, key: str, database: str, *,
                   denied_hosts: frozenset[str] = frozenset()) -> probe.OdooIdentity:
    origin = net.normalize_address(server_url)
    server = probe.OdooServer(origin=origin, version="", online_hint=False)
    return await probe.identify(server, key, database, denied_hosts=denied_hosts)


def open_url(server_url: str) -> str:
    """Where "Open your Odoo" goes: the person's own Odoo, nothing else."""
    return server_url.rstrip("/") + "/odoo"


def secret(key: str, identity: probe.OdooIdentity) -> dict[str, Any]:
    return {"api_key": key.strip(), "database": identity.database,
            "expires_at": identity.expires_at, "expiry_source": identity.expiry_source}


def display(identity: probe.OdooIdentity) -> dict[str, Any]:
    label = identity.name or "?"
    if identity.email:
        label = f"{label} ({identity.email})"
    return {"name": identity.name, "email": identity.email, "companies": identity.companies,
            "database": identity.database, "label": label,
            "expires_at": identity.expires_at, "expiry_source": identity.expiry_source}


def remote_user(identity: probe.OdooIdentity) -> str:
    return f"{identity.database}:{identity.uid}" if identity.database else str(identity.uid)
