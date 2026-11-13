"""Bounded structural PDF validation; never render, extract, or rewrite content.

PDF checks, carried over from an earlier private implementation. One change:
macOS does not implement RLIMIT_AS, so on darwin only its ValueError is
tolerated (CPU and wall-clock limits still apply). On Linux — the image — the
address-space limit is applied exactly as before and any failure still fails
closed.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

if __name__ != "__main__":
    from open_connectors.apps.odoo.client import OdooAPIError

PDF_MAX_BYTES = 10 * 1024 * 1024
_slots = asyncio.Semaphore(4)


async def validate_pdf(data: bytes) -> dict:
    # The child receives document bytes only, never the service's credentials.
    async with _slots:
        child = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(Path(__file__).resolve()),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        )
        try:
            output, _ = await asyncio.wait_for(child.communicate(data), timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            if child.returncode is None:
                child.kill()
            await child.wait()
            if asyncio.current_task().cancelling():
                raise
            raise OdooAPIError("PDF validation exceeded its resource limit.") from None
        if child.returncode != 0:
            raise OdooAPIError(
                "PDF is invalid, encrypted, or exceeds structural validation limits."
            )
        return json.loads(output)


def _validate_stdin() -> None:
    import io
    import logging
    import re
    import resource

    try:
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    except ValueError:
        if sys.platform != "darwin":
            raise
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    logging.disable(logging.CRITICAL)
    from pypdf import PdfReader

    raw = sys.stdin.buffer.read(PDF_MAX_BYTES + 1)
    if len(raw) > PDF_MAX_BYTES or not re.match(rb"%PDF-(1\.[0-7]|2\.0)[\r\n]", raw):
        raise ValueError("PDF signature")
    if not raw.rstrip(b" \t\r\n\x00").endswith(b"%%EOF"):
        raise ValueError("PDF end marker")
    reader = PdfReader(io.BytesIO(raw), strict=True)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDF")
    root = reader.trailer["/Root"]
    if root.get("/Type") != "/Catalog" or "/Pages" not in root:
        raise ValueError("PDF catalog")
    pages = len(reader.pages)
    if not 1 <= pages <= 10000:
        raise ValueError("PDF page count")
    for page in reader.pages:
        if page.get("/Type") != "/Page" or len(page.mediabox) != 4:
            raise ValueError("PDF page structure")
    print(json.dumps({"pages": pages, "validation": "bounded strict PDF structure"}))


if __name__ == "__main__":
    try:
        _validate_stdin()
    except Exception:
        sys.exit(1)
