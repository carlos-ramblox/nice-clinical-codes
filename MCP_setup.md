# MCP setup for the OLS MCP Server project
 
This project depends on **two** MCP (Model Context Protocol) servers. Both must be reachable from the Claude client (Cowork mode, Claude Desktop, 
or Claude Code) before any of the queries described in `CLAUDE.md` will work.
 
| Server | Purpose | Tool prefix you'll see in Claude |
|---|---|---|
| OLS (Ontology Lookup Service) | SNOMED CT lookups (and other ontologies indexed by EBI OLS) | `mcp__ols4__…` |
| ICD-10 Codes | ICD-10-CM and ICD-10-PCS lookups | `mcp__<uuid>__…` (e.g. `search_codes`, `get_hierarchy`, `lookup_code`) |
 
This document gives three equivalent ways to specify the connections, so the recipient can pick whichever matches their client:
 
1. Cowork connector instructions (point-and-click, for the Claude desktop app)
2. A project-scoped `.mcp.json` (auto-loaded by Claude Code / Claude Agent SDK when this folder is opened)
3. A snippet to paste into a user-level `claude_desktop_config.json` (Claude Desktop)
---
 
## 1. Cowork connector instructions
 
Use this route if the recipient is using **Cowork mode in the Claude desktop app**.
 
### 1a. ICD-10 Codes (registry connector)
 
This server is published in the Anthropic MCP connector directory and is the easiest of the two to add.
 
1. Open the Claude desktop app and switch to a Cowork-enabled chat.
2. Open **Settings → Connectors** (or click the connector icon in the composer).
3. Search for **"ICD-10 Codes"**.
4. Click **Connect**. No credentials are required for the public HCLS connector.
5. Confirm it appears as **Connected** and **Enabled in chat**.
Reference details (for verification only — you do not paste these manually):
 
- Name: `ICD-10 Codes`
- Description: Access ICD-10-CM and ICD-10-PCS code sets
- URL: `https://hcls.mcp.claude.com/icd10_codes/mcp`
- Directory UUID: `bd8c051d-df35-44c0-a8b8-084b700e1f21`
- Tools exposed: `search_codes`, `search_diagnosis_by_code`, `search_diagnosis_by_description`, `search_procedure_by_code`,
- `search_procedure_by_description`, `lookup_code`, `validate_code`, `get_hierarchy`, `get_by_category`, `get_by_body_system`
### 1b. OLS (custom connector)
 
The OLS server is **not** currently published in the Cowork connector directory, so it has to be added as a custom MCP. It is hosted 
publicly by EBI and requires no authentication.
 
1. In the Claude desktop app, open **Settings → Connectors → Add custom connector**.
2. Fill in:
   - **Name:** `OLS` (must result in tools showing under the prefix `mcp__ols4__…`)
   - **Transport:** `http`
   - **URL:** `https://www.ebi.ac.uk/ols4/api/mcp`
   - **Args / headers:** *(none)*
3. Click **Connect** and confirm tools such as `searchClasses`, `getChildren`, `getAncestors`, `listOntologies` appear.
Once both connectors show **Connected** in the desktop app, this project's queries will work end-to-end.
 
---
 
## 2. Project-scoped `.mcp.json`
 
Use this route if the recipient is using **Claude Code** or the **Claude Agent SDK** and wants the MCPs to load automatically whenever 
they open this folder. Save the file as `.mcp.json` at the root of the project (i.e. alongside `CLAUDE.md`).
 
```json
{
  "mcpServers": {
    "icd10": {
      "type": "http",
      "url": "https://hcls.mcp.claude.com/icd10_codes/mcp"
    },
    "ols4": {
      "type": "http",
      "url": "https://www.ebi.ac.uk/ols4/api/mcp"
    }
  }
}
```
 
Notes:
 
- The keys under `mcpServers` (`icd10`, `ols4`) become the prefix that Claude uses when naming tools, e.g. `mcp__ols4__searchClasses`. Keep `ols4` exactly as-is so existing tool calls in this project continue to resolve.
- `.mcp.json` is read on session start; restart the Claude Code session after editing it.
- Both endpoints above are public and require no credentials. If EBI ever introduces auth on OLS, add an `Authorization` header object alongside `url` rather than committing secrets to the repo — use a `.env` file or the host's secret store.
---
 
## 3. Claude Desktop `claude_desktop_config.json` snippet
 
Use this route if the recipient prefers a **user-level** Claude Desktop config rather than per-project. Paste the `mcpServers` block below into their existing `claude_desktop_config.json` (merging with any servers already configured). Locations:
 
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`
```json
{
  "mcpServers": {
    "icd10": {
      "type": "http",
      "url": "https://hcls.mcp.claude.com/icd10_codes/mcp"
    },
    "ols4": {
      "type": "http",
      "url": "https://www.ebi.ac.uk/ols4/api/mcp"
    }
  }
}
```
 
Restart Claude Desktop after saving. The tool prefixes should match the pattern this project relies on (`mcp__ols4__…` and the ICD-10 tool names listed above).
 
---
 
## Verifying the setup
 
Once both servers are connected, run this quick smoke test in a Claude session opened against this project folder:
 
> "Look up SNOMED CT and ICD-10 codes for 'haemorrhagic stroke'."
 
The expected behaviour (per `CLAUDE.md`) is that Claude calls `searchClasses` on OLS with `ontology="snomed"` and `search_codes` on the ICD-10 server with `code_type="diagnosis"`, and returns the two-object JSON described in the project instructions. A canonical example output is already in the project folder — see `haemorrhagic_stroke_20260426_233236.json` — for comparison.
 
## Server reference
 
| Server | Transport | URL | Auth |
|---|---|---|---|
| ICD-10 Codes | http | `https://hcls.mcp.claude.com/icd10_codes/mcp` | none (public Anthropic HCLS connector) |
| OLS (EBI Ontology Lookup Service) | http | `https://www.ebi.ac.uk/ols4/api/mcp` | none (public EBI endpoint) |
