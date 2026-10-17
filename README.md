# PDF2MD

Convert PDF files into Markdown with Microsoft's [MarkItDown](https://github.com/microsoft/markitdown).

## What it does

PDF2MD provides two ways to run the converter:

- **ChatGPT Work plugin:** converts PDFs attached to a Work chat in Work's cloud execution environment and returns downloadable Markdown files.
- **Python CLI:** converts local PDFs on the user's computer.

## ChatGPT integration

This repository contains a skills-only ChatGPT plugin package and a GitHub-importable workspace marketplace for testing. The plugin does not require a hosted MCP server.

### Use PDF2MD in ChatGPT Work

1. Install PDF2MD from the public Plugin Directory after its public release, or install it from the workspace marketplace while testing.
2. Start a **Work** chat and attach the PDFs to convert.
3. Invoke `@pdf2md` and ask for Markdown files.

The plugin runs its bundled conversion workflow in the Work execution environment. Users do not need Python, Codex, Claude, or a local project folder on their device. The public plugin manifest intentionally does not set `requires_local_executor`, so it is not marked Desktop only.

**File handling:** Work on web and mobile runs in OpenAI's cloud. Attaching a PDF makes it available to ChatGPT/OpenAI for processing in that cloud task. PDF2MD does not send the source PDF to the publisher's server, GitHub, or a separate PDF conversion service. If the PDF must never leave the device, use the local CLI below instead of Cloud Work.

The plugin uses Python and `markitdown[pdf]`. Its skill package includes the dependency declaration at `skills/convert-pdf-to-markdown/requirements.txt`; if MarkItDown is not already available, the workflow installs it in the temporary Work environment. That step may download software packages from the Python package index, but it does not upload the PDF. If the Work environment cannot access the package index, the conversion will report that dependency error.

### Import the workspace marketplace for testing

A workspace admin can import the marketplace from **Admin → Plugins → Add → Import marketplace**:

1. Set **Source** to `https://github.com/jrosbot/pdf2md`.
2. Leave **Path** empty; the marketplace manifest is at `.agents/plugins/marketplace.json`.
3. Choose `cloud-work` to test these changes, import the marketplace, and review the plugin's installation policy. After the branch is merged, choose `main`.
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
- ChatGPT Work processes chat attachments in OpenAI's cloud. The CLI processes PDFs on the user's device.
