"""Remind the person, through their assistant, before their key ends.

In the last ``WINDOW_DAYS`` days of a key (and after it ended) every tool
result carries one extra short line for the assistant to pass on.
"""

from __future__ import annotations

import time
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from open_connectors import identity
from open_connectors.pages.flow import human_date

WINDOW_DAYS = 7

HOW_TO = {
    "odoo": "make a new key in Odoo (your picture › My Preferences › Security › API Keys › Add) "
            "and connect Odoo again in your assistant",
}


def reminder_text(app: str, secret: dict[str, Any], now: float | None = None) -> str | None:
    expires_at = secret.get("expires_at")
    if not isinstance(expires_at, int):
        return None
    current = time.time() if now is None else now
    left = expires_at - current
    if left > WINDOW_DAYS * 86400:
        return None
    how = HOW_TO.get(app, "connect the app again in your assistant")
    if left <= 0:
        return f"Please tell the person: their {app.capitalize()} key has ended. To keep working, {how}."
    days = max(1, int(left // 86400))
    when = human_date(expires_at, "en")
    estimated = " (estimated)" if secret.get("expiry_source") != "odoo" else ""
    return (f"Please tell the person: their {app.capitalize()} key ends on {when}{estimated}, in {days} "
            f"day{'s' if days != 1 else ''}. To keep working, {how}.")


class KeyExpiryReminder(Middleware):
    def __init__(self, app: str):
        self.app = app

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        result = await call_next(context)
        try:
            text = reminder_text(self.app, identity.current_connection().secret)
        except identity.IdentityRefused:
            return result
        if not text or not isinstance(result, ToolResult):
            return result
        return ToolResult(content=[*result.content, TextContent(type="text", text=text)],
                          structured_content=result.structured_content, meta=getattr(result, "meta", None))
