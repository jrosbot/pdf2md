# PDF2MD

Convert PDF files into Markdown with Microsoft's [MarkItDown](https://github.com/microsoft/markitdown).

## What it does

PDF2MD provides two ways to run the converter:

- **Remote MCP server:** receives a selected PDF through an authenticated ChatGPT MCP connection, converts it temporarily, and returns Markdown text so ChatGPT can create and attach a `.md` file.
- **Python CLI:** converts local PDFs on the user's computer.

## ChatGPT integration

The public connector will use a Streamable HTTP MCP endpoint declared in `mcp.json`. The endpoint must be deployed before importing the package into the public directory. This repository currently ships `mcp.json.example` so it does not advertise an undeployed URL as live; copy it to `mcp.json` and replace the example hostname after deployment.

### Use PDF2MD in ChatGPT

1. Install PDF2MD from the public Plugin Directory after its public release, or connect the deployed MCP server from the workspace marketplace while testing.
2. Attach the PDFs to convert in a ChatGPT chat.
3. Invoke `@pdf2md` and ask for Markdown files.

The MCP server is authenticated with OAuth/OIDC through Google, Microsoft, or Apple. PDF2MD creates one internal account per verified email address, preserves the provider's stable `sub`, and limits each account to 100 MiB of PDF input per UTC day. Users do not need Python, Codex, Claude, or a local project folder on their device.

**File handling:** ChatGPT provides the MCP server with a short-lived file download URL. The server downloads the selected PDF to temporary storage, converts it with MarkItDown, deletes the PDF, and returns the Markdown. It does not persist the source PDF or generated Markdown. If the PDF must never leave the device or ChatGPT's own environment, use the local CLI below instead of the remote MCP connector.

The server uses Python, `mcp`, `httpx`, `PyJWT`, and `markitdown[pdf]`. The skill package tells ChatGPT to call `convert_pdf_to_markdown` and attach the returned Markdown as a `.md` file.

## Deploy the MCP server

The repository now contains a production-oriented Streamable HTTP server in `pdf2md_server/`:

- `pdf2md_server/server.py` exposes `/mcp`, `/healthz`, and the OAuth browser flow.
- `pdf2md_server/auth.py` implements dynamic client registration, PKCE authorization codes, refresh-token rotation, and login through Google, Microsoft, and Apple.
- `pdf2md_server/storage.py` stores OAuth state, users, tokens, and daily byte usage in SQLite.
- `pdf2md_server/oidc.py` validates provider ID tokens against the provider's discovery document and JWKS.
- `requirements-server.txt` and `Dockerfile` build the deployable service.

Copy `.env.example` to the deployment's secret configuration and set:

```bash
PDF2MD_PUBLIC_BASE_URL=https://mcp.example.com
PDF2MD_ISSUER_URL=https://mcp.example.com
PDF2MD_RESOURCE_URL=https://mcp.example.com/mcp
PDF2MD_DATABASE_PATH=/data/pdf2md.sqlite3
PDF2MD_SESSION_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
```

Register these callback URLs in each enabled identity provider:

```text
https://mcp.example.com/oauth/callback/google
https://mcp.example.com/oauth/callback/microsoft
https://mcp.example.com/oauth/callback/apple
```

The SQLite directory must be on persistent storage. For more than one server replica, put the OAuth and quota store behind a shared database before scaling horizontally. Set `PDF2MD_ALLOWED_DOWNLOAD_HOSTS` to the exact public file-service hosts accepted by the deployment; the server rejects non-HTTPS URLs, localhost, private IPs, and redirects.

For the provider-by-provider credential setup, see [`docs/oauth-credentials.md`](docs/oauth-credentials.md). It covers Google, Microsoft, Apple, callback URLs, secret storage, and the final connection test.

Run locally for a protocol smoke test:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-server.txt
PDF2MD_SESSION_SECRET=local-development-secret-please-change \
  .venv/bin/uvicorn pdf2md_server.server:app --host 127.0.0.1 --port 8000
