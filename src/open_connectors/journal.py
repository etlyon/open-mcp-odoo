"""Caller-keyed idempotency for write tools, keyed (account, tool, key).

A keyed, executing call claims (account, tool, key) in a SQLite journal with
one atomic write before any upstream request, runs the unchanged tool body at
most once, and records the outcome. A repeat with the same key and arguments
returns the recorded result without calling upstream; the same key with other
arguments is refused. A failure after the request may have reached upstream is
recorded as uncertain and is never retried. Calls without a key, and previews,
never reach this module. Keyed calls fail closed when the journal is not
configured or not writable.

Port of the 2026-09-12 Odoo mechanism (``services/odoo/idempotency.py`` at
an earlier private implementation) with the account added to the key: two accounts using the
same key never share a row.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
from typing import Annotated, Any, Awaitable, Callable

from pydantic import Field

LOGGER = logging.getLogger("open_connectors.journal")

JOURNAL_ENV = "OPEN_CONNECTORS_JOURNAL"
POLL_SECONDS = 0.1

IdempotencyKey = Annotated[
    str | None,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
        json_schema_extra={"x-ebot-bookkeeping": "idempotency"},
        description=(
            "Optional caller-chosen key (1-128 characters: A-Z a-z 0-9 . _ : -). "
            "Repeating an executing call with the same key and arguments returns the "
            "recorded result without writing to Odoo again; the same key with other "
            "arguments is refused. Previews never consume a key."
        ),
    ),
]

UNAVAILABLE_MESSAGE = (
    "idempotency journal unavailable; nothing was written. "
    "Retry without idempotency_key, or ask the operator to configure the journal."
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
    account TEXT NOT NULL,
    tool TEXT NOT NULL,
    key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('in_progress', 'done', 'uncertain')),
    fingerprint TEXT NOT NULL,
    result TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (account, tool, key)
)
"""


class JournalUnavailable(Exception):
    """The journal cannot be used; keyed calls then write nothing."""


