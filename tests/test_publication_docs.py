import os
import shutil
import subprocess
import tempfile
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

    def test_name_gate_allows_only_exact_public_attribution_locations(self):
        private_name = "ExamplePrivateOperator"
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            repo = base / "repo"
            (repo / "ci").mkdir(parents=True)
            (repo / "docs").mkdir()
            shutil.copy2(ROOT / "ci" / "name-check", repo / "ci" / "name-check")
            os.chmod(repo / "ci" / "name-check", 0o755)
            (repo / "README.md").write_text(
                "This code was written with the help of generative AI tools and reviewed by "
                f"{private_name}. It is intended for professional use; test it in your environment "
                "before production. Provided as is, without warranty.\n"
            )
            (repo / "config.example.toml").write_text(
                "one = 1\ntwo = 2\nthree = 3\nfour = 4\n"
                f'source_url = "https://github.com/{private_name}/open-mcp-sample"\n'
            )
            (repo / "docs" / "assistants.md").write_text("Generic publication guide.\n")
            words = base / "private-words.txt"
            words.write_text(private_name + "\n")
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            env = {**os.environ, "NAME_CHECK_FILE": str(words)}
            allowed = subprocess.run(["ci/name-check"], cwd=repo, env=env, text=True, capture_output=True)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            (repo / "docs" / "leak.md").write_text(private_name + "\n")
            subprocess.run(["git", "add", "docs/leak.md"], cwd=repo, check=True)
            refused = subprocess.run(["ci/name-check"], cwd=repo, env=env, text=True, capture_output=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("docs/leak.md:1", refused.stderr)


if __name__ == "__main__":
    unittest.main()
