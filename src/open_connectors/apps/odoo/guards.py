"""Shared mutation guardrails for every organization Odoo MCP.

Odoo write guards, carried over from an earlier private implementation, unchanged apart
from its import path. Named ``guards`` so it is not confused with the
per-call ``open_connectors.policy``.
"""

from __future__ import annotations

from typing import Any

from open_connectors.apps.odoo.client import OdooAPIError


SAFE_MUTATION_CONTEXT: dict[str, Any] = {
    # Prevent routine MCP saves from creating follower subscriptions, chatter
    # tracking messages, or email notifications as side effects.
    "tracking_disable": True,
    "mail_create_nosubscribe": True,
    "mail_create_nolog": True,
    "mail_notrack": True,
    "mail_notify_force_send": False,
    "send_email": False,
}

BLOCKED_MODELS = {
    "base.automation",
    "calendar.event",
    "ir.actions.server",
    "ir.cron",
    "mail.compose.message",
    "mail.followers",
    "mail.mail",
    "mail.message",
    "mail.template",
    "mailing.mailing",
    "res.groups",
    "res.users",
    "sms.sms",
    "sms.template",
}

BLOCKED_MODEL_PREFIXES = (
    "account.edi",
    "auth.",
    "base.",
    "bus.",
    "discuss.",
    "iap.",
    "ir.",
    "mail.",
    "mailing.",
    "payment.",
    "social.",
    "whatsapp.",
)

BLOCKED_FIELDS = {
    "access_token",
    "active",
    "message_follower_ids",
    "message_ids",
    "message_partner_ids",
    "partner_ids",
    "peppol_message_uuid",
    "sending_data",
    "signature",
    "signup_expiration",
    "signup_token",
    "signup_type",
    "state",
    "website_published",
}

BLOCKED_FIELD_PREFIXES = (
    "activity_",
    "message_",
)

ALLOWED_RELATION_COMMANDS = {0, 1, 4, 5, 6}


def require_mutable_model(model: str, mutable_models: set[str]) -> None:
    """Reject communication, system, or non-configured mutation targets."""
    if model in BLOCKED_MODELS or model.startswith(BLOCKED_MODEL_PREFIXES):
        raise OdooAPIError(f"Model '{model}' is blocked from MCP mutations.")
    if "*" not in mutable_models and model not in mutable_models:
        allowed = ", ".join(sorted(mutable_models))
        raise OdooAPIError(f"Model '{model}' is not mutable. Mutable models: {allowed}")


def validate_values(values: dict[str, Any]) -> None:
    """Reject fields and relational commands that can bypass safe tools."""
    if not values:
        raise OdooAPIError("At least one field value is required.")

    for field_name, value in values.items():
        if not field_name or not field_name.replace("_", "").isalnum():
            raise OdooAPIError(f"Invalid Odoo field name '{field_name}'.")
        if field_name in BLOCKED_FIELDS or field_name.startswith(BLOCKED_FIELD_PREFIXES):
            raise OdooAPIError(
                f"Field '{field_name}' is blocked from generic saves; use a dedicated safe tool."
            )
        _validate_relation_commands(field_name, value)


def _validate_relation_commands(field_name: str, value: Any) -> None:
    if not isinstance(value, list):
        return

    for item in value:
        if not isinstance(item, (list, tuple)) or not item:
            continue
        opcode = item[0]
        if not isinstance(opcode, int):
            continue
        if opcode not in ALLOWED_RELATION_COMMANDS:
            raise OdooAPIError(
                f"Field '{field_name}' uses blocked relation command {opcode}; "
                "record deletion and implicit unlinking are not exposed."
            )
        if opcode in {0, 1} and len(item) >= 3 and isinstance(item[2], dict):
            validate_values(item[2])
