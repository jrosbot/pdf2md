# PDF2MD

Convert PDF files into Markdown with Microsoft's [MarkItDown](https://github.com/microsoft/markitdown).

## What it does

PDF2MD provides two ways to run the same local-file converter:

- **ChatGPT Work plugin:** converts PDFs available as local files to the Work execution environment and returns one `.md` file per PDF.
- **Python CLI:** converts one or more local PDFs into Markdown files.

## ChatGPT integration

This repository contains a GitHub-importable ChatGPT plugin marketplace with a PDF conversion skill. A workspace admin can import the marketplace from GitHub and install or share the plugin within that workspace. The repository can be public or private; the importing GitHub account must be able to read it.

This is a **plugin with a skill**, not a hosted MCP connector. The skill runs the Python script where it is installed. Making this repository public does not deploy a server or give ChatGPT cloud chats direct access to attached PDF bytes. In Work, the PDF must be available as a local file to the execution environment.

To provide a remote `@pdf2md` connector that receives PDFs attached in chat and returns downloadable Markdown files, this project needs a hosted MCP server with a public HTTPS endpoint and a tool for accepting the PDF input and returning the converted file.

### Import the Work plugin

A workspace admin can import the marketplace from **Admin → Plugins → Add → Import marketplace**:

1. Set **Source** to `https://github.com/jrosbot/pdf2md`.
2. Leave **Path** empty; the marketplace manifest is at `.agents/plugins/marketplace.json`.
3. Choose `main`, import the marketplace, and review the plugin's installation policy.
4. Install `pdf2md` from **Plugins** and invoke it with `@pdf2md` in a new Work chat.

The plugin uses Python and `markitdown[pdf]`. Install the dependency in the Python environment used by the Work execution environment:

```bash
python -m pip install -r requirements.txt
```

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

These are preparation notes. The listing values still need to be entered in the submission portal; this README does not populate them.

## Run the converter locally

The CLI works on Ubuntu, macOS, and Windows. It converts files on your computer; PDF2MD does not upload your PDFs. Installing the Python dependencies requires an internet connection and downloads the required packages.

First, get the repository by cloning it with Git or downloading and extracting the repository ZIP. Then open a terminal in the `pdf2md` folder.

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
- The CLI and Work plugin operate on local files. There is no hosted MCP conversion endpoint.
