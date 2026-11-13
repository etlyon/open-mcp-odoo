"""The connector's only state: one SQLite file, which on its own opens nothing.

Tables:

* ``clients``      assistants that registered (DCR) or were seen by their
                   metadata document (CIMD);
* ``flows``        a sign-in in progress (steps B, C and "Allow?"), 20 minutes;
* ``connections``  one per (app, server, person on that server);
* ``codes``        one-time authorization codes, 60 seconds;
* ``tokens``       access and refresh tokens, stored only as SHA-256 hashes.

**No master key.** Each connection's secret (an Odoo key, an app password, a
token) is encrypted with its own random key ``K``. ``K`` itself is stored only
*wrapped* (encrypted) by keys derived from that connection's live code or
tokens, which the database keeps only as hashes; during sign-in it is wrapped
by a key that lives only in the person's browser cookie. So a copy of the
database, the disk or a backup reveals no secret, and once a connection's last
token has expired its secret can no longer be opened by anyone (it is then
deleted by :meth:`Store.sweep`).

Each sealed value also names the connection, app and server it belongs to; a
value moved to another row fails to open (fail closed).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    metadata TEXT NOT NULL,
    secret_hash TEXT,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS flows (
    flow_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    sealed BLOB,
    created REAL NOT NULL,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS connections (
    conn_id TEXT PRIMARY KEY,
    app TEXT NOT NULL,
    server TEXT NOT NULL,
    remote_user TEXT NOT NULL,
    client_id TEXT NOT NULL,
    resource TEXT NOT NULL,
    display TEXT NOT NULL,
    sealed BLOB NOT NULL,
    created REAL NOT NULL,
    last_used REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS codes (
    code_hash TEXT PRIMARY KEY,
    conn_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    resource TEXT NOT NULL,
    scope TEXT NOT NULL,
    wrapped BLOB NOT NULL,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    conn_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    resource TEXT NOT NULL,
    scope TEXT NOT NULL,
    family TEXT NOT NULL,
    wrapped BLOB NOT NULL,
    expires REAL NOT NULL,
    used REAL
);
CREATE INDEX IF NOT EXISTS tokens_conn ON tokens(conn_id);
CREATE INDEX IF NOT EXISTS tokens_family ON tokens(family);
"""

FLOW_SECONDS = 20 * 60
CODE_SECONDS = 60
ACCESS_SECONDS = 60 * 60
REFRESH_SECONDS = 30 * 24 * 60 * 60
WRAP_INFO = b"open-mcp-connectors/wrap/v1"


