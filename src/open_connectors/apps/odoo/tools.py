"""Guarded Odoo tools using Odoo 19's External JSON-2 API, per connection.

The tools' logic is carried over unchanged from an earlier private
implementation. Around them:

- ``client`` resolves the calling connection's own server, database and API
  key on every call (``ConnectionOdooClient``), always through the guarded road;
- the person connects on the sign-in pages (address, key, "Allow?"), so there
  is no connect tool;
- keyed writes are journaled under (connection, tool, key);
- the policy middleware asks the person before destructive writes and money
  workflows, whatever the assistant shows.
"""

from __future__ import annotations

import base64
from contextvars import ContextVar
import json
import re
import logging
import os
from typing import Annotated, Any

import httpx
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, field_validator
from starlette.requests import Request
from starlette.responses import JSONResponse

from open_connectors import identity
from open_connectors.journal import IdempotencyKey, run_keyed
from open_connectors.policy import AccountToolPolicy
from open_connectors.reminder import KeyExpiryReminder
from open_connectors.apps.odoo.client import ConnectionOdooClient, OdooAPIError, OdooClient
from open_connectors.apps.odoo.guards import SAFE_MUTATION_CONTEXT, require_mutable_model, validate_values
from open_connectors.apps.odoo.attachments import (
    FileAttachmentInput,
    attach_pdf,
    attachment_matches,
    attachment_receipt,
    capability,
)
from open_connectors.apps.odoo.pdf_validation import PDF_MAX_BYTES

LOGGER = logging.getLogger("open_connectors.apps.odoo")

APP = "odoo"
CONNECT_TOOL = "odoo_connect"

DEFAULT_ALLOWED_MODELS = {"*"}

# Broad normal-user coverage is intentional. Odoo ACLs remain authoritative,
# while guards.py blocks external communication, system administration,
# and unsafe generic state changes. Explicit deletion follows Odoo ACLs.
DEFAULT_MUTABLE_MODELS = {"*"}

WORKFLOW_ACTIONS: dict[str, dict[str, Any]] = {
    "crm_mark_won": {
        "model": "crm.lead",
        "method": "action_set_won",
        "preview_fields": ["name", "active", "probability", "stage_id"],
    },
    "sale_confirm": {
        "model": "sale.order",
        "method": "action_confirm",
        "preview_fields": ["name", "state", "partner_id", "amount_total", "currency_id"],
    },
    "sale_cancel": {
        "model": "sale.order",
        "method": "action_cancel",
        "preview_fields": ["name", "state", "partner_id", "amount_total", "currency_id"],
    },
    "purchase_confirm": {
        "model": "purchase.order",
        "method": "button_confirm",
        "preview_fields": ["name", "state", "partner_id", "amount_total", "currency_id"],
    },
    "purchase_cancel": {
        "model": "purchase.order",
        "method": "button_cancel",
        "preview_fields": ["name", "state", "partner_id", "amount_total", "currency_id"],
    },
    "stock_validate": {
        "model": "stock.picking",
        "method": "button_validate",
        "preview_fields": ["name", "state", "partner_id", "scheduled_date", "picking_type_id"],
    },
    "stock_cancel": {
        "model": "stock.picking",
        "method": "action_cancel",
        "preview_fields": ["name", "state", "partner_id", "scheduled_date", "picking_type_id"],
    },
    "account_post": {
        "model": "account.move",
        "method": "action_post",
        "preview_fields": [
            "name",
            "state",
            "move_type",
            "partner_id",
            "amount_total",
            "currency_id",
            "peppol_move_state",
            "sending_data",
        ],
    },
    "account_reset_to_draft": {
        "model": "account.move",
        "method": "button_draft",
        "preview_fields": ["name", "state", "move_type", "peppol_move_state", "sending_data"],
    },
    "account_cancel": {
        "model": "account.move",
        "method": "button_cancel",
        "preview_fields": ["name", "state", "move_type", "peppol_move_state", "sending_data"],
    },
    "payment_post": {
        "model": "account.payment",
        "method": "action_post",
        "preview_fields": [
            "name", "state", "partner_id", "amount", "currency_id", "payment_type", "partner_type",
        ],
    },
    "payment_cancel": {
        "model": "account.payment",
        "method": "action_cancel",
        "preview_fields": [
            "name", "state", "partner_id", "amount", "currency_id", "payment_type", "partner_type",
        ],
    },
}

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
WRITE_ACTION = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
DESTRUCTIVE_ACTION = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)

# These tools have a body-level, read-only preview controlled by ``execute``.
# The policy middleware may let only their literal false/default form reach the
# body without asking. Tools that always write (delete, notes) are absent.
ODOO_PREVIEW_TOOLS = frozenset(
    {
        "odoo_archive_records",
        "odoo_attach_file",
        "odoo_attach_text",
        "odoo_create_record",
        "odoo_run_workflow",
        "odoo_schedule_activity",
        "odoo_update_records",
        "odoo_upsert_record",
    }
)

ODOO_ALLOWED_MODELS = {
    item.strip()
    for item in os.environ.get("ODOO_ALLOWED_MODELS", ",".join(sorted(DEFAULT_ALLOWED_MODELS))).split(",")
    if item.strip()
}
ODOO_MUTABLE_MODELS = {
    item.strip()
    for item in os.environ.get("ODOO_MUTABLE_MODELS", ",".join(sorted(DEFAULT_MUTABLE_MODELS))).split(",")
    if item.strip()
}
ODOO_MAX_LIMIT = min(max(int(os.environ.get("ODOO_MAX_LIMIT", "100")), 1), 500)
ODOO_PDF_ATTACHMENTS_ENABLED = os.environ.get("ODOO_PDF_ATTACHMENTS_ENABLED", "false").lower() == "true"
ODOO_PDF_MAX_BYTES = min(max(int(os.environ.get("ODOO_PDF_MAX_BYTES", str(PDF_MAX_BYTES))), 1), PDF_MAX_BYTES)

