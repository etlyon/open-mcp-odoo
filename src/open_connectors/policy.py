"""Per-account tool policy (Phase 2): allowed, ask or denied, per tool.

Visibility first, as in Phase 1: until an account has a stored credential for
this app it sees only the app's connect tool; once it has one, the connect tool
is gone. Then ``ACCOUNTS_DIR/<account>/policy.yaml`` decides each remaining
tool. The file holds up to three lists of tool names, ``deny``, ``ask`` and
``allow``, and the decision is, in this order:

1. named in ``deny``        -> denied: hidden from ``tools/list``, refused on call;
2. named in ``ask``         -> ask;
3. named in ``allow``       -> allowed;
4. the app's connect tool   -> allowed;
5. ``read_only_hint`` true  -> allowed (``readOnlyHint`` on the wire);
6. ``destructive_hint`` false -> allowed (undoable writes: create/update/file; D254);
7. ``destructive_hint`` true  -> ask (delete, send, share);
8. anything else            -> ask.

Only the operator writes that file; no tool can. A file that cannot be read
exactly (an unknown key, a list that is not a list of names, a name given
twice) refuses every tool (fail closed).

*Ask* is enforced here, by the server, whatever the client shows. A call to an
ask tool runs only after one of, in order:

(a) a valid, unused ``confirm_token`` for (account, tool, arguments);
(b) the user's answer to a form elicitation the request declares it supports:
    on 2026-07-28 an ``InputRequiredResult`` with one boolean ``confirm``
    request (the framework seals ``request_state``); on a handshake connection
    ``ctx.elicit``. A refusal ends the call; a failure to ask falls to (c);
(c) otherwise nothing runs, and the result is ``status: confirmation_required``
    with a single-use token to repeat the same call with.

``tools/list`` advertises the decision in each tool's ``meta``
(``open-connectors/decision``, plus ``anthropic/requiresUserInteraction`` for ask) and
adds the optional ``confirm_token`` argument, which is removed again before the
tool body validates its arguments. Each ask writes one log line naming the
tool, the channel and the outcome; never an argument, an account or a token.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mcp_types
import yaml
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import AcceptedElicitation
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools import InputRequiredToolResult, ToolResult
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from open_connectors import identity, journal


class StoreError(RuntimeError):
    """Kept for the policy file reader's error type."""


LOGGER = logging.getLogger("open_connectors.policy")

POLICY_LISTS = ("deny", "ask", "allow")

ALLOWED = "allowed"
ASK = "ask"
DENIED = "denied"

CONFIRM_TOKEN = "confirm_token"
CONFIRM_REQUEST = "confirm"
TOKEN_TTL_SECONDS = 300
LEGACY_ELICITATION_TIMEOUT_SECONDS = 15.0
SUMMARY_MAX_CHARS = 2000
DECISION_META = "open-connectors/decision"
INTERACTION_META = "anthropic/requiresUserInteraction"

CONFIRM_TOKEN_SCHEMA: dict[str, Any] = {
    "type": "string",
    "description": (
        "Only when repeating a call whose result was status confirmation_required, after the user "
        "agreed: the confirm_token from that result. It works once, for exactly that call."
    ),
}

_TOKEN_PATTERN = re.compile(r"[0-9]{1,20}\.[0-9a-f]{64}")
_MICROSECONDS = 1_000_000
_FUTURE_SKEW_SECONDS = 60


