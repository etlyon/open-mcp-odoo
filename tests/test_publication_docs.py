import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.name
REVIEWED_HEADS = {
    "open-mcp-odoo": "48c84bdb75a2d4a612e85eb188498dc8f412ba2a",
    "open-mcp-nextcloud": "47b479e800488bc166b29bb974b9800478d87783",
    "open-mcp-ksuite": "96d5266cd6c92e49b4a8d33a6702e762c0b239af",
}


class PublicationDocsTests(unittest.TestCase):
    def test_one_command_install_is_bounded_and_keeps_state(self):
        readme = (ROOT / "README.md").read_text()
        compose = (ROOT / "compose.yaml").read_text()
        config = (ROOT / "config.example.toml").read_text()
        self.assertIn("docker compose up -d --build", readme)
        self.assertIn("127.0.0.1:", compose)
        self.assertIn("read_only: true", compose)
        self.assertIn("no-new-privileges:true", compose)
        self.assertIn("cap_drop:", compose)
        self.assertIn("connector-data:/data", compose)
        self.assertIn('state_dir = "/data"', config)

    def test_assistant_steps_are_current_and_link_official_sources(self):
        guide = (ROOT / "docs" / "assistants.md").read_text()
        self.assertNotIn("not yet", guide.lower())
        self.assertIn("Customize > Connectors", guide)
        self.assertIn("Add custom MCP server", guide)
        self.assertIn("Custom MCP Connector", guide)
        self.assertIn("support.claude.com", guide)
        self.assertIn("help.openai.com", guide)
        self.assertIn("docs.mistral.ai", guide)
        self.assertGreaterEqual(guide.count("/mcp"), 3)

    def test_security_proof_names_the_reviewed_source_without_internal_paths(self):
        proof = (ROOT / "SECURITY_REVIEW.md").read_text()
        self.assertIn(REVIEWED_HEADS[REPO], proof)
        self.assertIn("No known vulnerabilities", proof)
        self.assertIn("0 findings", proof)
        self.assertNotIn("/home/", proof)
        self.assertNotIn("~/.cache", proof)


if __name__ == "__main__":
    unittest.main()