class StoreError(RuntimeError):
    pass


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_id(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def _wrap_key(material: str) -> Fernet:
    """A Fernet key derived from a high-entropy bearer value (code, token, cookie key)."""
    raw = hmac.new(WRAP_INFO, material.encode("utf-8"), hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _wrap(material: str, data_key: bytes) -> bytes:
    return _wrap_key(material).encrypt(data_key)


def _unwrap(material: str, wrapped: bytes) -> bytes:
    try:
        return _wrap_key(material).decrypt(wrapped)
    except InvalidToken:
        raise StoreError("cannot unwrap") from None


def _seal(data_key: bytes, binding: dict[str, str], secret: dict[str, Any]) -> bytes:
    payload = json.dumps({"bind": binding, "secret": secret}, separators=(",", ":"), sort_keys=True)
    return Fernet(data_key).encrypt(payload.encode("utf-8"))


def _open(data_key: bytes, binding: dict[str, str], sealed: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(Fernet(data_key).decrypt(sealed).decode("utf-8"))
    except (InvalidToken, ValueError, UnicodeDecodeError, TypeError):
        raise StoreError("sealed value cannot be opened") from None
    if payload.get("bind") != binding:
        raise StoreError("sealed value belongs to another row")
    secret = payload.get("secret")
    if not isinstance(secret, dict):
        raise StoreError("sealed value is malformed")
    return secret


@dataclass(frozen=True)
class Connection:
    conn_id: str
    app: str
    server: str
    remote_user: str
    client_id: str
    resource: str
    display: dict[str, Any]
    secret: dict[str, Any]
    scope: str = ""


@dataclass(frozen=True)
class Grant:
    """What a valid code or token unlocks."""

    conn_id: str
    client_id: str
    resource: str
    scope: str
    data_key: bytes
    family: str = ""
    redirect_uri: str = ""
    code_challenge: str = ""


class Store:
    def __init__(self, path: str | os.PathLike[str]):
        self._path = str(path)
        self._lock = threading.RLock()
        if not Path(self._path).exists():
            fd = os.open(self._path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
            os.close(fd)
        self._db = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def _tx(self):
        store = self

        class _Tx:
            def __enter__(self):
                store._lock.acquire()
                store._db.execute("BEGIN IMMEDIATE")
                return store._db

            def __exit__(self, exc_type, exc, tb):
                try:
                    store._db.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    store._lock.release()
                return False

        return _Tx()

    # ------------------------------------------------------------------ clients
    def save_client(self, client_id: str, kind: str, metadata: dict[str, Any], secret_hash: str | None = None) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO clients(client_id, kind, metadata, secret_hash, created) VALUES (?,?,?,?,?)",
                       (client_id, kind, json.dumps(metadata, sort_keys=True), secret_hash, time.time()))

    def get_client(self, client_id: str) -> tuple[str, dict[str, Any], str | None] | None:
        row = self._db.execute("SELECT kind, metadata, secret_hash FROM clients WHERE client_id=?", (client_id,)).fetchone()
        return (row[0], json.loads(row[1]), row[2]) if row else None

    def count_clients_since(self, since: float) -> int:
        return self._db.execute("SELECT COUNT(*) FROM clients WHERE kind='dcr' AND created > ?", (since,)).fetchone()[0]

    # ------------------------------------------------------------------ flows
    def new_flow(self, data: dict[str, Any]) -> str:
        flow_id = new_id()
        now = time.time()
        with self._tx() as db:
            db.execute("INSERT INTO flows(flow_id, data, sealed, created, expires) VALUES (?,?,?,?,?)",
                       (flow_id, json.dumps(data), None, now, now + FLOW_SECONDS))
        return flow_id

    def get_flow(self, flow_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT data, expires FROM flows WHERE flow_id=?", (flow_id,)).fetchone()
        if not row or row[1] < time.time():
            return None
        return json.loads(row[0])

    def update_flow(self, flow_id: str, data: dict[str, Any]) -> None:
        with self._tx() as db:
            db.execute("UPDATE flows SET data=? WHERE flow_id=?", (json.dumps(data), flow_id))

    def put_flow_secret(self, flow_id: str, cookie_key: str, secret: dict[str, Any]) -> None:
        """Hold a pasted secret between step C and "Allow?", openable only with the browser's cookie key."""
        data_key = Fernet.generate_key()
        sealed = json.dumps({"k": _wrap(cookie_key, data_key).decode("ascii"),
                             "s": _seal(data_key, {"flow": flow_id}, secret).decode("ascii")})
        with self._tx() as db:
            db.execute("UPDATE flows SET sealed=? WHERE flow_id=?", (sealed.encode("ascii"), flow_id))

    def flow_secret(self, flow_id: str, cookie_key: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT sealed FROM flows WHERE flow_id=?", (flow_id,)).fetchone()
        if not row or not row[0]:
            return None
        box = json.loads(bytes(row[0]).decode("ascii"))
        data_key = _unwrap(cookie_key, box["k"].encode("ascii"))
        return _open(data_key, {"flow": flow_id}, box["s"].encode("ascii"))

    def clear_flow_secret(self, flow_id: str) -> None:
        with self._tx() as db:
            db.execute("UPDATE flows SET sealed=NULL WHERE flow_id=?", (flow_id,))

    def seal_flow_value(self, flow_id: str, cookie_key: str, value: dict[str, Any]) -> str:
        """Seal a value a sign-in must hold for a while (e.g. a login-flow poll token) for the flow's own data.

        Only the browser's cookie key opens it, like :meth:`put_flow_secret`.
        """
        data_key = Fernet.generate_key()
        return json.dumps({"k": _wrap(cookie_key, data_key).decode("ascii"),
                           "s": _seal(data_key, {"flow": flow_id, "slot": "pending"}, value).decode("ascii")})

    def open_flow_value(self, flow_id: str, cookie_key: str, sealed: str) -> dict[str, Any]:
        try:
            box = json.loads(sealed)
            data_key = _unwrap(cookie_key, box["k"].encode("ascii"))
        except (ValueError, KeyError, TypeError, AttributeError):
            raise StoreError("sealed value cannot be opened") from None
        return _open(data_key, {"flow": flow_id, "slot": "pending"}, box["s"].encode("ascii"))

    def extend_flow(self, flow_id: str, seconds: float) -> None:
        """Keep a sign-in open for ``seconds`` more (never shortens it)."""
        with self._tx() as db:
            db.execute("UPDATE flows SET expires=MAX(expires, ?) WHERE flow_id=?", (time.time() + seconds, flow_id))

    def end_flow(self, flow_id: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM flows WHERE flow_id=?", (flow_id,))

    def count_open_flows(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM flows WHERE expires > ?", (time.time(),)).fetchone()[0]

    # ------------------------------------------------------------------ connect: Allow -> code
    def connect(self, *, app: str, server: str, remote_user: str, client_id: str, resource: str,
                display: dict[str, Any], secret: dict[str, Any], redirect_uri: str,
                code_challenge: str, scope: str) -> str:
        """Create the connection and its one-time code; return the code."""
        conn_id = new_id()
        data_key = Fernet.generate_key()
        code = new_id(32)
        binding = {"conn": conn_id, "app": app, "server": server, "user": remote_user}
        now = time.time()
        with self._tx() as db:
            db.execute(
                "INSERT INTO connections(conn_id, app, server, remote_user, client_id, resource, display, sealed, created, last_used) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (conn_id, app, server, remote_user, client_id, resource, json.dumps(display),
                 _seal(data_key, binding, secret), now, now))
            db.execute(
                "INSERT INTO codes(code_hash, conn_id, client_id, redirect_uri, code_challenge, resource, scope, wrapped, expires) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (digest(code), conn_id, client_id, redirect_uri, code_challenge, resource, scope,
                 _wrap(code, data_key), now + CODE_SECONDS))
        return code

    def take_code(self, code: str) -> Grant | None:
        """Read and delete in one step: a code works once."""
        with self._tx() as db:
            row = db.execute("SELECT conn_id, client_id, redirect_uri, code_challenge, resource, scope, wrapped, expires "
                             "FROM codes WHERE code_hash=?", (digest(code),)).fetchone()
            if row:
                db.execute("DELETE FROM codes WHERE code_hash=?", (digest(code),))
        if not row or row[7] < time.time():
            return None
        try:
            data_key = _unwrap(code, bytes(row[6]))
        except StoreError:
            return None
        return Grant(conn_id=row[0], client_id=row[1], resource=row[4], scope=row[5], data_key=data_key,
                     redirect_uri=row[2], code_challenge=row[3])

    # ------------------------------------------------------------------ tokens
    def issue_tokens(self, grant: Grant) -> tuple[str, str, int]:
        access, refresh = new_id(32), new_id(32)
        family = grant.family or new_id(12)
        now = time.time()
        with self._tx() as db:
            for value, kind, ttl in ((access, "access", ACCESS_SECONDS), (refresh, "refresh", REFRESH_SECONDS)):
                db.execute("INSERT INTO tokens VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                           (digest(value), kind, grant.conn_id, grant.client_id, grant.resource, grant.scope,
                            family, _wrap(value, grant.data_key), now + ttl))
        return access, refresh, ACCESS_SECONDS

    def check_access(self, token: str) -> Grant | None:
        row = self._db.execute(
            "SELECT conn_id, client_id, resource, scope, wrapped, expires, family FROM tokens WHERE token_hash=? AND kind='access'",
            (digest(token),)).fetchone()
        if not row or row[5] < time.time():
            return None
        try:
            data_key = _unwrap(token, bytes(row[4]))
        except StoreError:
            return None
        return Grant(conn_id=row[0], client_id=row[1], resource=row[2], scope=row[3], data_key=data_key, family=row[6])

    def rotate_refresh(self, token: str, client_id: str) -> Grant | None:
        """Use a refresh token once. Reuse of a used one kills its whole family."""
        with self._tx() as db:
            row = db.execute("SELECT conn_id, client_id, resource, scope, family, expires, used, wrapped FROM tokens "
                             "WHERE token_hash=? AND kind='refresh'", (digest(token),)).fetchone()
            if not row:
                return None
            if row[6] is not None:
                db.execute("DELETE FROM tokens WHERE family=?", (row[4],))
                return None
            if row[1] != client_id or row[5] < time.time():
                return None
            db.execute("UPDATE tokens SET used=? WHERE token_hash=?", (time.time(), digest(token)))
            db.execute("DELETE FROM tokens WHERE family=? AND kind='access'", (row[4],))
        try:
            data_key = _unwrap(token, bytes(row[7]))
        except StoreError:
            return None
        return Grant(conn_id=row[0], client_id=row[1], resource=row[2], scope=row[3], data_key=data_key, family=row[4])

    def connection_for_token(self, token: str) -> Connection | None:
        """The connection a token still on record opens (for a disconnect hook), or None.

        Expiry does not matter here: revoking an expired token still disconnects (RFC 7009), and the
        disconnect hook must see the secret it is about to lose.
        """
        if not token:
            return None
        row = self._db.execute("SELECT conn_id, client_id, resource, scope, wrapped, expires, family FROM tokens "
                               "WHERE token_hash=?", (digest(token),)).fetchone()
        if not row:
            return None
        try:
            data_key = _unwrap(token, bytes(row[4]))
            return self.get_connection(Grant(conn_id=row[0], client_id=row[1], resource=row[2], scope=row[3],
                                             data_key=data_key, family=row[6]))
        except StoreError:
            return None

    def revoke(self, token: str) -> bool:
        """RFC 7009 revocation = Disconnect: the token family AND the connection go."""
        row = self._db.execute("SELECT conn_id FROM tokens WHERE token_hash=?", (digest(token),)).fetchone()
        if not row:
            return False
        self.delete_connection(row[0])
        return True

    # ------------------------------------------------------------------ connections
    def get_connection(self, grant: Grant) -> Connection | None:
        row = self._db.execute(
            "SELECT app, server, remote_user, client_id, resource, display, sealed FROM connections WHERE conn_id=?",
            (grant.conn_id,)).fetchone()
        if not row:
            return None
        app, server, user = row[0], row[1], row[2]
        secret = _open(grant.data_key, {"conn": grant.conn_id, "app": app, "server": server, "user": user}, bytes(row[6]))
        return Connection(conn_id=grant.conn_id, app=app, server=server, remote_user=user, client_id=row[3],
                          resource=row[4], display=json.loads(row[5]), secret=secret, scope=grant.scope or "")

    def touch(self, conn_id: str) -> None:
        with self._tx() as db:
            db.execute("UPDATE connections SET last_used=? WHERE conn_id=?", (time.time(), conn_id))

    def delete_connection(self, conn_id: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM tokens WHERE conn_id=?", (conn_id,))
            db.execute("DELETE FROM codes WHERE conn_id=?", (conn_id,))
            db.execute("DELETE FROM connections WHERE conn_id=?", (conn_id,))

    def sweep(self) -> int:
        """Drop expired flows, codes and tokens, then every connection no live code or token can open."""
        now = time.time()
        with self._tx() as db:
            db.execute("DELETE FROM flows WHERE expires < ?", (now,))
            db.execute("DELETE FROM codes WHERE expires < ?", (now,))
            db.execute("DELETE FROM tokens WHERE expires < ?", (now,))
            cur = db.execute(
                "DELETE FROM connections WHERE created < ? AND conn_id NOT IN (SELECT conn_id FROM tokens) "
                "AND conn_id NOT IN (SELECT conn_id FROM codes)", (now - 2 * CODE_SECONDS,))
            return cur.rowcount

    def close(self) -> None:
        self._db.close()