def _confirmation_arguments(tool: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Bind consent to the action, excluding declared transport retry keys.

    A confirming call is a new logical call and may have a newly generated
    idempotency key. Free-form business data is left intact: only properties
    explicitly declared by the tool schema are considered bookkeeping.
    """
    root = tool.parameters or {}
    result = copy.deepcopy(arguments)

    def walk(schema: Any, value: Any, depth: int = 0) -> None:
        if depth > 32 or not isinstance(schema, dict) or not isinstance(value, dict):
            return
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            resolved: Any = root
            for key in ref[2:].split("/"):
                resolved = resolved.get(key.replace("~1", "/").replace("~0", "~")) if isinstance(resolved, dict) else None
            walk(resolved, value, depth + 1)
        for part in schema.get("allOf", []):
            walk(part, value, depth + 1)
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return
        for name, child in properties.items():
            if name == "idempotency_key" and isinstance(child, dict) and child.get("x-ebot-bookkeeping") == "idempotency":
                value.pop(name, None)
            elif name in value:
                walk(child, value[name], depth + 1)

    walk(root, result)
    return result


class PolicyError(StoreError):
    """The account's policy file cannot be applied; every tool is refused."""


def _now() -> float:
    return time.time()


class _PolicyLoader(yaml.SafeLoader):
    """SafeLoader that refuses a mapping key given twice instead of keeping the last one."""


def _construct_mapping(loader: _PolicyLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            repeated = key in seen
            seen.add(key)
        except TypeError:
            raise PolicyError("policy.yaml for this account has an unknown shape; every tool is refused.") from None
        if repeated:
            raise PolicyError("policy.yaml for this account repeats a key; every tool is refused.")
    return loader.construct_mapping(node, deep=True)


_PolicyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


@dataclass(frozen=True)
class Policy:
    deny: frozenset[str] = frozenset()
    ask: frozenset[str] = frozenset()
    allow: frozenset[str] = frozenset()

    def decide(self, tool: str, annotations: Any, connect_tool: str) -> str:
        """``allowed``, ``ask`` or ``denied`` for one tool of this account."""
        if tool in self.deny:
            return DENIED
        if tool in self.ask:
            return ASK
        if tool in self.allow:
            return ALLOWED
        if tool == connect_tool:
            return ALLOWED
        if getattr(annotations, "read_only_hint", None) is True:
            return ALLOWED
        # D254: when the person asked, undoable writes run; money/delete/outside-send stay ask.
        if getattr(annotations, "destructive_hint", None) is False:
            return ALLOWED  # DEFAULT-ALLOW-WRITE
        return ASK  # DEFAULT-ASK


def load_policy(path: Path) -> Policy:
    """Read one account's ``policy.yaml``; a missing or empty file is the defaults."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Policy()
    except (OSError, UnicodeError):
        raise PolicyError("policy.yaml for this account cannot be read; every tool is refused.") from None
    try:
        data = yaml.load(text, Loader=_PolicyLoader)
    except yaml.YAMLError:
        raise PolicyError("policy.yaml for this account is not valid YAML; every tool is refused.") from None
    if data is None:
        return Policy()
    if not isinstance(data, dict) or any(key not in POLICY_LISTS for key in data):
        raise PolicyError(
            "policy.yaml for this account has an unknown shape (only the lists 'deny', 'ask' and 'allow' "
            "of tool names are read); every tool is refused."
        )
    lists: dict[str, frozenset[str]] = {}
    seen: set[str] = set()
    for key in POLICY_LISTS:
        names = data.get(key)
        if names is None:
            names = []
        if not isinstance(names, list) or not all(isinstance(name, str) and name for name in names):
            raise PolicyError(f"policy.yaml '{key}' must be a list of tool names; every tool is refused.")
        for name in names:
            if name in seen:
                raise PolicyError(f"policy.yaml names '{name}' more than once; every tool is refused.")
            seen.add(name)
        lists[key] = frozenset(names)
    return Policy(**lists)


class ConfirmationTokens:
    """Single-use confirmations for one process.

    A token is ``<issued_ts>.<hex HMAC-SHA256(key, account\\ntool\\nfingerprint\\nissued_ts)>``
    with ``issued_ts`` in microseconds and a key from ``os.urandom(32)`` at
    start, valid for 300 seconds and once. Each issuance is distinct, so a
    second confirmation of the same call is never mistaken for the used first
    one. A restart invalidates every open confirmation; the caller is asked again.
    """

    def __init__(self, key: bytes | None = None, ttl: int = TOKEN_TTL_SECONDS) -> None:
        self._key = key if key is not None else os.urandom(32)
        self.ttl = ttl
        self._used: dict[str, int] = {}
        self._last_issued = 0
        self._lock = threading.Lock()

    def _mac(self, account: str, tool: str, fingerprint: str, issued: int) -> str:
        message = f"{account}\n{tool}\n{fingerprint}\n{issued}".encode("utf-8", "surrogatepass")
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def issue(self, account: str, tool: str, fingerprint: str) -> str:
        with self._lock:
            issued = int(_now() * _MICROSECONDS)
            if issued <= self._last_issued < issued + _MICROSECONDS:
                issued = self._last_issued + 1  # two issuances in one instant stay distinct
            self._last_issued = issued
        return f"{issued}.{self._mac(account, tool, fingerprint, issued)}"

    def redeem(self, token: object, account: str, tool: str, fingerprint: str) -> str | None:
        """Consume ``token`` if it confirms exactly this call; otherwise return why it is refused."""
        if not isinstance(token, str) or not _TOKEN_PATTERN.fullmatch(token):
            return "malformed"
        issued_text, _, mac = token.partition(".")
        issued = int(issued_text)
        if not hmac.compare_digest(mac, self._mac(account, tool, fingerprint, issued)):
            return "not issued for this account, tool and arguments"
        now = int(_now() * _MICROSECONDS)
        if now - issued > self.ttl * _MICROSECONDS:
            return "expired"
        if issued > now + _FUTURE_SKEW_SECONDS * _MICROSECONDS:
            return "not valid yet"
        with self._lock:
            for used, expiry in list(self._used.items()):
                if expiry < now:
                    del self._used[used]
            if token in self._used:
                return "already used"
            self._used[token] = issued + self.ttl * _MICROSECONDS
        return None


class AccountToolPolicy(Middleware):
    def __init__(
        self,
        *,
        app: str,
        connect_tool: str,
        tokens: ConfirmationTokens | None = None,
        annotations: Callable[[str], Any] | None = None,
        preview_tools: frozenset[str] = frozenset(),
        modern_token_only: bool = False,
        mandatory_ask_tools: frozenset[str] = frozenset(),
        summary: Callable[[str, dict[str, Any]], Awaitable[str | None]] | None = None,
    ) -> None:
        self.app = app
        self.connect_tool = connect_tool
        self.tokens = tokens if tokens is not None else ConfirmationTokens()
        self.preview_tools = preview_tools
        self.modern_token_only = modern_token_only
        self.mandatory_ask_tools = mandatory_ask_tools
        # Optional: what an ask shows the person, per tool, from the call's arguments (kSuite: who gets
        # the mail and what it says). None, or a summary of None, keeps the generic question.
        self.summary = summary
        # Optional: annotations by tool name, for an app whose tools declare none
        # (the kSuite door proxies vendor servers). A name it does not know keeps
        # the tool's own annotations.
        self.annotations = annotations

    def _annotations(self, tool: Any) -> Any:
        if self.annotations is not None:
            override = self.annotations(tool.name)
            if override is not None:
                return override
        return tool.annotations

    def _state(self, account: str) -> tuple[bool, Policy]:
        # A call reaches the tools only through the gate, with a live connection:
        # always connected; the safe defaults from the tool annotations decide.
        return True, Policy()

    def _decision(self, policy: Policy, tool: str, annotations: Any) -> str:
        """Apply an adapter's non-bypassable approval boundary after deny.

        A normal account policy may explicitly allow a destructive tool. Some
        business adapters have a narrower invariant: money, submit/cancel and
        deletion always ask, even if an operator accidentally lists them under
        ``allow``. ``deny`` remains stronger than this gate.
        """
        if tool in policy.deny:
            return DENIED
        if tool in self.mandatory_ask_tools:
            return ASK
        return policy.decide(tool, annotations, self.connect_tool)

    def _refusal(self, tool: str, connected: bool, policy: Policy) -> str | None:
        if tool in policy.deny:
            return f"Tool '{tool}' is not allowed for this account."
        if tool == self.connect_tool and connected:
            return f"A {self.app} credential is already connected for this account; {tool} is not offered."
        if tool != self.connect_tool and not connected:
            return f"No {self.app} credential is connected for this account yet; call {self.connect_tool} first."
        return None

    async def on_list_tools(self, context: MiddlewareContext[Any], call_next: Any) -> Sequence[Any]:
        tools = await call_next(context)
        try:
            connected, policy = self._state(identity.current_account())
        except (identity.IdentityRefused, StoreError):
            return []
        return [
            _advertise(tool, self._decision(policy, tool.name, self._annotations(tool)), self._annotations(tool))
            for tool in tools
            if self._refusal(tool.name, connected, policy) is None
        ]

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        name = context.message.name
        arguments = context.message.arguments
        # Removed before anything else: the tool body never sees it, so neither
        # its validation nor its logging can.
        token = arguments.pop(CONFIRM_TOKEN, None) if arguments is not None else None
        try:
            account = identity.current_account()
            connected, policy = self._state(account)
        except (identity.IdentityRefused, StoreError) as exc:
            raise ToolError(str(exc)) from None
        refusal = self._refusal(name, connected, policy)
        if refusal is not None:
            raise ToolError(refusal)
        ctx = context.fastmcp_context
        if ctx is None:
            raise ToolError("This call carries no request context; access refused.")
        tool = await ctx.fastmcp.get_tool(name)
        if tool is None:
            raise ToolError(f"Unknown tool: {name!r}")
        decision = self._decision(policy, name, self._annotations(tool))
        if decision == ALLOWED:
            return await call_next(context)
        if decision != ASK:
            raise ToolError(f"Tool '{name}' is not allowed for this account.")
        if _is_preview(name, arguments, self.preview_tools):
            _log_ask(
                name,
                "preview",
                "allowed",
                protocol=_protocol(ctx),
                capability=_elicitation_capability(ctx),
            )
            return await call_next(context)
        fingerprint = journal.fingerprint(_confirmation_arguments(tool, dict(arguments or {})))
        return await self._ask(context, call_next, ctx, tool, account, fingerprint, token, dict(arguments or {}))

    async def _describe(self, tool: str, arguments: dict[str, Any]) -> str | None:
        """The app's summary of this call for the person, or None (any failure keeps the generic question)."""
        if self.summary is None:
            return None
        try:
            text = await self.summary(tool, arguments)
        except Exception:
            return None
        return text[:SUMMARY_MAX_CHARS] if isinstance(text, str) and text.strip() else None

    async def _ask(
        self,
        context: MiddlewareContext[Any],
        call_next: Any,
        ctx: Any,
        tool: Any,
        account: str,
        fingerprint: str,
        token: object,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        name = tool.name
        protocol = _protocol(ctx)
        capability = _elicitation_capability(ctx)
        # (a) a confirmation the caller already holds
        if token is not None:
            reason = self.tokens.redeem(token, account, name, fingerprint)
            if reason is not None:
                _log_ask(name, "token", "refused", protocol=protocol, capability=capability)
                raise ToolError(
                    f"confirm_token refused ({reason}); nothing ran. Call {name} again without "
                    "confirm_token to ask for a new confirmation."
                )
            _log_ask(name, "token", "accepted", protocol=protocol, capability=capability)
            return await call_next(context)
        # (b) ask the user through the client, or fall back to a token
        async def describe() -> str | None:
            return await self._describe(name, arguments or {})

        if _is_modern(ctx):
            return await self._ask_modern(
                context, call_next, ctx, tool, account, fingerprint, protocol, capability, describe
            )
        return await self._ask_legacy(
            context, call_next, ctx, tool, account, fingerprint, protocol, capability, describe
        )

    async def _ask_modern(
        self,
        context: MiddlewareContext[Any],
        call_next: Any,
        ctx: Any,
        tool: Any,
        account: str,
        fingerprint: str,
        protocol: str,
        capability: str,
        describe: Callable[[], Awaitable[str | None]] | None = None,
    ) -> Any:
        if self.modern_token_only:
            summary = await describe() if describe else None
            return self._token_result(tool, account, fingerprint, protocol, capability, summary=summary)
        state = _form_state(account, tool.name, fingerprint)
        if ctx.request_state is not None or ctx.input_responses is not None:
            return await _form_answer(
                context,
                call_next,
                ctx,
                tool.name,
                state,
                protocol=protocol,
                capability=capability,
            )
        summary = await describe() if describe else None
        if _declares_form_elicitation(ctx):
            _log_ask(tool.name, "form", "asked", protocol=protocol, capability=capability)
            return _form_request(tool.name, state, summary)
        return self._token_result(tool, account, fingerprint, protocol, capability, summary=summary)

    async def _ask_legacy(
        self,
        context: MiddlewareContext[Any],
        call_next: Any,
        ctx: Any,
        tool: Any,
        account: str,
        fingerprint: str,
        protocol: str,
        capability: str,
        describe: Callable[[], Awaitable[str | None]] | None = None,
    ) -> Any:
        elicit_error: str | None = None
        summary = await describe() if describe else None
        if _declares_form_elicitation(ctx):
            try:
                answer = await asyncio.wait_for(
                    ctx.elicit(_question(tool.name, summary), response_type=bool),
                    timeout=LEGACY_ELICITATION_TIMEOUT_SECONDS,
                )
            except Exception as exc:  # any failure to ask falls back to a token
                elicit_error = type(exc).__name__
            else:
                if isinstance(answer, AcceptedElicitation) and answer.data is True:
                    _log_ask(tool.name, "elicit", "accepted", protocol=protocol, capability=capability)
                    return await call_next(context)
                _log_ask(tool.name, "elicit", "declined", protocol=protocol, capability=capability)
                raise ToolError(f"declined: {tool.name} was not confirmed; nothing ran.")
        return self._token_result(tool, account, fingerprint, protocol, capability, elicit_error, summary=summary)

    def _token_result(
        self,
        tool: Any,
        account: str,
        fingerprint: str,
        protocol: str,
        capability: str,
        elicit_error: str | None = None,
        *,
        summary: str | None = None,
    ) -> ToolResult:
        confirm = self.tokens.issue(account, tool.name, fingerprint)
        _log_ask(
            tool.name,
            "token",
            "issued",
            elicit_error,
            protocol=protocol,
            capability=capability,
        )
        return _confirmation_required(tool, confirm, self.tokens.ttl, summary)


def _advertise(tool: Any, decision: str, annotations: Any = None) -> Any:
    """A copy of ``tool`` with the optional ``confirm_token`` argument and the decision in ``meta``.

    ``annotations``, when given, are the ones the decision was taken from and
    are advertised in place of the tool's own.
    """
    parameters = dict(tool.parameters or {})
    properties = dict(parameters.get("properties") or {})
    properties[CONFIRM_TOKEN] = dict(CONFIRM_TOKEN_SCHEMA)
    parameters["properties"] = properties
    meta = {key: value for key, value in (tool.meta or {}).items() if key != INTERACTION_META}
    meta[DECISION_META] = decision
    if decision == ASK:
        meta[INTERACTION_META] = True
    update: dict[str, Any] = {"parameters": parameters, "meta": meta}
    if annotations is not None:
        update["annotations"] = annotations
    return tool.model_copy(update=update)


def _is_modern(ctx: Any) -> bool:
    request = ctx.request_context
    return request is not None and request.protocol_version in MODERN_PROTOCOL_VERSIONS


def _protocol(ctx: Any) -> str:
    return "modern" if _is_modern(ctx) else "legacy"


def _elicitation_capability(ctx: Any) -> str:
    """A safe capability-shape label; never includes client identity or request data."""
    try:
        capabilities = ctx.session.client_capabilities
    except RuntimeError:
        return "unavailable"
    elicitation = getattr(capabilities, "elicitation", None)
    if elicitation is None:
        return "none"
    form = getattr(elicitation, "form", None) is not None
    url = getattr(elicitation, "url", None) is not None
    if form and url:
        return "form_url"
    if form:
        return "form"
    if url:
        return "url"
    return "legacy_empty"


def _is_preview(name: str, arguments: Any, preview_tools: frozenset[str]) -> bool:
    """True only for an explicitly named preview tool with literal false/default execute."""
    if name not in preview_tools or not isinstance(arguments, dict):
        return False
    params = arguments.get("params")
    if not isinstance(params, dict):
        return False
    return "execute" not in params or params["execute"] is False


def _declares_form_elicitation(ctx: Any) -> bool:
    """True when this request (2026-07-28 ``_meta``) or its session declared form elicitation."""
    try:
        capabilities = ctx.session.client_capabilities
    except RuntimeError:
        return False
    elicitation = getattr(capabilities, "elicitation", None)
    if elicitation is None:
        return False
    # An empty elicitation capability predates the form/url split and means form.
    return elicitation.form is not None or elicitation.url is None


def _question(tool: str, summary: str | None = None) -> str:
    question = f"Allow {tool} to run now, for your account, with the arguments of this call?"
    return f"{summary}\n\n{question}" if summary else question


def _form_state(account: str, tool: str, fingerprint: str) -> str:
    return f"{account}|{tool}|{fingerprint}"


def _form_request(tool: str, state: str, summary: str | None = None) -> InputRequiredToolResult:
    request = mcp_types.ElicitRequest(
        params=mcp_types.ElicitRequestFormParams(
            message=_question(tool, summary),
            requested_schema={
                "type": "object",
                "properties": {
                    CONFIRM_REQUEST: {"type": "boolean", "title": "Confirm", "description": f"Run {tool} now?"},
                },
                "required": [CONFIRM_REQUEST],
            },
        )
    )
    return InputRequiredToolResult(
        mcp_types.InputRequiredResult(input_requests={CONFIRM_REQUEST: request}, request_state=state)
    )


async def _form_answer(
    context: Any,
    call_next: Any,
    ctx: Any,
    tool: str,
    expected: str,
    *,
    protocol: str | None = None,
    capability: str | None = None,
) -> Any:
    """Second leg on 2026-07-28: the state must be this call's, and the answer an explicit yes."""
    state = ctx.request_state
    if not isinstance(state, str) or not hmac.compare_digest(
        state.encode("utf-8", "surrogatepass"), expected.encode("utf-8", "surrogatepass")
    ):
        _log_ask(tool, "form", "refused", protocol=protocol, capability=capability)
        raise ToolError(
            f"The confirmation state does not belong to this call; nothing ran. Call {tool} again to be asked again."
        )
    answer = (ctx.input_responses or {}).get(CONFIRM_REQUEST)
    content = getattr(answer, "content", None)
    if getattr(answer, "action", None) == "accept" and isinstance(content, dict) and content.get(CONFIRM_REQUEST) is True:
        _log_ask(tool, "form", "accepted", protocol=protocol, capability=capability)
        return await call_next(context)
    _log_ask(tool, "form", "declined", protocol=protocol, capability=capability)
    raise ToolError(f"declined: {tool} was not confirmed; nothing ran.")


def _confirmation_required(tool: Any, confirm: str, ttl: int, summary: str | None = None) -> ToolResult:
    payload: dict[str, Any] = {
        "status": "confirmation_required",
        "executed": False,
        "tool": tool.name,
        "confirm_token": confirm,
        "expires_in": ttl,
        "detail": (
            f"{tool.name} asks before it runs for this account, and nothing ran. Ask the user whether to "
            "run it with exactly these arguments. If they agree, repeat the same call with the same "
            f"arguments plus confirm_token; the token works once, for this call only, for {ttl} seconds."
        ),
    }
    if summary:
        payload["summary"] = summary
        payload["detail"] += " Show the user the summary, word for word, when you ask."
    text = json.dumps(payload)
    schema = tool.output_schema if isinstance(tool.output_schema, dict) else None
    structured = {"result": text} if schema and schema.get("x-fastmcp-wrap-result") else None
    return ToolResult(content=text, structured_content=structured)


def _log_ask(
    tool: str,
    path: str,
    outcome: str,
    elicit_error: str | None = None,
    *,
    protocol: str | None = None,
    capability: str | None = None,
) -> None:
    """The ask log line: tool, channel and outcome; never an argument, an account or a token."""
    details = "" if protocol is None else f" protocol={protocol} capability={capability or 'unavailable'}"
    error = "" if elicit_error is None else f" elicit_error={elicit_error}"
    LOGGER.info("policy ask tool=%s%s path=%s outcome=%s%s", tool, details, path, outcome, error)