mcp = FastMCP(
    name="Odoo",
    instructions=(
        "Use Odoo as the system of record. Discover the model when unsure, then search before reading or writing. "
        "For a total, count, average or breakdown use odoo_read_group, which aggregates inside Odoo; "
        "do not read rows in order to add them up yourself. "
        "When the person asked you to save or update a record (contact, note, activity, attach), call the write "
        "tool with execute=true at once and answer with the record link; do not ask them to say yes again. "
        "Use execute=false only to inspect what a write would do when they have not yet asked to save. "
        "Internal note and document-summary tools save immediately; do not report success unless executed=true. "
        "Ask before deleting, archiving, or running a workflow that posts or confirms money. "
        "For deletion, unlink=true from odoo_access_rights means odoo_delete_records is allowed even when "
        "generic create/update policy is blocked. "
        "Use odoo_attach_file for original PDF bytes when its dedicated capability is enabled, "
        "even if ir.attachment generic writes are false. "
        "External communications remain blocked. "
        "Every call acts as the connected person's own Odoo user, with that user's Odoo rights. "
        "If Odoo refuses the key, tell the person to connect Odoo again in their assistant."
    ),
    middleware=[
        KeyExpiryReminder(APP),
        AccountToolPolicy(
            app=APP,
            connect_tool=CONNECT_TOOL,
            preview_tools=ODOO_PREVIEW_TOOLS,
            # Modern form elicitation is a two-leg result with no server-side
            # wait to bound. Odoo approvals therefore use the same-arguments,
            # single-use token path; legacy imperative elicitation stays
            # available with a bounded wait in the shared middleware.
            modern_token_only=True,
        )
    ],
)

# FastMCP 4 takes the HTTP options at run() time, not on the constructor.
HTTP_RUN_OPTIONS: dict[str, Any] = {
    "host": os.environ.get("HOST", "0.0.0.0"),
    "port": int(os.environ.get("PORT", "8000")),
    "path": "/mcp",
    "json_response": True,
    "stateless_http": True,
}

# How long a second caller with the same key waits for an in-flight claim, and
# how old an in_progress row must be before a restart calls it uncertain. Each
# value covers that tool's worst-case sequence of 30 s Odoo calls.
KEYED_TOOL_TIMEOUTS: dict[str, float] = {
    "odoo_create_record": 180.0,
    "odoo_update_records": 180.0,
    "odoo_upsert_record": 180.0,
    "odoo_archive_records": 180.0,
    "odoo_delete_records": 180.0,
    "odoo_log_internal_note": 180.0,
    "odoo_log_document_summary": 180.0,
    "odoo_attach_file": 300.0,
    "odoo_attach_text": 180.0,
    "odoo_schedule_activity": 180.0,
    "odoo_run_workflow": 180.0,
}

client = ConnectionOdooClient(
    allowed_models=ODOO_ALLOWED_MODELS,
    app=APP,
)


def startup_check() -> None:
    """Refuse to start with empty model sets."""
    if not ODOO_ALLOWED_MODELS:
        raise ValueError("ODOO_ALLOWED_MODELS must contain at least one model.")
    if not ODOO_MUTABLE_MODELS:
        raise ValueError("ODOO_MUTABLE_MODELS must contain at least one model.")
    if not callable(odoo_connection_status):
        raise ValueError("odoo_connection_status is not registered.")


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "odoo-connector", "transport": "stateless-http"})


class ModelInput(BaseModel):
    model: str = Field(description="Allowlisted Odoo model, for example res.partner")


class SearchInput(ModelInput):
    domain: list[Any] = Field(default_factory=list, description="Odoo domain expression")
    context: dict[str, Any] = Field(default_factory=dict)


