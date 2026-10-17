"""Shared PDF-to-Markdown conversion helpers.

The command-line entry point and the remote MCP server both use this module so
that conversion behaviour stays identical in local and hosted deployments.
"""

from __future__ import annotations

import re
from pathlib import Path


def safe_stem(stem: str) -> str:
    """Keep output names portable across common filesystems."""

    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" .")
    return cleaned or "converted-document"


def output_path(output_dir: Path, stem: str) -> Path:
    """Return a non-overwriting Markdown path in *output_dir*."""

    candidate = output_dir / f"{safe_stem(stem)}.md"
    suffix = 2
    while candidate.exists():
        candidate = output_dir / f"{safe_stem(stem)}-{suffix}.md"
        suffix += 1
    return candidate


def convert_one(source: Path, output_dir: Path) -> Path:
    """Convert one local PDF and write its Markdown output."""

    if not source.is_file():
        raise ValueError(f"Input file does not exist or is not a regular file: {source}")
    if source.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a PDF file: {source}")

    # Import lazily so --help works before MarkItDown is installed.
    from markitdown import MarkItDown

    result = MarkItDown(enable_plugins=False).convert_local(str(source))
    markdown = result.markdown
    if not markdown.strip():
        raise ValueError(f"No Markdown text was extracted from {source.name}")

    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_path(output_dir, source.stem)
    destination.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return destination