def fingerprint(arguments: dict[str, Any]) -> str:
    """sha256 of the canonical JSON of a call's arguments, key excluded."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _journal_path() -> str:
    path = os.environ.get(JOURNAL_ENV, "").strip()
    if not path:
        raise JournalUnavailable("not configured")
    directory = os.path.dirname(path) or "."
    if not os.path.isdir(directory) or not os.access(directory, os.W_OK | os.X_OK):
        raise JournalUnavailable("directory missing or not writable")
    if os.path.exists(path) and not os.access(path, os.R_OK | os.W_OK):
        raise JournalUnavailable("file not writable")
    return path


def _create_private(path: str) -> None:
    """Create a missing journal file with mode 600 before SQLite opens it.

    ``sqlite3.connect`` alone creates the file with the process umask (644 under
    the usual 022). An existing file is left as it is.
    """
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)  # JOURNAL-MODE
    except OSError as exc:
        raise JournalUnavailable(type(exc).__name__) from exc
    os.close(descriptor)


def _connect() -> sqlite3.Connection:
    path = _journal_path()
    _create_private(path)
    try:
        connection = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    except sqlite3.Error as exc:
        raise JournalUnavailable(type(exc).__name__) from exc
    try:
        connection.execute(_SCHEMA)
    except sqlite3.Error as exc:
        connection.close()
        raise JournalUnavailable(type(exc).__name__) from exc
    return connection


def _claim(account: str, tool: str, key: str, digest: str) -> tuple[str, str, str | None] | None:
    """Insert an in_progress row atomically; None means claimed, else the existing row."""
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT status, fingerprint, result FROM journal WHERE account = ? AND tool = ? AND key = ?",
            (account, tool, key),
        ).fetchone()
        if row is None:
            now = time.time()
            connection.execute(
                "INSERT INTO journal (account, tool, key, status, fingerprint, result, created_at, updated_at) "
                "VALUES (?, ?, ?, 'in_progress', ?, NULL, ?, ?)",
                (account, tool, key, digest, now, now),
            )
        connection.execute("COMMIT")
        return None if row is None else (row[0], row[1], row[2])
    except sqlite3.Error as exc:
        raise JournalUnavailable(type(exc).__name__) from exc
    finally:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        connection.close()


def _record(account: str, tool: str, key: str, status: str, result: str | None) -> bool:
    """Record a final status; False, with a warning, if the journal write failed."""
    try:
        connection = _connect()
        try:
            connection.execute(
                "UPDATE journal SET status = ?, result = ?, updated_at = ? "
                "WHERE account = ? AND tool = ? AND key = ?",
                (status, result, time.time(), account, tool, key),
            )
        finally:
            connection.close()
    except (JournalUnavailable, sqlite3.Error) as exc:
        LOGGER.warning(
            "idempotency journal write failed tool=%s key=%s reason=%s", tool, key, type(exc).__name__
        )
        return False
    return True


def _release(account: str, tool: str, key: str) -> bool:
    """Drop an in_progress claim after a definitive failure: nothing was written."""
    try:
        connection = _connect()
        try:
            connection.execute(
                "DELETE FROM journal WHERE account = ? AND tool = ? AND key = ? AND status = 'in_progress'",
                (account, tool, key),
            )
        finally:
            connection.close()
    except (JournalUnavailable, sqlite3.Error) as exc:
        LOGGER.warning(
            "idempotency journal release failed tool=%s key=%s reason=%s", tool, key, type(exc).__name__
        )
        return False
    return True


def sweep_stale_claims(timeouts: dict[str, float]) -> int:
    """At process start, mark in_progress rows older than their tool timeout uncertain."""
    try:
        connection = _connect()
    except JournalUnavailable as exc:
        LOGGER.info("idempotency journal inactive reason=%s", exc)
        return 0
    try:
        now = time.time()
        fallback = max(timeouts.values(), default=300.0)
        tools = [
            row[0]
            for row in connection.execute("SELECT DISTINCT tool FROM journal WHERE status = 'in_progress'")
        ]
        marked = 0
        for tool in tools:
            cursor = connection.execute(
                "UPDATE journal SET status = 'uncertain', updated_at = ? "
                "WHERE tool = ? AND status = 'in_progress' AND updated_at < ?",
                (now, tool, now - timeouts.get(tool, fallback)),
            )
            marked += cursor.rowcount
        rows = connection.execute("SELECT COUNT(*) FROM journal").fetchone()[0]
        LOGGER.info(
            "idempotency journal active rows=%d stale_claims_marked_uncertain=%d", rows, marked
        )
        return marked
    finally:
        connection.close()


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _mark(result: str, key: str, *, replay: bool) -> str:
    """Add the key and replay flag to a JSON-object result; other text is returned as is."""
    try:
        payload = json.loads(result)
    except (TypeError, ValueError):
        return result
    if not isinstance(payload, dict):
        return result
    payload["idempotency_key"] = key
    payload["idempotent_replay"] = replay
    return _dumps(payload)


async def _uncertain(
    tool: str,
    key: str,
    error: str | None,
    describe: Callable[[], Awaitable[dict[str, Any]]] | None,
    *,
    first_attempt: bool,
    upstream: str = "Odoo",
) -> str:
    check: dict[str, Any] | None = None
    if describe is not None:
        try:
            check = await describe()
        except Exception as exc:  # the description is advisory only
            check = {"error": type(exc).__name__}
    if first_attempt:
        detail = (
            f"This attempt may or may not have reached {upstream}. A retry with the same key will "
            "not write again; inspect check_by_ref and reconcile before using a new key."
        )
    else:
        detail = (
            f"An earlier attempt with this key may or may not have reached {upstream}. Nothing was "
            "written by this call; inspect check_by_ref and reconcile before using a new key."
        )
    return _dumps(
        {
            "status": "uncertain",
            "executed": None,
            "retry_safe": False,
            "tool": tool,
            "idempotency_key": key,
            "error": error,
            "check_by_ref": check,
            "detail": detail,
        }
    )


async def _wait_for_claim(
    account: str, tool: str, key: str, digest: str, deadline: float
) -> tuple[str, str, str | None] | None:
    """Claim the key, or wait while an earlier call with the same arguments holds it."""
    while True:
        existing = _claim(account, tool, key, digest)
        if existing is None or existing[0] != "in_progress" or existing[1] != digest:
            return existing
        if time.monotonic() >= deadline:
            return existing
        await asyncio.sleep(POLL_SECONDS)


async def _answer_existing(
    tool: str,
    key: str,
    digest: str,
    existing: tuple[str, str, str | None],
    describe: Callable[[], Awaitable[dict[str, Any]]] | None,
    upstream: str = "Odoo",
) -> tuple[str, str]:
    status, stored_digest, recorded = existing
    if stored_digest != digest:
        return "conflict", _dumps(
            {
                "error": "idempotency_key_conflict",
                "executed": False,
                "tool": tool,
                "idempotency_key": key,
                "detail": "This key was already used with different arguments; nothing was written.",
            }
        )
    if status == "done":
        return "replay", _mark(recorded or "", key, replay=True)
    if status == "uncertain":
        return "uncertain_replay", await _uncertain(
            tool, key, recorded, describe, first_attempt=False, upstream=upstream
        )
    return "still_in_progress", _dumps(
        {
            "status": "in_progress",
            "executed": None,
            "retry_safe": False,
            "tool": tool,
            "idempotency_key": key,
            "detail": (
                "An earlier call with this key is still running; nothing was written by this "
                "call. Retry later with the same key."
            ),
        }
    )


async def _execute_claimed(
    account: str,
    tool: str,
    key: str,
    execute: Callable[[], Awaitable[str]],
    already_done: Callable[[], Awaitable[str | None]] | None,
    describe: Callable[[], Awaitable[dict[str, Any]]] | None,
    classify: Callable[[str], str],
    upstream: str = "Odoo",
    record: Callable[[str], str] | None = None,
) -> tuple[str, str]:
    """Run a claimed call once and record its outcome (``record`` picks what of a success is kept)."""
    keep = record or (lambda result: result)
    settled = False
    try:
        if already_done is not None:
            try:
                done = await already_done()
            except Exception as exc:
                _release(account, tool, key)
                settled = True
                return "check_failed", _dumps(
                    {
                        "error": (
                            f"check-by-ref failed before writing ({type(exc).__name__}); "
                            "nothing was written."
                        ),
                        "executed": False,
                        "tool": tool,
                        "idempotency_key": key,
                    }
                )
            if done is not None:
                settled = _record(account, tool, key, "done", keep(done))
                return "done_by_ref", _mark(done, key, replay=False)
        result = await execute()
        kind = classify(result)
        if kind == "success":
            settled = _record(account, tool, key, "done", keep(result))
            return "done", _mark(result, key, replay=False)
        if kind == "uncertain":
            settled = _record(account, tool, key, "uncertain", result)
            return "uncertain", await _uncertain(
                tool, key, result, describe, first_attempt=True, upstream=upstream
            )
        settled = _release(account, tool, key)
        return "definitive_failure", result
    finally:
        if not settled:
            # Cancelled mid-flight (for example a dropped client stream) or the journal
            # write failed: the request may have been sent, so a repeat must not write.
            _record(account, tool, key, "uncertain", None)


async def run_keyed(
    *,
    account: str,
    tool: str,
    key: str,
    arguments: dict[str, Any],
    timeout: float,
    execute: Callable[[], Awaitable[str]],
    classify: Callable[[str], str],
    already_done: Callable[[], Awaitable[str | None]] | None = None,
    describe: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    upstream: str = "Odoo",
    record: Callable[[str], str] | None = None,
) -> str:
    """Run one keyed write at most once per (account, tool, key); see the module docstring.

    ``upstream`` names the app in the uncertain detail text (default ``Odoo``). ``record``, when given,
    turns a successful result into what the journal keeps and a repeat returns (default: all of it).
    """
    started = time.monotonic()
    outcome = "error"
    try:
        digest = fingerprint(arguments)
        try:
            existing = await _wait_for_claim(account, tool, key, digest, started + timeout)
        except JournalUnavailable as exc:
            outcome = "journal_unavailable"
            LOGGER.warning("idempotency journal unavailable tool=%s key=%s reason=%s", tool, key, exc)
            return _dumps({"error": UNAVAILABLE_MESSAGE, "executed": False, "tool": tool})
        if existing is not None:
            outcome, reply = await _answer_existing(tool, key, digest, existing, describe, upstream)
            return reply
        outcome, reply = await _execute_claimed(
            account, tool, key, execute, already_done, describe, classify, upstream, record
        )
        return reply
    finally:
        LOGGER.info(
            "idempotency tool=%s key=%s outcome=%s duration_ms=%d",
            tool,
            key,
            outcome,
            int((time.monotonic() - started) * 1000),
        )
