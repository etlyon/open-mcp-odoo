"""No tool may ever read a file from the connector's own machine on a caller's say-so.

A connector is public: whatever an assistant passes as a "path" is attacker
input. Attachments therefore travel only as bytes (strict base64); a path-like
argument is refused before anything runs. The source scan at the end keeps it
that way for every app added later.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from pydantic import ValidationError

from open_connectors.apps.odoo.attachments import FileAttachmentInput, decode_content
from open_connectors.apps.odoo.client import OdooAPIError

PDF_B64 = "JVBERi0xLjQKJcOkw7zDtsOfCjEgMCBvYmoKPDw+PgplbmRvYmoKdHJhaWxlcgo8PD4+CiUlRU9GCg=="

BASE = dict(model="res.partner", record_id=1, filename="invoice.pdf", content_base64=PDF_B64)


class NoLocalPaths(unittest.TestCase):
    def test_path_argument_refused(self):
        for extra in ({"path": "/etc/passwd"}, {"attachments": ["/etc/hosts"]}, {"file_path": "/data/x.pdf"}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                FileAttachmentInput(**BASE, **extra)

    def test_filename_cannot_be_a_path(self):
        for name in ("../../etc/passwd.pdf", "/etc/passwd.pdf", "C:\\x.pdf", "a/b.pdf"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                FileAttachmentInput(**{**BASE, "filename": name})

    def test_content_must_be_bytes_not_a_path(self):
        for value in ("/etc/passwd", "file:///etc/passwd", "~/.ssh/id_rsa"):
            with self.subTest(value=value), self.assertRaises(OdooAPIError):
                decode_content(value, 1024 * 1024)


    def test_source_has_no_file_reads_from_input(self):
        """Fail if any app module opens or reads a file (the only allowed case is listed)."""
        allowed = {("pdf_validation.py", "Path(__file__)")}
        pattern = re.compile(r"\bopen\(|\.read_bytes\(|\.read_text\(|readFileSync|os\.open\(|Path\(")
        root = Path(__file__).resolve().parent.parent / "src" / "open_connectors" / "apps"
        scanned = {str(path.relative_to(root)) for path in root.rglob("*.py")}
        for module in ("odoo/adapter.py", "odoo/tools.py", "odoo/attachments.py"):
            self.assertIn(module, scanned)
        found = []
        for path in root.rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line) and not any(path.name == f and token in line for f, token in allowed):
                    found.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
        self.assertEqual(found, [], "an app module reads files; caller-given paths must never be read")


if __name__ == "__main__":
    unittest.main()