class SearchReadInput(SearchInput):
    fields: list[str] = Field(description="Explicit fields to return")
    limit: int = Field(default=20, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
    order: str | None = Field(default=None, description="Odoo order expression")

    @field_validator("fields")
    @classmethod
    def require_fields(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("At least one field is required.")
        return value


# Odoo 19 exposes aggregation over the External JSON-2 API as
# `formatted_read_group`. Measured live against Odoo 19.0-20251222 on 2026-09-12:
# the classic `read_group` still answers but requires `fields` and returns bare
# field names; `formatted_read_group` requires `aggregates`, returns explicit
# "<field>:<method>" keys, and always returns fully expanded rows, so it needs no
# `lazy` flag and in fact rejects one. The private `_read_group` is refused
# remotely by Odoo itself.
READ_GROUP_METHOD = "formatted_read_group"

# Every groupby and aggregate token is an identifier, optionally one property
# hop, optionally one ":suffix" (a date granularity for a groupby, an aggregate
# method for an aggregate). Anchored and punctuation-free, so no expression,
# quote, parenthesis, comma or space can reach Odoo's group-by parser.
_SPEC_NAME = r"[A-Za-z_][A-Za-z0-9_]{0,62}(?:\.[A-Za-z_][A-Za-z0-9_]{0,62})?"
_GROUPBY_SPEC = re.compile(rf"^{_SPEC_NAME}(?::[a-z][a-z0-9_]{{0,31}})?$")
_AGGREGATE_SPEC = re.compile(rf"^{_SPEC_NAME}:([a-z][a-z0-9_]{{0,31}})$")
_ORDER_TERM = re.compile(rf"^{_SPEC_NAME}(?::[a-z][a-z0-9_]{{0,31}})?(?: (?:asc|desc))?$", re.IGNORECASE)

# Odoo 19's aggregate vocabulary. sum, avg, min, max, count, count_distinct and
# array_agg were confirmed against the live server; bool_and and bool_or are the
# remaining pair Odoo accepts. Anything else is refused before any Odoo call.
AGGREGATE_METHODS = frozenset(
    {"sum", "avg", "min", "max", "count", "count_distinct", "array_agg", "bool_and", "bool_or"}
)

# Row count, always requested so a caller can tell an empty group from a zero sum.
GROUP_COUNT = "__count"

# `limit` bounds the number of groups returned, not the depth of the grouping,
# so depth is bounded separately: each extra level multiplies the cross-product.
# A question that needs a fourth level is better asked as a narrower domain.
MAX_GROUPBY_DEPTH = 3


class ReadGroupInput(ModelInput):
    domain: list[Any] = Field(default_factory=list, description="Odoo domain expression")
    groupby: list[str] = Field(
        default_factory=list,
        max_length=MAX_GROUPBY_DEPTH,
        description=(
            "Up to three grouping fields, for example ['partner_id'] or "
            "['partner_id', 'invoice_date:month']. A date field takes a granularity suffix "
            "(year, quarter, month, week, day). An empty list returns one grand-total row."
        ),
    )
    aggregates: list[str] = Field(
        default_factory=lambda: [GROUP_COUNT],
        max_length=10,
        description=(
            "Measures as '<field>:<method>', for example 'amount_total:sum'. Methods: sum, avg, "
            "min, max, count, count_distinct, array_agg, bool_and, bool_or. '__count' is always included."
        ),
    )
    limit: int = Field(default=50, ge=1, le=500, description="Maximum number of groups to return")
    offset: int = Field(default=0, ge=0, description="Groups to skip, for paging")
    order: str | None = Field(
        default=None,
        max_length=200,
        description="Order over groupby or aggregate specs, for example 'amount_total:sum DESC'",
    )
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("groupby")
    @classmethod
    def check_groupby(cls, value: list[str]) -> list[str]:
        for spec in value:
            if not _GROUPBY_SPEC.fullmatch(spec):
                raise ValueError(
                    f"Invalid groupby specification '{spec}'. "
                    "Use '<field>' or '<date_field>:<granularity>'."
                )
        return value

    @field_validator("aggregates")
    @classmethod
    def check_aggregates(cls, value: list[str]) -> list[str]:
        for spec in value:
            if spec == GROUP_COUNT:
                continue
            match = _AGGREGATE_SPEC.fullmatch(spec)
            if not match:
                raise ValueError(
                    f"Invalid aggregate specification '{spec}'. "
                    "Use '<field>:<method>', for example 'amount_total:sum'."
                )
            if match.group(1) not in AGGREGATE_METHODS:
                raise ValueError(
                    f"Unsupported aggregate method '{match.group(1)}'. "
                    f"Allowed: {', '.join(sorted(AGGREGATE_METHODS))}."
                )
        return value

    @field_validator("order")
    @classmethod
    def check_order(cls, value: str | None) -> str | None:
        if value is None:
            return None
        for term in value.split(","):
            if not _ORDER_TERM.fullmatch(term.strip()):
                raise ValueError(
                    f"Invalid order term '{term.strip()}'. Use '<spec> [ASC|DESC]', comma separated."
                )
        return value


class ReadInput(ModelInput):
    ids: list[int] = Field(min_length=1, max_length=100)
    fields: list[str] = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class FieldsInput(ModelInput):
    attributes: list[str] = Field(
        default_factory=lambda: ["string", "type", "required", "readonly", "relation", "help"]
    )
    context: dict[str, Any] = Field(default_factory=dict)


class AccessRightsInput(BaseModel):
    models: list[str] = Field(min_length=1, max_length=50)


class ModelCatalogInput(BaseModel):
    query: str = Field(default="", max_length=100, description="Optional partial technical or display name")
    limit: int = Field(default=50, ge=1, le=200)


class CreateInput(ModelInput):
    idempotency_key: IdempotencyKey = None
    values: dict[str, Any] = Field(description="Odoo field values for one new record")
    return_fields: list[str] = Field(default_factory=lambda: ["id", "display_name"], max_length=30)
    execute: bool = Field(default=False, description="False previews; true performs the save")


class UpdateInput(ModelInput):
    idempotency_key: IdempotencyKey = None
    ids: list[int] = Field(min_length=1, max_length=100)
    values: dict[str, Any] = Field(description="Odoo field values to apply to every selected record")
    return_fields: list[str] = Field(default_factory=lambda: ["id", "display_name"], max_length=30)
    execute: bool = Field(default=False, description="False previews; true performs the save")


class UpsertInput(ModelInput):
    idempotency_key: IdempotencyKey = None
    domain: list[Any] = Field(description="Exact-match lookup domain; zero matches creates, one updates")
    values: dict[str, Any]
    return_fields: list[str] = Field(default_factory=lambda: ["id", "display_name"], max_length=30)
    execute: bool = Field(default=False, description="False previews; true performs the save")


class ArchiveInput(ModelInput):
    idempotency_key: IdempotencyKey = None
    ids: list[int] = Field(min_length=1, max_length=100)
    archived: bool = Field(default=True, description="True archives; false restores archived records")
    execute: bool = Field(default=False, description="False previews; true performs the change")


class DeleteInput(ModelInput):
    idempotency_key: IdempotencyKey = None
    ids: list[int] = Field(min_length=1, max_length=100)
    reason: str | None = Field(
        default=None,
        max_length=500,
        description="Optional audit explanation supplied by the user",
    )


class InternalNoteInput(ModelInput):
    idempotency_key: IdempotencyKey = None
    record_id: int = Field(gt=0)
    note: str = Field(min_length=1, max_length=20000)


class DocumentSummaryInput(ModelInput):
    idempotency_key: IdempotencyKey = None
    record_id: int = Field(gt=0)
    source_name: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=100000)
    source_url: str | None = Field(default=None, max_length=2048)
    extracted_fields: dict[str, Any] = Field(default_factory=dict)


class TextAttachmentInput(ModelInput):
    idempotency_key: IdempotencyKey = None
    record_id: int = Field(gt=0)
    filename: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=250000)
    mimetype: str = Field(default="text/markdown", pattern=r"^(text/(plain|markdown)|application/json)$")
    description: str | None = Field(default=None, max_length=2000)
    execute: bool = Field(default=False, description="False previews; true stores the attachment")


class WorkflowInput(BaseModel):
    idempotency_key: IdempotencyKey = None
    action: str = Field(description="One named internal workflow from odoo_allowed_models")
    ids: list[int] = Field(min_length=1, max_length=20)
    execute: bool = Field(default=False, description="False previews; true runs the state transition")


class ScheduleActivityInput(ModelInput):
    idempotency_key: IdempotencyKey = None
    record_id: int = Field(gt=0)
    summary: str = Field(min_length=1, max_length=500)
    note: str = Field(default="", max_length=10000)
    due_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    user_id: int | None = Field(default=None, gt=0, description="Internal Odoo user; defaults to API user")
    activity_type_xmlid: str = Field(
        default="mail.mail_activity_data_todo",
        pattern=r"^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$",
    )
    execute: bool = Field(default=False, description="False previews; true schedules the activity")


_LAST_ERROR_TYPE: ContextVar[str | None] = ContextVar("_last_error_type", default=None)

# httpx failures that can occur after the request reached Odoo, so the write may
# have been applied even though the tool reported an error.
_MAYBE_DELIVERED = frozenset(
    {
        "NetworkError",
        "ProtocolError",
        "ReadError",
        "ReadTimeout",
        "RemoteProtocolError",
        "TimeoutException",
        "TransportError",
        "WriteError",
        "WriteTimeout",
    }
)
_ODOO_HTTP_STATUS = re.compile(r"^Odoo API HTTP (\d{3}): ")


