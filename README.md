This code was written with the help of generative AI tools and reviewed by ETLYON. It is intended for professional use; test it in your environment before production. Provided as is, without warranty.

# open-mcp-odoo

Connect any assistant to **your own Odoo**. You bring your server or account and your own key; nothing is set up in advance.

Works with Claude, ChatGPT, Le Chat, eBot and any assistant that can add a connector by address. Your assistant acts as you, with exactly the rights you have.

> "mcp" appears only in this repository's name. People and product screens say **Apps** / **Connector** (D386).

## Install in 5 minutes

**1. Run one Docker container** (TLS in front of it; set the three names below — never put secrets in the image):

```sh
mkdir -p /etc/open-mcp-odoo /var/lib/open-mcp-odoo
cat >/etc/open-mcp-odoo/connector.toml <<'EOF'
public_url = "https://odoo.example.org"
app = "odoo"
state_dir = "/var/lib/open-mcp-odoo"
source_url = "https://github.com/etlyon/open-mcp-odoo"
maker_label = "open source"
EOF
docker build -t open-mcp-odoo:0.1.0 .
docker run --name open-mcp-odoo --restart=unless-stopped \
  -p 127.0.0.1:8110:8000 \
  -v /etc/open-mcp-odoo/connector.toml:/config/connector.toml:ro \
  -v /var/lib/open-mcp-odoo:/var/lib/open-mcp-odoo \
  open-mcp-odoo:0.1.0
```

Settings (names only — put your own values):
| Setting | Meaning |
|---|---|
| `public_url` | Public https host of **this** connector (no path) |
| `source_url` | Public source of the code you run (AGPL §13) |
| `state_dir` | Where the SQLite file lives (0700) |
| `allowed_servers` | Optional exact hosts people may connect (Odoo server apps) |
| `allowed_mail_domains` / `send_recipient_domains` | kSuite only — see `config.example.toml` |

Put a reverse proxy with TLS in front (`X-Forwarded-For` / `X-Forwarded-Proto`) and pass `--forwarded-allow-ips` if you change the entrypoint.

**2. Connect it in eBot (or any AI app)**

1. In the assistant, add a custom connector / MCP server and paste `https://<your-connector-host>/mcp`.
2. Complete the on-screen window (address + key, or Grant access, or Infomaniak token — this app's steps are below).
3. Allow the assistant when asked. Done.

Same paste works in Claude, ChatGPT, Le Chat and eBot. Menu names per assistant: [docs/assistants.md](docs/assistants.md).

## Connect (A → B → C → D)

**A. Add the connector to your assistant.** In your assistant's settings, add a custom connector and paste the address your operator gives you, e.g. `https://odoo.example.org/mcp`. In Le Chat only an administrator can add one. (The exact menu names per assistant are in [docs/assistants.md](docs/assistants.md), checked by hand.)

**B. Which server?** A page opens. Type the address you open Odoo with, e.g. `erp.example.com`. The page checks it is a real Odoo 19 or newer.

**C. Your key.** In Odoo: your picture › My Preferences › Security › API Keys › Add API Key. Name it "Assistant", copy it, paste it on the page. The page then shows **Allow \<assistant\> into your Odoo?** with your name and your server. Check both, then click Allow.

**D. Done.** Your assistant can now read and change in Odoo what you can.

To stop: remove the connector in your assistant and delete the key in Odoo. A connection nobody uses for 30 days is deleted, and its key can no longer be read.

Two Odoo servers in one assistant: add one address per server, with the server in it, e.g. `https://odoo.example.org/erp.example.com/mcp`.

## What it keeps

- Your key (Odoo), app password (Hub) or API token (kSuite, with the id and address of your own mailbox), encrypted with a key that only your assistant's live sign-in can unlock. A copy of the connector's disk or backups opens nothing.
- Your name and email as Odoo, Nextcloud or Infomaniak gives them, to show on the "Allow?" and "connected" pages.
- For a change made with an idempotency key (so a repeat does not write twice): the key, a hash of the arguments and the answer. For kSuite only the ids, yes/no answers and counts of that answer are kept: no address, subject, title or text.
- Nothing else: no chat content.

## Licence

AGPL-3.0-or-later ([LICENSE](LICENSE)). If you run a modified copy for others, offer them its source: set `source_url`; every page links to it. Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Not affiliated with Odoo S.A., Nextcloud GmbH or Infomaniak.
