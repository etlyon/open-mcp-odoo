"""One process = one app on one host: sign-in server + gate + the app's tools."""

from __future__ import annotations

import argparse
import logging
import os
import time
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from open_connectors import config as config_module
from open_connectors import journal
from open_connectors.oauth.server import AuthServer, ResourceGate
from open_connectors.pages import flow as pages
from open_connectors.store import Store

SWEEP_SECONDS = 600
APP = "odoo"


def _adapter() -> Any:
    from open_connectors.apps.odoo import adapter
    return adapter


def _tools_app(settings: config_module.Settings) -> Any:
    """The app's tools as an ASGI app at ``/mcp``. Imported after the environment is set."""
    from open_connectors.apps.odoo import client as odoo_client
    odoo_client.DENIED_HOSTS = settings.denied_hosts | {settings.public_host}
    from open_connectors.apps.odoo import tools
    tools.startup_check()
    return tools.mcp.http_app(path="/mcp", json_response=True, stateless_http=True)


class Application:
    """ASGI entry: lifespan goes to the tools app; HTTP goes through the gate."""

    def __init__(self, settings: config_module.Settings, *, adapter: Any | None = None, tools_app: Any | None = None):
        settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.environ.setdefault(journal.JOURNAL_ENV, str(settings.state_dir / "journal.sqlite"))
        pages.MAKER["label"] = settings.maker_label
        pages.BRAND.update(styles=settings.brand_styles if settings.brand_dir else (),
                           logo=settings.brand_logo if settings.brand_dir else "", name=settings.brand_name)
        self.settings = settings
        self.store = Store(settings.state_dir / "connector.sqlite")
        self.auth = AuthServer(settings, self.store, adapter or _adapter())
        self.tools = tools_app or _tools_app(settings)
        others = Starlette(routes=[*self.auth.routes(), Route("/healthz", self.healthz, methods=["GET"])])
        self.gate = ResourceGate(self.tools, self.auth, others)
        self._last_sweep = 0.0

    async def healthz(self, _: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "app": self.settings.app})

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self.tools(scope, receive, send)
            return
        now = time.monotonic()
        if now - self._last_sweep > SWEEP_SECONDS:
            self._last_sweep = now
            removed = self.store.sweep()
            if removed:
                logging.getLogger("open_connectors").info("sweep removed %d unusable connections", removed)
        await self.gate(scope, receive, send)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="open-connectors")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the Odoo connector")
    serve.add_argument("--config", default=None)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--forwarded-allow-ips", default="127.0.0.1",
                       help="addresses of the reverse proxy whose X-Forwarded-* headers are trusted")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("httpx", "httpcore", "fastmcp", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = config_module.load(args.config)
    import uvicorn
    uvicorn.run(Application(settings), host=args.host, port=args.port, proxy_headers=True,
                forwarded_allow_ips=args.forwarded_allow_ips, access_log=False, server_header=False)


if __name__ == "__main__":
    main()
