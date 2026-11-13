"""Extract text from untrusted PDF and Office files in a short-lived child.

The child receives the PDF on stdin and nothing else: a clean environment, a
memory cap, a CPU cap and a wall-clock cap. It does not see credentials.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PDF_MAX_BYTES = 10 * 1024 * 1024
OFFICE_MAX_BYTES = 25 * 1024 * 1024
OFFICE_MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
XML_MAX_BYTES = 20 * 1024 * 1024
PDF_MAX_PAGES = 75


class PdfTextError(ValueError):
    """The PDF could not be read inside the limit. The message is this connector's, not the parser's."""


class OfficeTextError(ValueError):
    """An Office package could not be read inside the child-process limits."""


def _run_child(data: bytes, mode: str, max_chars: int, error_type: type[ValueError]) -> tuple[str, dict, list[str]]:
    try:
        child = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).resolve()), mode, str(max_chars)],
            input=data,
            capture_output=True,
            timeout=5,
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise error_type("The document took too long to read.") from exc
    if child.returncode != 0:
        raise error_type("The document could not be read inside the safety limits.")
    try:
        payload = json.loads(child.stdout.decode("utf-8"))
        text = payload["text"]
        stats = payload["stats"]
        warnings = payload["warnings"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise error_type("The document could not be read inside the safety limits.") from exc
    if not isinstance(text, str) or not isinstance(stats, dict) or not isinstance(warnings, list):
        raise error_type("The document could not be read inside the safety limits.")
    return text, stats, [item for item in warnings if isinstance(item, str)]


def extract_pdf_text(data: bytes) -> tuple[str, dict, list[str]]:
    """``(text, stats, warnings)``. Refuses a PDF over 10 MiB before the child starts."""
    if len(data) > PDF_MAX_BYTES:
        raise PdfTextError("The PDF is larger than 10 MiB.")
    return _run_child(data, "pdf", 1_000_000, PdfTextError)


def extract_office_text(data: bytes, kind: str, max_chars: int) -> tuple[str, dict, list[str]]:
    """Extract a ZIP-based document outside the connector process."""
    if kind not in {"docx", "xlsx", "pptx", "odf"}:
        raise OfficeTextError("Unsupported Office document type.")
    if len(data) > OFFICE_MAX_BYTES:
        raise OfficeTextError("The Office document is larger than 25 MiB.")
    if type(max_chars) is not int or not 1 <= max_chars <= 1_200_000:
        raise OfficeTextError("The extraction limit is invalid.")
    return _run_child(data, kind, max_chars, OfficeTextError)


def _extract_stdin(mode: str, max_chars: int) -> None:
    import io
    import logging
    import resource

    try:
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    except ValueError:
        if sys.platform != "darwin":
            raise
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    logging.disable(logging.CRITICAL)
    maximum = PDF_MAX_BYTES if mode == "pdf" else OFFICE_MAX_BYTES
    raw = sys.stdin.buffer.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("document size")
    if mode == "pdf":
        payload = _pdf_payload(io, raw)
    else:
        payload = _office_payload(io, raw, mode, max_chars)
    print(json.dumps(payload))


def _pdf_payload(io: object, raw: bytes) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ValueError("PDF encrypted") from exc
    total = len(reader.pages)
    limit = min(total, PDF_MAX_PAGES)
    parts: list[str] = []
    warnings: list[str] = []
    for index in range(limit):
        try:
            page_text = reader.pages[index].extract_text() or ""
        except Exception:
            warnings.append(f"Page {index + 1} could not be extracted.")
            continue
        if page_text.strip():
            parts.append(f"[Page {index + 1}]\n{page_text}")
    if total > limit:
        warnings.append(f"Only the first {limit} of {total} pages were processed.")
    if not parts:
        warnings.append("No embedded text was found; this document may require OCR.")
    return {
        "text": "\n\n".join(parts),
        "stats": {"pages_total": total, "pages_processed": limit},
        "warnings": warnings,
    }


def _package(io: object, raw: bytes):
    from zipfile import BadZipFile, ZipFile

    try:
        archive = ZipFile(io.BytesIO(raw))
    except BadZipFile as exc:
        raise ValueError("invalid office package") from exc
    if sum(item.file_size for item in archive.infolist()) > OFFICE_MAX_UNCOMPRESSED_BYTES:
        archive.close()
        raise ValueError("expanded office package")
    return archive


def _bounded(parts: list[str], value: str, max_chars: int) -> bool:
    """Append at most max_chars * 2 characters; true means extraction stopped."""
    used = sum(len(item) + 1 for item in parts)
    remaining = max_chars * 2 - used
    if remaining <= 0:
        return True
    parts.append(value[:remaining])
    return len(value) > remaining


def _odf_text(content: bytes, max_chars: int) -> tuple[str, int, bool]:
    from xml.parsers import expat

    if len(content) > XML_MAX_BYTES:
        raise ValueError("document XML size")
    parser = expat.ParserCreate(namespace_separator="}")
    def refuse(*_args):
        raise ValueError("document type")
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parts: list[str] = []
    current: list[str] | None = None
    paragraph_count = 0
    truncated = False
    def start(name: str, _attrs: dict) -> None:
        nonlocal current
        if name.rsplit("}", 1)[-1] in {"p", "h"}:
            current = []
    def chars(value: str) -> None:
        if current is not None:
            current.append(value)
    def end(name: str) -> None:
        nonlocal current, paragraph_count, truncated
        if current is not None and name.rsplit("}", 1)[-1] in {"p", "h"}:
            value = "".join(current).strip()
            current = None
            if value:
                paragraph_count += 1
                truncated = _bounded(parts, value, max_chars) or truncated
    parser.StartElementHandler = start
    parser.CharacterDataHandler = chars
    parser.EndElementHandler = end
    parser.Parse(content, True)
    return "\n".join(parts), paragraph_count, truncated


def _office_payload(io: object, raw: bytes, mode: str, max_chars: int) -> dict:
    warnings: list[str] = []
    if mode == "docx":
        from docx import Document
        with _package(io, raw):
            pass
        document = Document(io.BytesIO(raw))
        parts: list[str] = []
        truncated = False
        for paragraph in document.paragraphs:
            if paragraph.text.strip() and _bounded(parts, paragraph.text, max_chars):
                truncated = True
                break
        table_rows = 0
        if not truncated:
            for table in document.tables:
                for row in table.rows:
                    value = "\t".join(cell.text.strip() for cell in row.cells)
                    if value.strip():
                        table_rows += 1
                        if _bounded(parts, value, max_chars):
                            truncated = True
                            break
                if truncated:
                    break
        stats = {"paragraphs": len(document.paragraphs), "table_rows": table_rows}
        text = "\n".join(parts)
    elif mode == "xlsx":
        from openpyxl import load_workbook
        with _package(io, raw):
            pass
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        parts = []
        cells_read = 0
        truncated = False
        try:
            sheet_count = len(workbook.sheetnames)
            for worksheet in workbook.worksheets:
                if _bounded(parts, f"[Sheet: {worksheet.title}]", max_chars):
                    truncated = True
                    break
                for row in worksheet.iter_rows(values_only=True):
                    values = ["" if value is None else str(value) for value in row]
                    while values and values[-1] == "":
                        values.pop()
                    if not values:
                        continue
                    cells_read += len(values)
                    if _bounded(parts, "\t".join(values), max_chars):
                        truncated = True
                        break
                if truncated:
                    break
        finally:
            workbook.close()
        stats = {"sheets": sheet_count, "cells_read": cells_read}
        text = "\n".join(parts)
    elif mode == "pptx":
        from pptx import Presentation
        with _package(io, raw):
            pass
        presentation = Presentation(io.BytesIO(raw))
        parts = []
        truncated = False
        for index, slide in enumerate(presentation.slides, start=1):
            values = [getattr(shape, "text", "").strip() for shape in slide.shapes]
            values = [value for value in values if value]
            if values and _bounded(parts, f"[Slide {index}]\n" + "\n".join(values), max_chars):
                truncated = True
                break
        stats = {"slides": len(presentation.slides)}
        text = "\n\n".join(parts)
    elif mode == "odf":
        with _package(io, raw) as archive:
            info = archive.getinfo("content.xml")
            if info.file_size > XML_MAX_BYTES:
                raise ValueError("document XML size")
            with archive.open(info) as handle:
                content = handle.read(XML_MAX_BYTES + 1)
        if len(content) > XML_MAX_BYTES:
            raise ValueError("document XML size")
        text, paragraph_count, truncated = _odf_text(content, max_chars)
        stats = {"paragraphs": paragraph_count}
    else:
        raise ValueError("unsupported mode")
    if truncated:
        stats["extraction_truncated"] = True
        warnings.append("Document processing stopped after reaching the extraction limit.")
    return {"text": text, "stats": stats, "warnings": warnings}


if __name__ == "__main__":
    try:
        selected = sys.argv[1] if len(sys.argv) > 1 else "pdf"
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 1_000_000
        _extract_stdin(selected, limit)
    except Exception:
        sys.exit(1)
