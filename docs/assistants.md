# Add this connector to an assistant

Use the connector's full public address, including `/mcp`, for example
`https://odoo.example.org/mcp`. Do not add a static API key or request header:
the connector discovers OAuth and asks each person to approve their own account.

These menu paths were checked against the vendors' public instructions on
18 November 2026. They are publication instructions, not a claim that ETLYON
clicked them in a paid vendor seat.

## Claude

For an individual Claude account, open **Customize > Connectors**, choose
**+ Add > Add custom connector**, enter a name and the full `/mcp` address,
then continue. Keep the detected sign-in method and choose **Use Claude's
published identity (Recommended)** for the OAuth client. Finish with **Add**,
then **Connect** if Claude asks.

For Team or Enterprise, an owner first uses **Organization settings >
Connectors > Add > Custom > Web** with the same address. Each member then uses
**Customize > Connectors > Connect**. Claude requires a public HTTPS endpoint
on port 443.

Official instructions: <https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp>

## ChatGPT

Custom MCP servers with full tool support are currently a ChatGPT Business or
Enterprise/Edu web feature. An admin, owner or authorized developer opens
**Plugins**, chooses **+ > Add custom MCP server**, enters a name and the full
public HTTPS `/mcp` address, configures the detected authentication, accepts
the risk notice, then chooses **Create as a plugin**. Review the discovered
tools before installing or publishing it. In a new conversation, type `@` and
select the installed plugin.

Official instructions: <https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt>

## Le Chat / Mistral Vibe Work

An administrator opens **Connectors**, chooses **+ Add Connector**, switches
to **Custom MCP Connector**, enters a unique name with no spaces or special
characters, and pastes the full `/mcp` address as **Server URL**. Choose
**Connect** and complete the detected OAuth flow. On Free, Pro and Student
plans, the account owner is the administrator by default.

Official instructions: <https://docs.mistral.ai/vibe/work/web/connectors/mcp-connectors>

## First connection

Whichever assistant you use, the next browser window belongs to this
connector. Check its name and address, connect your own Odoo, Hub or kSuite
account, and approve only the access you intend. Start with manual approval for
write actions. Removing the connector from an assistant does not rotate an app
password or API token unless the app's own instructions say so.
