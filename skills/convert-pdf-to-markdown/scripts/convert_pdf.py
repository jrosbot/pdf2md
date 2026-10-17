#!/usr/bin/env python3
"""Convert local PDF files to Markdown with Microsoft MarkItDown."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from uuid import uuid4


def safe_stem(stem: str) -> str:
    """Keep output names portable across common filesystems."""
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" .")
    return cleaned or "converted-document"


def output_path(output_dir: Path, stem: str) -> Path:
    candidate = output_dir / f"{safe_stem(stem)}.md"
    suffix = 2
    while candidate.exists():
        candidate = output_dir / f"{safe_stem(stem)}-{suffix}.md"
        suffix += 1
    return candidate


def convert_one(source: Path, output_dir: Path) -> Path:
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

    destination = output_path(output_dir, source.stem)
    destination.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return destination


def publish_release(files: Sequence[Path], repository: str) -> tuple[str, list[str]]:
    gh = shutil.which("gh")
    if gh is None:
        raise RuntimeError("GitHub CLI 'gh' is required to publish release assets.")

    tag = "pdf-to-markdown-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    title = "PDF to Markdown " + tag.removeprefix("pdf-to-markdown-")
    command = [
        gh,
        "release",
        "create",
        tag,
        *(str(path) for path in files),
        "--repo",
        repository,
        "--title",
        title,
        "--notes",
        "Markdown files converted from PDFs.",
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown GitHub CLI error"
        raise RuntimeError(f"Could not publish the GitHub Release: {detail}")

    release_url = completed.stdout.strip().splitlines()[-1]
    asset_urls = [
        f"https://github.com/{repository}/releases/download/{tag}/{path.name}"
        for path in files
    ]
    return release_url, asset_urls


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", nargs="+", type=Path, help="PDF files to convert")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("converted-markdown"),
        help="Directory for generated .md files (default: converted-markdown)",
    )
    parser.add_argument(
        "--release-repo",
        metavar="OWNER/REPO",
        help="Create a GitHub Release in this repository and upload the Markdown files",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    converted: list[Path] = []
    failures: list[str] = []
    for raw_path in args.pdfs:
        source = raw_path.expanduser().resolve()
        try:
            destination = convert_one(source, output_dir)
            converted.append(destination)
            print(f"CONVERTED\t{source.name}\t{destination}")
        except Exception as exc:  # report per-file failures without losing successful conversions
            failures.append(f"{source.name}: {exc}")
            print(f"FAILED\t{source.name}\t{exc}", file=sys.stderr)

    if args.release_repo and converted:
        try:
            release_url, asset_urls = publish_release(converted, args.release_repo)
            print(f"RELEASE\t{release_url}")
            for path, url in zip(converted, asset_urls, strict=True):
                print(f"ASSET\t{path.name}\t{url}")
        except Exception as exc:
            failures.append(f"GitHub Release: {exc}")

    if failures:
        print("\nErrors:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