def classify_odoo_result(result: str) -> str:
    """Return "success", "definitive" (nothing was written) or "uncertain".

    Odoo tools report failure as a JSON object with an "error" key rather than
    an error string, and `attach_pdf` reports an ambiguous create in its own
    receipt. Both shapes are read here; when a call cannot be proven harmless,
    it is called uncertain, because a wrong "definitive" would allow a retry to
    write twice while a wrong "uncertain" only costs the caller a fresh key.
    """
    error_type = _LAST_ERROR_TYPE.get()
    try:
        payload = json.loads(result)
    except (TypeError, ValueError):
        return "uncertain"
    if not isinstance(payload, dict):
        return "uncertain"
    if "error" not in payload:
        # attach_pdf reports an ambiguous outcome without raising.
        if payload.get("executed") is None or payload.get("status") in {
            "create_outcome_unknown",
            "created_identifier_unknown",
        }:
            return "uncertain"
        return "success"
    message = str(payload.get("error", ""))
    status = _ODOO_HTTP_STATUS.match(message)
    if status:
        # Odoo answered: 5xx may have applied the write, 4xx did not.
        return "uncertain" if int(status.group(1)) >= 500 else "definitive"
    if message == "Odoo request timed out.":
        return "uncertain"
    if message == "Could not reach the Odoo API.":
        # httpx.HTTPError covers both "never connected" and "lost after sending".
        return "definitive" if error_type in {"ConnectError", "ConnectTimeout", "PoolTimeout"} else "uncertain"
    if error_type is not None and error_type in _MAYBE_DELIVERED:
        return "uncertain"
    if message.startswith("Unexpected "):
        return "uncertain"
    # Everything else is a policy, allowlist, ACL, validation or lookup refusal
    # raised before any mutating call was made.
    return "definitive"


def _keyed_arguments(params: BaseModel) -> dict[str, Any]:
    return params.model_dump(mode="json", exclude={"idempotency_key"})


async def _dispatch(
    tool: str,
    params: BaseModel,
    impl: Any,
    already_done: Any = None,
    describe: Any = None,
) -> str:
    """Run a write tool once per (account, key). No key, or a preview, runs the body directly."""
    key = getattr(params, "idempotency_key", None)
    if key is None or not getattr(params, "execute", True):
        return await impl(params)
    try:
        account = identity.current_account()
    except identity.IdentityRefused as exc:
        return _json({"error": str(exc), "executed": False, "tool": tool})
    _LAST_ERROR_TYPE.set(None)
    return await run_keyed(
        account=account,
        tool=tool,
        key=key,
        arguments=_keyed_arguments(params),
        timeout=KEYED_TOOL_TIMEOUTS[tool],
        execute=lambda: impl(params),
        classify=classify_odoo_result,
        already_done=(lambda: already_done(params)) if already_done is not None else None,
        describe=(lambda: describe(params)) if describe is not None else None,
    )


async def _attachment_already_created(params: Any) -> str | None:
    """Read-only check-by-ref: Odoo's own ir.attachment checksum and linkage."""
    matches = await attachment_matches(client, params)
    if len(matches) == 1:
        return _json(attachment_receipt(params, matches[0]))
    return None


async def _attachment_ref_state(params: Any) -> dict[str, Any]:
    try:
        matches = await attachment_matches(client, params)
    except Exception as exc:
        return {"error": type(exc).__name__}
    return {
        "model": params.model,
        "record_id": params.record_id,
        "filename": params.filename,
        "matching_attachment_ids": [row.get("id") for row in matches],
    }


async def _records_absent(params: Any) -> str | None:
    """Check-by-ref for delete: every target already absent means the work is done."""
    rows = await client.call(
        params.model, "read", {"ids": params.ids, "fields": ["id"], "load": None}
    )
    if rows:
        return None
    return _json(
        {
            "executed": False,
            "operation": "delete",
            "model": params.model,
            "deleted_ids": [],
            "already_absent": True,
            "requested_ids": params.ids,
            "reason": params.reason,
            "authority": "Odoo ACLs and record rules",
        }
    )


async def _records_ref_state(params: Any) -> dict[str, Any]:
    try:
        rows = await client.call(
            params.model, "read", {"ids": params.ids, "fields": ["id"], "load": None}
        )
    except Exception as exc:
        return {"error": type(exc).__name__}
    return {"model": params.model, "requested_ids": params.ids, "present_ids": [r.get("id") for r in rows]}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _error(exc: Exception) -> str:
    # The returned text is unchanged. The exception type is recorded out of band
    # so the idempotency layer can tell a failure that never reached Odoo from
    # one that may have been applied. Unkeyed callers never observe this.
    _LAST_ERROR_TYPE.set(
        exc.source_type if isinstance(exc, OdooAPIError) and exc.source_type else type(exc).__name__
    )
    if isinstance(exc, OdooAPIError):
        message = str(exc)
    elif isinstance(exc, httpx.TimeoutException):
        message = "Odoo request timed out."
    elif isinstance(exc, httpx.HTTPError):
        message = "Could not reach the Odoo API."
    else:
        message = f"Unexpected {type(exc).__name__}."
    return _json({"error": message})


def _connection_failure(exc: Exception) -> dict[str, Any]:
    """Return one actionable, credential-free diagnostic category."""
    if isinstance(exc, OdooAPIError):
        category = exc.category
        detail = str(exc)
    elif isinstance(exc, httpx.TimeoutException):
        category, detail = "unreachable", "Odoo request timed out."
    elif isinstance(exc, httpx.HTTPError):
        category, detail = "unreachable", "Could not reach the Odoo API."
    else:
        category, detail = "unexpected", f"Unexpected {type(exc).__name__}."
    actions = {
        "not_connected": "Connect Odoo again in your assistant.",
        "credential_rejected": (
            "Odoo refused this connection's key; it has probably expired or been deleted. "
            "Connect Odoo again in your assistant with a new key."
        ),
        "acl_denied": "Odoo knows the user but refused this check. Ask an Odoo administrator about this user's access.",
        "compatibility_missing": "This Odoo does not offer the method this check needs (Odoo 19 or newer is required).",
        "invalid_request": "Odoo rejected the check request.",
        "request_conflict": "Odoo reported a request conflict. Wait briefly, then run the diagnostic again.",
        "request_refused": "Odoo refused the check request.",
        "rate_limited": "Odoo is rate limiting requests. Wait briefly, then run the diagnostic again.",
        "unreachable": "Odoo could not be reached after one bounded read retry. Check Odoo availability and routing.",
        "invalid_response": "Odoo returned an unexpected answer to the check.",
        "odoo_error": "Odoo answered the check with an error.",
        "unexpected": "The check failed unexpectedly.",
    }
    return {
        "status": category,
        "connected": False,
        "credential_verified_for_this_call": False,
        "detail": detail,
        "action": actions.get(category, actions["odoo_error"]),
        "credential_material_exposed": False,
    }