```

`GET /healthz` should return `{"status":"ok","service":"pdf2md"}`. Production deployment still needs a real HTTPS hostname, provider credentials, persistent storage, and the DNS/TLS configuration for that hostname.

### Import the workspace marketplace for testing

A workspace admin can import the marketplace from **Admin → Plugins → Add → Import marketplace**:

1. Set **Source** to `https://github.com/jrosbot/pdf2md`.
2. Leave **Path** empty; the marketplace manifest is at `.agents/plugins/marketplace.json`.
3. Choose `cloud-work` to test skills-only changes. Once the MCP server has a real HTTPS endpoint, add the endpoint to `mcp.json`, import the marketplace again, and review the OAuth connection flow.
4. Install `pdf2md` from **Plugins** and invoke it with `@pdf2md` in a new Work chat.

The workspace GitHub marketplace import is an admin feature. If you do not see **Admin → Plugins**, ask your workspace admin to import it and make the plugin available to your role.

## Public directory listing details

| Field | Value |
| --- | --- |
| Publisher identity | **THE DUCKS IN A ROW S.L.**, the legal entity named on [The Diar legal page](https://www.thediar.com/es/legal/). Its identity still needs verification in the submission portal. |
| Availability | European Union (EU-27): AT, BE, BG, CY, CZ, DE, DK, EE, ES, FI, FR, GR, HR, HU, IE, IT, LT, LU, LV, MT, NL, PL, PT, RO, SE, SI, SK. |
| Payments | **No.** The connector does not charge users or require a purchase. |
| Privacy policy URL | [The Diar legal page](https://www.thediar.com/es/legal/). |
| Terms of service URL | [The Diar legal page](https://www.thediar.com/es/legal/), as supplied for the listing. |
| Icon asset | `assets/pdf2md-icon.png` (512 × 512 PNG). |

The publisher confirms that the privacy policy and terms at this legal page apply to PDF2MD. The page says its privacy policy was last updated in May 2018.

The listing metadata is also included in `plugin.json` so the public submission portal can prefill it. Confirm the verified Developer identity and review the imported fields before submitting.

## Run the converter locally

The CLI works on Ubuntu, macOS, and Windows. It converts files on your computer; PDF2MD does not upload your PDFs. Installing the Python dependencies requires an internet connection and downloads the required packages.

Clone the repository and enter its folder (these commands work in bash, zsh, and PowerShell):

```bash
git clone https://github.com/jrosbot/pdf2md.git
cd pdf2md
```

Alternatively, download and extract the repository ZIP, then open a terminal in the extracted `pdf2md` folder.

### Ubuntu

Install Python, its virtual-environment support, and Git if they are not already installed:

```bash
sudo apt update
sudo apt install python3 python3-venv git
```

Create an isolated Python environment and install the dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Convert one or more PDFs:

```bash
.venv/bin/python skills/convert-pdf-to-markdown/scripts/convert_pdf.py "$HOME/Downloads/report.pdf"
```

### macOS

Make sure Python 3 and Git are installed. Create an isolated environment and install the dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Convert one or more PDFs:

```bash
.venv/bin/python skills/convert-pdf-to-markdown/scripts/convert_pdf.py "$HOME/Downloads/report.pdf"
```

### Windows (PowerShell)

Make sure Python 3 and Git are installed. In PowerShell, create an isolated environment and install the dependencies:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Convert a PDF:

```powershell
.\.venv\Scripts\python.exe .\skills\convert-pdf-to-markdown\scripts\convert_pdf.py "$HOME\Downloads\report.pdf"
```

By default, the Markdown files are written to `converted-markdown/` inside the repository folder. To choose another output folder, add `--output-dir`:

```bash
# Ubuntu or macOS
.venv/bin/python skills/convert-pdf-to-markdown/scripts/convert_pdf.py "$HOME/Downloads/report.pdf" --output-dir "$HOME/Documents/markdown"
```

```powershell
# Windows
.\.venv\Scripts\python.exe .\skills\convert-pdf-to-markdown\scripts\convert_pdf.py "$HOME\Downloads\report.pdf" --output-dir "$HOME\Documents\markdown"
```

To convert multiple PDFs in one run, add their paths after the script name.

## Current limits

- PDF input only; one Markdown output per PDF.
- Uses MarkItDown for extraction; no OCR service or LLM-based rewriting is added by this project.
- The remote MCP server accepts one PDF at a time, up to 25 MiB, with a 100 MiB per-account daily input quota.
- The remote server needs a configured Google, Microsoft, or Apple OIDC client before login buttons appear.
- ChatGPT transfers the selected PDF to the remote server for the duration of conversion; the local CLI keeps it on the user's device.
