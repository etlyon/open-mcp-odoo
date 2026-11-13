"""Create-only, private PDF paperclips under the calling account's Odoo identity.

Odoo attachment tools, carried over from an earlier private implementation, unchanged
apart from import paths.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Any, Literal
import unicodedata
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from open_connectors.journal import IdempotencyKey
from open_connectors.apps.odoo.client import OdooAPIError, OdooClient
from open_connectors.apps.odoo.guards import SAFE_MUTATION_CONTEXT, require_mutable_model
from open_connectors.apps.odoo.pdf_validation import PDF_MAX_BYTES, validate_pdf


class FileAttachmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    idempotency_key: IdempotencyKey = None
    model: str = Field(min_length=1, max_length=200, description="Allowed destination business model")
    record_id: int = Field(gt=0, strict=True)
    filename: str = Field(min_length=5, max_length=255, description="PDF filename without a path")
    content_base64: str = Field(
        min_length=1, max_length=4 * ((PDF_MAX_BYTES + 2) // 3),
        description="Strict base64 of original PDF bytes; no data URL or extracted text",
    )
    mimetype: Literal["application/pdf"] = "application/pdf"
    description: str | None = Field(default=None, max_length=2000)
    execute: bool = Field(default=False, description="False validates/previews; true creates one private attachment")

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        value = unicodedata.normalize("NFC", value)
        if (value != value.strip() or not value.lower().endswith(".pdf")
                or value.startswith(".") or len(value.encode("utf-8")) > 255
                or any(c in "/\\:" or unicodedata.category(c).startswith("C") for c in value)):
            raise ValueError("Use a plain PDF filename without paths or control characters.")
        return value


def decode_content(value: str, max_bytes: int) -> bytes:
    if len(value) > 4 * ((max_bytes + 2) // 3):
        raise OdooAPIError(f"PDF exceeds the {max_bytes}-byte limit.")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise OdooAPIError("content_base64 must be strict base64 of the original PDF.") from None
    if not raw or len(raw) > max_bytes:
        raise OdooAPIError(f"PDF must contain 1 to {max_bytes} decoded bytes.")
    if base64.b64encode(raw).decode("ascii") != value:
        raise OdooAPIError("content_base64 must use canonical base64 encoding.")
    return raw


def capability(enabled: bool, mutable_models: set[str], max_bytes: int) -> dict:
    return {
        "tool": "odoo_attach_file", "enabled": enabled and bool(mutable_models),
        "mimetypes": ["application/pdf"], "max_decoded_bytes": max_bytes,
        "preview_default": True, "create_only": True, "public": False,
        "policy_note": "Separate from generic ir.attachment writes; destination policy and Odoo record permissions apply.",
        "validation": "Bounded strict PDF structure; encrypted/malformed PDFs rejected; no malware scan or rendering.",
    }


async def attach_pdf(client: OdooClient, mutable_models: set[str], params: FileAttachmentInput,
                     *, enabled: bool, max_bytes: int) -> dict:
    if not enabled:
        raise OdooAPIError("PDF attachments are disabled for this service.")
    client.require_model(params.model)
    client.require_model("ir.attachment")
    require_mutable_model(params.model, mutable_models)
    raw = decode_content(params.content_base64, max_bytes)
    validation = await validate_pdf(raw)
    digest = hashlib.sha256(raw).hexdigest()
    target = await client.call(params.model, "read", {
        "ids": [params.record_id], "fields": ["id", "display_name"], "load": None,
    })
    if len(target) != 1 or target[0].get("id") != params.record_id:
        raise OdooAPIError("The destination does not exist or is not accessible.")
    # Odoo 19 checks both model ACL and record rules, using the same API user.
    if not await client.call(params.model, "has_access", {"ids": [params.record_id], "operation": "write"}):
        raise OdooAPIError("The Odoo user has no write access to the destination record.")
    if not await client.call("ir.attachment", "check_access_rights", {
        "operation": "create", "raise_exception": False,
    }):
        raise OdooAPIError("The Odoo user has no attachment create access.")
    context = await client.call("res.users", "context_get", {}, enforce_allowlist=False)
    uid = context.get("uid")
    if type(uid) is not int or uid <= 0:
        raise OdooAPIError("Could not establish the effective Odoo user.")
    users = await client.call("res.users", "read", {
        "ids": [uid], "fields": ["id", "name", "login", "active"], "load": None,
    }, enforce_allowlist=False)
    if len(users) != 1 or users[0].get("id") != uid or not users[0].get("active"):
        raise OdooAPIError("The effective Odoo user is not active.")
    receipt = {
        "executed": False, "operation": "attach_file", "model": params.model,
        "record_id": params.record_id, "record": target[0], "acting_user": users[0],
        "filename": params.filename, "mimetype": "application/pdf", "public": False,
        "bytes": len(raw), "sha256": digest, "max_decoded_bytes": max_bytes,
        **validation,
    }
    if not params.execute:
        return {**receipt, "next": "Call once with execute=true when the user has authorized this attachment."}
    receipt["operation_id"] = str(uuid.uuid4())
    values = {
        "name": params.filename, "type": "binary", "datas": params.content_base64,
        "mimetype": "application/pdf", "res_model": params.model,
        "res_id": params.record_id, "public": False,
    }
    if params.description is not None:
        values["description"] = params.description
    # No transport retries or durable idempotency facility exist in this service.
    # Once create is attempted, errors must not imply that no record was saved.
    try:
        created = await client.call("ir.attachment", "create", {
            "vals_list": values, "context": SAFE_MUTATION_CONTEXT,
        })
    except Exception as exc:
        return {**receipt, "executed": None, "status": "create_outcome_unknown",
                "verified": False, "retry_safe": False, "error_type": type(exc).__name__,
                "next": "Do not retry automatically. Reconcile attachments by record, filename, creator and SHA-256 first."}
    receipt["executed"] = True
    ids = created.get("ids") if isinstance(created, dict) else created
    if type(ids) is int:
        ids = [ids]
    if not isinstance(ids, list) or len(ids) != 1 or type(ids[0]) is not int or ids[0] <= 0:
        return {**receipt, "status": "created_identifier_unknown", "verified": False,
                "retry_safe": False, "next": "Odoo returned success. Reconcile before any retry."}
    receipt["attachment_id"] = ids[0]
    receipt["attachment_ids"] = ids
    try:
        rows = await client.call("ir.attachment", "read", {
            "ids": ids, "fields": ["id", "name", "type", "res_model", "res_id", "public",
                                    "mimetype", "file_size", "datas", "create_uid"],
            "load": None, "context": {"bin_size": False, "bin_size_datas": False},
        })
        if len(rows) != 1:
            raise ValueError("Missing attachment")
        row = rows[0]
        returned = decode_content(row["datas"], max_bytes)
        returned_hash = hashlib.sha256(returned).hexdigest()
        author = row.get("create_uid")
        if isinstance(author, list):
            author = author[0]
        checks = {
            "id": row.get("id") == ids[0], "filename": row.get("name") == params.filename,
            "type": row.get("type") == "binary", "mimetype": row.get("mimetype") == "application/pdf",
            "linkage": row.get("res_model") == params.model and row.get("res_id") == params.record_id,
            "private": row.get("public") is False, "creator": author == uid,
            "size": row.get("file_size") == len(raw) == len(returned),
            "sha256": returned_hash == digest,
        }
        verified = all(checks.values())
        return {**receipt, "status": "created_verified" if verified else "created_verification_failed",
                "verified": verified, "checks": checks, "readback_sha256": returned_hash,
                "retry_safe": False,
                "next": "Open the record's paperclip in Odoo." if verified else "Inspect this attachment ID; do not create another copy."}
    except Exception as exc:
        return {**receipt, "status": "created_readback_failed", "verified": False,
                "retry_safe": False, "error_type": type(exc).__name__,
                "next": "Creation succeeded. Inspect the returned attachment ID; do not create another copy."}


def _stored_bytes(params: Any) -> bytes | None:
    """The bytes this call would store, or None when the input is not decodable.

    Returning None leaves an invalid request to the tool body, so its own
    validation error reaches the caller unchanged.
    """
    content_base64 = getattr(params, "content_base64", None)
    if content_base64 is not None:
        try:
            return base64.b64decode(content_base64, validate=True)
        except Exception:
            return None
    content = getattr(params, "content", None)
    return content.encode("utf-8") if isinstance(content, str) else None


async def attachment_matches(client: OdooClient, params: Any) -> list[dict]:
    """Read-only search for an attachment this call would duplicate.

    Odoo maintains `ir.attachment.checksum` (sha1 of the stored bytes) itself, so
    it is an external reference this service already has for free. The search is
    a read: no ir.model.data is written, no ir.* mutation exception is opened and
    generic ir.attachment writes stay blocked exactly as before.
    """
    raw = _stored_bytes(params)
    if raw is None:
        return []
    # Odoo's published duplicate-lookup checksum, never a signature or password hash.
    digest = hashlib.sha1(raw, usedforsecurity=False).hexdigest()  # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1
    return await client.call(
        "ir.attachment",
        "search_read",
        {
            "domain": [
                ["res_model", "=", params.model],
                ["res_id", "=", params.record_id],
                ["name", "=", params.filename],
                ["checksum", "=", digest],
            ],
            "fields": [
                "id", "name", "res_model", "res_id", "checksum",
                "file_size", "public", "create_uid",
            ],
            "limit": 2,
            "context": {},
        },
    )


def attachment_receipt(params: Any, row: dict) -> dict:
    """Receipt for an attachment that already exists, so nothing is created."""
    author = row.get("create_uid")
    if isinstance(author, list):
        author = author[0]
    return {
        "executed": True,
        "operation": "attach_file" if hasattr(params, "content_base64") else "attach_text",
        "status": "already_attached",
        "already_attached": True,
        "created": False,
        "verified": True,
        "model": params.model,
        "record_id": params.record_id,
        "filename": params.filename,
        "attachment_id": row.get("id"),
        "attachment_ids": [row.get("id")],
        "checksum_sha1": row.get("checksum"),
        "bytes": row.get("file_size"),
        "public": row.get("public"),
        "create_uid": author,
        "retry_safe": False,
        "next": (
            "An identical attachment is already linked to this record; nothing was created."
        ),
    }