def _return_fields(fields: list[str]) -> list[str]:
    ordered: list[str] = []
    for field in ["id", *fields]:
        if field not in ordered:
            ordered.append(field)
    return ordered


def _normalise_created_ids(value: Any) -> list[int]:
    if isinstance(value, int):
        return [value]
    if isinstance(value, list) and all(isinstance(item, int) for item in value):
        return value
    if isinstance(value, dict) and isinstance(value.get("ids"), list):
        return [item for item in value["ids"] if isinstance(item, int)]
    raise OdooAPIError("Odoo created the record but returned an unexpected identifier format.")


async def _validate_mutation(model: str, values: dict[str, Any]) -> dict[str, dict[str, Any]]:
    client.require_model(model)
    require_mutable_model(model, ODOO_MUTABLE_MODELS)
    validate_values(values)
    metadata = await client.call(
        model,
        "fields_get",
        {"attributes": ["type", "readonly", "store", "relation"]},
    )
    missing = sorted(set(values).difference(metadata))
    if missing:
        raise OdooAPIError(f"Unknown fields for {model}: {', '.join(missing)}")
    readonly = sorted(name for name in values if metadata[name].get("readonly"))
    if readonly:
        raise OdooAPIError(f"Readonly fields for {model}: {', '.join(readonly)}")
    return metadata


async def _read_records(model: str, ids: list[int], fields: list[str]) -> Any:
    return await client.call(
        model,
        "read",
        {"ids": ids, "fields": _return_fields(fields), "load": None},
    )


async def _post_internal_note(model: str, record_id: int, note: str) -> Any:
    client.require_model(model)
    require_mutable_model(model, ODOO_MUTABLE_MODELS)
    await _read_records(model, [record_id], ["display_name"])
    return await client.call(
        model,
        "message_post",
        {
            "ids": [record_id],
            "body": note,
            "body_is_html": False,
            "message_type": "comment",
            "subtype_xmlid": "mail.mt_note",
            "partner_ids": [],
            "context": {
                "mail_create_nosubscribe": True,
                "mail_notify_force_send": False,
            },
        },
    )


# --- Existing tools (logic unchanged) plus one read-only diagnostic --------


@mcp.tool(name="odoo_health", annotations=READ_ONLY)
async def odoo_health() -> str:
    """Check Odoo reachability and report its public server version."""
    try:
        return _json(await client.version())
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_allowed_models", annotations=READ_ONLY)
async def odoo_allowed_models() -> str:
    """List configured read and mutation boundaries."""
    return _json(
        {
            "read_models": "all models allowed by the Odoo integration user's ACLs"
            if "*" in ODOO_ALLOWED_MODELS else sorted(ODOO_ALLOWED_MODELS),
            "mutable_models": "all normal business models allowed by Odoo ACLs and MCP policy"
            if "*" in ODOO_MUTABLE_MODELS else sorted(ODOO_MUTABLE_MODELS),
            "internal_workflows": sorted(WORKFLOW_ACTIONS),
            "max_limit": ODOO_MAX_LIMIT,
            "pdf_attachments": capability(ODOO_PDF_ATTACHMENTS_ENABLED, ODOO_MUTABLE_MODELS, ODOO_PDF_MAX_BYTES),
            "external_communication": "blocked",
            "delete": "allowed when explicitly requested and permitted by Odoo ACLs/record rules",
        }
    )


@mcp.tool(name="odoo_whoami", annotations=READ_ONLY)
async def odoo_whoami() -> str:
    """Return the Odoo context associated with the configured API key."""
    try:
        return _json(await client.call("res.users", "context_get", {}, enforce_allowlist=False))
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_connection_status", annotations=READ_ONLY)
async def odoo_connection_status() -> str:
    """Verify this connection's Odoo key and show whose it is (name, login, active).

    Read-only; never returns key material.
    """
    try:
        credential = client.credential_for_current_account()
        context = await client.call("res.users", "context_get", {}, enforce_allowlist=False, safe_read=True)
        uid = context.get("uid") if isinstance(context, dict) else None
        if type(uid) is not int or uid <= 0:
            raise OdooAPIError("Odoo named no user.", category="invalid_response")
        users = await client.call(
            "res.users", "read",
            {"ids": [uid], "fields": ["id", "name", "login", "email", "active"], "load": None},
            enforce_allowlist=False, safe_read=True,
        )
        user = users[0] if isinstance(users, list) and len(users) == 1 and isinstance(users[0], dict) else None
        if not user or user.get("id") != uid or user.get("active") is False:
            raise OdooAPIError("Odoo returned no active user.", category="invalid_response")
        return _json({
            "status": "connected",
            "connected": True,
            "credential_verified_for_this_call": True,
            "identity": {
                "uid": uid,
                "display_name": user.get("name"),
                "login": user.get("login") or user.get("email"),
                "active": user.get("active"),
            },
            **credential.status(),
            "credential_material_exposed": False,
        })
    except Exception as exc:
        return _json(_connection_failure(exc))


