"""Who is calling: the connection the gate put in front of this request."""

from __future__ import annotations

from open_connectors.oauth.server import CURRENT
from open_connectors.store import Connection


class IdentityRefused(Exception):
    """No connection is attached to this call (the gate always attaches one)."""


def current_connection() -> Connection:
    connection = CURRENT.get()
    if connection is None:
        raise IdentityRefused("This call has no connection; connect the app again.")
    return connection


def current_account() -> str:
    """The key under which per-call state (write log, confirmations) is kept: the connection id."""
    return current_connection().conn_id
