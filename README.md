# PDF2MD

Convert PDF files into Markdown with Microsoft's [MarkItDown](https://github.com/microsoft/markitdown).

## What it does today

PDF2MD currently has three ways to run the same local-file converter:

- **ChatGPT Work plugin:** provides a reusable workflow for converting PDFs that are available as local files to the Work execution environment. It returns one `.md` file per PDF.
- **Python CLI:** converts one or more local PDFs and can optionally publish the generated Markdown files as GitHub Release assets.
- **GitHub Actions workflow:** manually converts PDFs found in `pdfs/` and publishes the Markdown files as a GitHub Release.

## ChatGPT integration

This repository contains a GitHub-importable ChatGPT plugin marketplace with a PDF conversion skill. A workspace admin can import the marketplace from GitHub and install or share the plugin within that workspace. The repository can be public or private; the importing GitHub account must be able to read it.

This is a **plugin with a skill**, not a hosted MCP connector. The skill runs the Python script where it is installed. Making this repository public does not deploy a server or give ChatGPT cloud chats direct access to attached PDF bytes. In Work, the PDF must be available as a local file to the execution environment.

To provide a remote `@pdf2md` connector that receives PDFs attached in chat and returns downloadable Markdown files, this project still needs a hosted MCP server with a public HTTPS endpoint and a tool for accepting the PDF input and returning the converted file.

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

## Run the converter locally

Requirements: Python and `markitdown[pdf]`.

```bash
python -m pip install -r requirements.txt
python skills/convert-pdf-to-markdown/scripts/convert_pdf.py /path/to/one.pdf /path/to/two.pdf
```

Markdown files are written to `converted-markdown/`. Choose another folder with `--output-dir`:

```bash
python skills/convert-pdf-to-markdown/scripts/convert_pdf.py report.pdf --output-dir ./md
```

## Publish Markdown as GitHub Release assets

The CLI can create a uniquely tagged GitHub Release and upload only the generated Markdown files:

```bash
python skills/convert-pdf-to-markdown/scripts/convert_pdf.py report.pdf \
  --output-dir converted-markdown \
  --release-repo OWNER/REPO
```

The GitHub CLI (`gh`) must be installed and authenticated with permission to publish a release in the selected repository. The script does not upload the original PDFs.

## Run the GitHub Actions workflow

1. Add PDFs to `pdfs/`.
2. Open **Actions → Convert PDFs to Markdown → Run workflow**.
3. Open the workflow summary to get the Release link and Markdown asset links.

**Privacy:** PDFs added to `pdfs/` are committed to the repository. In a public repository, those PDFs are visible to everyone. Use only documents you intend to make public, or run the converter locally / use a private input repository. In a public repository, the generated Markdown Release assets are also public.

## Current limits

- PDF input only; one Markdown output per PDF.
- Uses MarkItDown for extraction; no OCR service or LLM-based rewriting is added by this project.
- The CLI and GitHub Action operate on repository or local file paths. There is no hosted MCP conversion endpoint yet.