@mcp.tool(name="odoo_model_catalog", annotations=READ_ONLY)
async def odoo_model_catalog(params: ModelCatalogInput) -> str:
    """Discover installed persistent Odoo models before choosing a model for a task."""
    try:
        query = params.query.strip()
        domain: list[Any] = [["transient", "=", False]]
        if query:
            domain.extend(["|", ["name", "ilike", query], ["model", "ilike", query]])
        models = await client.call(
            "ir.model",
            "search_read",
            {
                "domain": domain,
                "fields": ["id", "name", "model", "transient"],
                "limit": params.limit,
                "order": "name",
            },
            enforce_allowlist=False,
        )
        for model in models:
            try:
                require_mutable_model(model["model"], ODOO_MUTABLE_MODELS)
                model["mcp_generic_write_policy"] = True
            except OdooAPIError:
                model["mcp_generic_write_policy"] = False
            model["mcp_delete_policy"] = "Allowed when Odoo grants unlink ACL and record-rule access"
        return _json({"query": query, "models": models})
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_search_count", annotations=READ_ONLY)
async def odoo_search_count(params: SearchInput) -> str:
    """Count records matching an Odoo domain without reading record data."""
    try:
        payload = {"domain": params.domain, "context": params.context}
        return _json({"count": await client.call(params.model, "search_count", payload)})
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_search_read", annotations=READ_ONLY)
async def odoo_search_read(params: SearchReadInput) -> str:
    """Search and read allowlisted Odoo records with an enforced result limit."""
    try:
        payload: dict[str, Any] = {
            "domain": params.domain,
            "fields": params.fields,
            "limit": min(params.limit, ODOO_MAX_LIMIT),
            "offset": params.offset,
            "context": params.context,
        }
        if params.order:
            payload["order"] = params.order
        return _json(await client.call(params.model, "search_read", payload))
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_read_group", annotations=READ_ONLY)
async def odoo_read_group(params: ReadGroupInput) -> str:
    """Aggregate records inside Odoo instead of reading rows and adding them up.

    Maps to Odoo 19's `formatted_read_group`: one row per group, with explicit
    '<field>:<method>' keys. Revenue by customer this quarter is model
    'account.move', domain [["move_type","=","out_invoice"],["state","=","posted"],
    ["invoice_date",">=","2026-07-01"],["invoice_date","<=","2026-09-30"]],
    groupby ["partner_id"], aggregates ["amount_total:sum"]. Prefer this over
    odoo_search_read whenever the answer is a total, count, average or breakdown.
    """
    try:
        aggregates = list(params.aggregates)
        if GROUP_COUNT not in aggregates:
            aggregates.append(GROUP_COUNT)
        limit = min(params.limit, ODOO_MAX_LIMIT)
        payload: dict[str, Any] = {
            "domain": params.domain,
            "groupby": params.groupby,
            "aggregates": aggregates,
            "limit": limit,
            "offset": params.offset,
            "context": params.context,
        }
        if params.order:
            payload["order"] = params.order
        groups = await client.call(params.model, READ_GROUP_METHOD, payload)
        return _json(
            {
                "model": params.model,
                "method": READ_GROUP_METHOD,
                "groupby": params.groupby,
                "aggregates": aggregates,
                "limit": limit,
                "offset": params.offset,
                "group_count": len(groups) if isinstance(groups, list) else None,
                "more_groups_possible": isinstance(groups, list) and len(groups) == limit,
                "groups": groups,
            }
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_read", annotations=READ_ONLY)
async def odoo_read(params: ReadInput) -> str:
    """Read explicit fields from up to 100 allowlisted Odoo record IDs."""
    try:
        payload = {
            "ids": params.ids,
            "fields": params.fields,
            "context": params.context,
            "load": None,
        }
        return _json(await client.call(params.model, "read", payload))
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_fields_get", annotations=READ_ONLY)
async def odoo_fields_get(params: FieldsInput) -> str:
    """Describe fields for an allowlisted Odoo model."""
    try:
        payload = {"attributes": params.attributes, "context": params.context}
        return _json(await client.call(params.model, "fields_get", payload))
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_access_rights", annotations=READ_ONLY)
async def odoo_access_rights(params: AccessRightsInput) -> str:
    """Report the integration user's effective CRUD rights for configured models."""
    try:
        results: dict[str, Any] = {}
        for model in params.models:
            try:
                client.require_model(model)
                rights: dict[str, Any] = {}
                for operation in ("read", "create", "write", "unlink"):
                    rights[operation] = bool(
                        await client.call(
                            model,
                            "check_access_rights",
                            {"operation": operation, "raise_exception": False},
                        )
                    )
                try:
                    require_mutable_model(model, ODOO_MUTABLE_MODELS)
                    generic_write_policy = True
                except OdooAPIError:
                    generic_write_policy = False
                rights["mcp_generic_write"] = generic_write_policy and (
                    rights["create"] or rights["write"]
                )
                rights["mcp_delete"] = rights["unlink"]
                rights["mcp_mutable"] = rights["mcp_generic_write"] or rights["mcp_delete"]
                rights["policy_note"] = (
                    "Delete is allowed through odoo_delete_records when mcp_delete=true; "
                    "generic create/update restrictions do not block explicit deletion."
                )
                rights["available"] = True
                if model == "ir.attachment":
                    rights["pdf_attachments"] = {
                        **capability(ODOO_PDF_ATTACHMENTS_ENABLED, ODOO_MUTABLE_MODELS, ODOO_PDF_MAX_BYTES),
                        "odoo_create_access": rights["create"],
                        "destination_access_checked_on_call": True,
                    }
                results[model] = rights
            except OdooAPIError as exc:
                results[model] = {"available": False, "error": str(exc)}
        return _json(results)
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_create_record", annotations=WRITE_ACTION)
async def odoo_create_record(params: CreateInput) -> str:
    """Preview or create one internal Odoo record without outbound notifications."""
    return await _dispatch(
        "odoo_create_record",
        params,
        _impl_odoo_create_record,
    )


async def _impl_odoo_create_record(params: CreateInput) -> str:
    try:
        await _validate_mutation(params.model, params.values)
        if not params.execute:
            return _json(
                {
                    "executed": False,
                    "operation": "create",
                    "model": params.model,
                    "values": params.values,
                    "next": "If the person already asked to save this, call again with execute=true now.",
                }
            )
        created = await client.call(
            params.model,
            "create",
            {"vals_list": params.values, "context": SAFE_MUTATION_CONTEXT},
        )
        ids = _normalise_created_ids(created)
        return _json(
            {
                "executed": True,
                "operation": "create",
                "records": await _read_records(params.model, ids, params.return_fields),
            }
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_update_records", annotations=WRITE_ACTION)
async def odoo_update_records(params: UpdateInput) -> str:
    """Preview or update explicit records with chatter/email side effects suppressed."""
    return await _dispatch(
        "odoo_update_records",
        params,
        _impl_odoo_update_records,
    )


async def _impl_odoo_update_records(params: UpdateInput) -> str:
    try:
        await _validate_mutation(params.model, params.values)
        before = await _read_records(params.model, params.ids, list(params.values))
        if len(before) != len(set(params.ids)):
            raise OdooAPIError("One or more requested records do not exist or are not accessible.")
        if not params.execute:
            return _json(
                {
                    "executed": False,
                    "operation": "update",
                    "model": params.model,
                    "before": before,
                    "values": params.values,
                    "next": "If the person already asked to save this change, call again with execute=true now.",
                }
            )
        await client.call(
            params.model,
            "write",
            {"ids": params.ids, "vals": params.values, "context": SAFE_MUTATION_CONTEXT},
        )
        return _json(
            {
                "executed": True,
                "operation": "update",
                "before": before,
                "records": await _read_records(
                    params.model,
                    params.ids,
                    [*params.return_fields, *params.values],
                ),
            }
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_upsert_record", annotations=WRITE_ACTION)
async def odoo_upsert_record(params: UpsertInput) -> str:
    """Preview or create/update one record selected by an exact lookup domain."""
    return await _dispatch(
        "odoo_upsert_record",
        params,
        _impl_odoo_upsert_record,
    )


async def _impl_odoo_upsert_record(params: UpsertInput) -> str:
    try:
        await _validate_mutation(params.model, params.values)
        matches = await client.call(
            params.model,
            "search_read",
            {"domain": params.domain, "fields": ["id"], "limit": 2, "context": {}},
        )
        if len(matches) > 1:
            raise OdooAPIError("Upsert domain matched multiple records; refine it before saving.")
        if not matches:
            create_params = CreateInput(
                model=params.model,
                values=params.values,
                return_fields=params.return_fields,
                execute=params.execute,
            )
            return await odoo_create_record(create_params)
        update_params = UpdateInput(
            model=params.model,
            ids=[matches[0]["id"]],
            values=params.values,
            return_fields=params.return_fields,
            execute=params.execute,
        )
        return await odoo_update_records(update_params)
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_archive_records", annotations=DESTRUCTIVE_ACTION)
async def odoo_archive_records(params: ArchiveInput) -> str:
    """Preview or archive/restore records without permanently deleting them."""
    return await _dispatch(
        "odoo_archive_records",
        params,
        _impl_odoo_archive_records,
    )


async def _impl_odoo_archive_records(params: ArchiveInput) -> str:
    try:
        client.require_model(params.model)
        require_mutable_model(params.model, ODOO_MUTABLE_MODELS)
        metadata = await client.call(
            params.model,
            "fields_get",
            {"attributes": ["type", "readonly"]},
        )
        if "active" not in metadata:
            raise OdooAPIError(f"Model '{params.model}' does not support archiving.")
        before = await client.call(
            params.model,
            "read",
            {
                "ids": params.ids,
                "fields": ["id", "display_name", "active"],
                "load": None,
                "context": {"active_test": False},
            },
        )
        if len(before) != len(set(params.ids)):
            raise OdooAPIError("One or more requested records do not exist or are not accessible.")
        operation = "archive" if params.archived else "restore"
        if not params.execute:
            return _json(
                {
                    "executed": False,
                    "operation": operation,
                    "records": before,
                    "next": "Call again with execute=true after explicit user confirmation.",
                }
            )
        method = "action_archive" if params.archived else "action_unarchive"
        await client.call(
            params.model,
            method,
            {"ids": params.ids, "context": SAFE_MUTATION_CONTEXT},
        )
        return _json({"executed": True, "operation": operation, "ids": params.ids})
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_delete_records", annotations=DESTRUCTIVE_ACTION)
async def odoo_delete_records(params: DeleteInput) -> str:
    """Permanently delete explicit records when the user asks and Odoo permits it."""
    return await _dispatch(
        "odoo_delete_records",
        params,
        _impl_odoo_delete_records,
        already_done=_records_absent,
        describe=_records_ref_state,
    )


async def _impl_odoo_delete_records(params: DeleteInput) -> str:
    try:
        client.require_model(params.model)
        can_delete = bool(
            await client.call(
                params.model,
                "check_access_rights",
                {"operation": "unlink", "raise_exception": False},
            )
        )
        if not can_delete:
            raise OdooAPIError(
                f"The Odoo integration user has no delete access on model '{params.model}'."
            )
        before = await _read_records(params.model, params.ids, ["display_name"])
        if len(before) != len(set(params.ids)):
            raise OdooAPIError("One or more requested records do not exist or are not accessible.")
        deleted = await client.call(
            params.model,
            "unlink",
            {"ids": params.ids, "context": SAFE_MUTATION_CONTEXT},
        )
        if deleted is False:
            raise OdooAPIError("Odoo did not confirm deletion of the requested records.")
        return _json(
            {
                "executed": True,
                "operation": "delete",
                "model": params.model,
                "deleted_ids": params.ids,
                "deleted_records": before,
                "reason": params.reason,
                "authority": "Odoo ACLs and record rules",
            }
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_log_internal_note", annotations=WRITE_ACTION)
async def odoo_log_internal_note(params: InternalNoteInput) -> str:
    """Log a mention-free internal note immediately after the user asks to save it."""
    return await _dispatch(
        "odoo_log_internal_note",
        params,
        _impl_odoo_log_internal_note,
    )


async def _impl_odoo_log_internal_note(params: InternalNoteInput) -> str:
    try:
        client.require_model(params.model)
        require_mutable_model(params.model, ODOO_MUTABLE_MODELS)
        target = await _read_records(params.model, [params.record_id], ["display_name"])
        if not target:
            raise OdooAPIError("The target record does not exist or is not accessible.")
        message_id = await _post_internal_note(params.model, params.record_id, params.note)
        return _json(
            {
                "executed": True,
                "operation": "internal_note",
                "record_id": params.record_id,
                "message_id": message_id,
                "external_recipients": [],
            }
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_log_document_summary", annotations=WRITE_ACTION)
async def odoo_log_document_summary(params: DocumentSummaryInput) -> str:
    """Save a Hub document summary and source provenance as an internal note immediately."""
    return await _dispatch(
        "odoo_log_document_summary",
        params,
        _impl_odoo_log_document_summary,
    )


async def _impl_odoo_log_document_summary(params: DocumentSummaryInput) -> str:
    try:
        parts = [f"MCP Hub document: {params.source_name}"]
        if params.source_url:
            parts.append(f"Source: {params.source_url}")
        parts.extend(["", "Summary:", params.summary])
        if params.extracted_fields:
            parts.extend(
                [
                    "",
                    "Extracted fields:",
                    json.dumps(params.extracted_fields, ensure_ascii=False, indent=2, default=str),
                ]
            )
        note = "\n".join(parts)
        if len(note) > 20000:
            raise OdooAPIError(
                "The document log exceeds 20,000 characters; store a shorter summary "
                "or use odoo_attach_text for the longer derived content."
            )
        note_params = InternalNoteInput(
            model=params.model,
            record_id=params.record_id,
            note=note,
        )
        return await odoo_log_internal_note(note_params)
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_attach_file", annotations=WRITE_ACTION)
async def odoo_attach_file(params: FileAttachmentInput) -> str:
    """Preview or attach original PDF bytes as a private record paperclip. Generic ir.attachment writes may remain false.

    Supply strict content_base64, model, record_id and filename; execute defaults to false.
    Execution creates once and verifies the bytes and linkage by readback. An uncertain
    create or failed readback must be reconciled before retrying to avoid duplicates.
    """
    return await _dispatch(
        "odoo_attach_file",
        params,
        _impl_odoo_attach_file,
        already_done=_attachment_already_created,
        describe=_attachment_ref_state,
    )


async def _impl_odoo_attach_file(params: FileAttachmentInput) -> str:
    try:
        return _json(await attach_pdf(
            client, ODOO_MUTABLE_MODELS, params,
            enabled=ODOO_PDF_ATTACHMENTS_ENABLED, max_bytes=ODOO_PDF_MAX_BYTES,
        ))
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_attach_text", annotations=WRITE_ACTION)
async def odoo_attach_text(params: TextAttachmentInput) -> str:
    """Attach Hub-derived text to an Odoo record without sending it externally."""
    return await _dispatch(
        "odoo_attach_text",
        params,
        _impl_odoo_attach_text,
        already_done=_attachment_already_created,
        describe=_attachment_ref_state,
    )


async def _impl_odoo_attach_text(params: TextAttachmentInput) -> str:
    try:
        client.require_model(params.model)
        require_mutable_model(params.model, ODOO_MUTABLE_MODELS)
        target = await _read_records(params.model, [params.record_id], ["display_name"])
        if not target:
            raise OdooAPIError("The target record does not exist or is not accessible.")
        if not params.execute:
            return _json(
                {
                    "executed": False,
                    "operation": "attach_text",
                    "record": target[0],
                    "filename": params.filename,
                    "mimetype": params.mimetype,
                    "bytes": len(params.content.encode("utf-8")),
                    "next": "If the person already asked to attach this, call again with execute=true now.",
                }
            )
        attachment = await client.call(
            "ir.attachment",
            "create",
            {
                "vals_list": {
                    "name": params.filename,
                    "type": "binary",
                    "datas": base64.b64encode(params.content.encode("utf-8")).decode("ascii"),
                    "mimetype": params.mimetype,
                    "description": params.description,
                    "res_model": params.model,
                    "res_id": params.record_id,
                },
                "context": SAFE_MUTATION_CONTEXT,
            },
        )
        attachment_ids = _normalise_created_ids(attachment)
        return _json(
            {
                "executed": True,
                "operation": "attach_text",
                "record_id": params.record_id,
                "attachment_ids": attachment_ids,
            }
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_schedule_activity", annotations=WRITE_ACTION)
async def odoo_schedule_activity(params: ScheduleActivityInput) -> str:
    """Preview or schedule an activity for an internal Odoo user only."""
    return await _dispatch(
        "odoo_schedule_activity",
        params,
        _impl_odoo_schedule_activity,
    )


async def _impl_odoo_schedule_activity(params: ScheduleActivityInput) -> str:
    try:
        client.require_model(params.model)
        require_mutable_model(params.model, ODOO_MUTABLE_MODELS)
        target = await _read_records(params.model, [params.record_id], ["display_name"])
        if not target:
            raise OdooAPIError("The target record does not exist or is not accessible.")
        user_id = params.user_id
        if user_id is None:
            context = await client.call("res.users", "context_get", {}, enforce_allowlist=False)
            user_id = context.get("uid")
        users = await client.call(
            "res.users",
            "search_read",
            {
                "domain": [["id", "=", user_id], ["active", "=", True], ["share", "=", False]],
                "fields": ["id", "name"],
                "limit": 1,
            },
            enforce_allowlist=False,
        )
        if not users:
            raise OdooAPIError("Activities may only be assigned to an active internal Odoo user.")
        preview = {
            "executed": False,
            "operation": "schedule_activity",
            "record": target[0],
            "assigned_user": users[0],
            "summary": params.summary,
            "due_date": params.due_date,
            "external_recipients": [],
        }
        if not params.execute:
            preview["next"] = "If the person already asked to schedule this, call again with execute=true now."
            return _json(preview)
        payload: dict[str, Any] = {
            "ids": [params.record_id],
            "act_type_xmlid": params.activity_type_xmlid,
            "summary": params.summary,
            "note": params.note,
            "user_id": user_id,
            "context": {
                "mail_create_nosubscribe": True,
                "mail_notify_force_send": False,
            },
        }
        if params.due_date:
            payload["date_deadline"] = params.due_date
        activity = await client.call(params.model, "activity_schedule", payload)
        return _json(
            {
                **preview,
                "executed": True,
                "activity_ids": _normalise_created_ids(activity),
            }
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool(name="odoo_run_workflow", annotations=DESTRUCTIVE_ACTION)
async def odoo_run_workflow(params: WorkflowInput) -> str:
    """Preview or run a named internal CRM, sales, or accounting transition."""
    return await _dispatch(
        "odoo_run_workflow",
        params,
        _impl_odoo_run_workflow,
    )


async def _impl_odoo_run_workflow(params: WorkflowInput) -> str:
    try:
        workflow = WORKFLOW_ACTIONS.get(params.action)
        if not workflow:
            raise OdooAPIError(
                f"Unknown workflow '{params.action}'. Allowed workflows: "
                f"{', '.join(sorted(WORKFLOW_ACTIONS))}"
            )
        model = workflow["model"]
        client.require_model(model)
        require_mutable_model(model, ODOO_MUTABLE_MODELS)
        before = await _read_records(model, params.ids, workflow["preview_fields"])
        if len(before) != len(set(params.ids)):
            raise OdooAPIError("One or more workflow records do not exist or are not accessible.")
        if model == "account.move":
            pending = [
                record["id"]
                for record in before
                if record.get("sending_data")
                or record.get("peppol_move_state") in {"to_send", "processing", "done"}
            ]
            if pending:
                raise OdooAPIError(
                    "Workflow blocked because records have pending or completed external "
                    f"delivery state: {pending}"
                )
        preview = {
            "executed": False,
            "operation": "workflow",
            "action": params.action,
            "model": model,
            "before": before,
            "external_communication": "disabled",
        }
        if not params.execute:
            preview["next"] = "Call again with execute=true after explicit user confirmation."
            return _json(preview)
        await client.call(
            model,
            workflow["method"],
            {"ids": params.ids, "context": SAFE_MUTATION_CONTEXT},
        )
        return _json(
            {
                **preview,
                "executed": True,
                "after": await _read_records(model, params.ids, workflow["preview_fields"]),
            }
        )
    except Exception as exc:
        return _error(exc)



