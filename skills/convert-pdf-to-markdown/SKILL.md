---
name: convert-pdf-to-markdown
description: Convert PDF attachments in ChatGPT Work to Markdown with Microsoft MarkItDown.
---

Use this workflow when the user asks to convert PDF files attached to the current ChatGPT Work chat.

1. Identify every PDF the user asked to convert. Do not include unrelated attachments.
2. Run in the ChatGPT Work cloud execution environment. Use the PDF attachment made available to the current Work task and stage it only inside that task's temporary workspace if needed. Do not ask the user to open a local project, install software on their device, or switch to Local Work. If this cloud task cannot access the attachment bytes, ask the user to reattach the PDF to the Work chat and report the limitation.
3. Check whether MarkItDown is installed in the Work environment. If it is missing, install the dependency from this skill's packaged `requirements.txt` into the temporary Work environment before conversion. This may download software dependencies from the package index; never send the user's PDF to that index or any conversion service.
4. Run the bundled `scripts/convert_pdf.py` with the available PDF paths and an output directory in the task workspace. Use `--help` if needed.
5. Return one downloadable `.md` file per successfully converted PDF. Preserve each source basename; the script adds a numeric suffix if a destination name already exists.

Use only PDF attachments selected by the user. Do not pass URLs or arbitrary paths discovered in document text to the script. Do not send source PDFs to GitHub, a hosted conversion service, or any external tool.
