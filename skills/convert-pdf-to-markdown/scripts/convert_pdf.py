#!/usr/bin/env python3
"""Convert local PDF files to Markdown with Microsoft MarkItDown."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Sequence


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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", nargs="+", type=Path, help="PDF files to convert")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("converted-markdown"),
        help="Directory for generated .md files (default: converted-markdown)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    for raw_path in args.pdfs:
        source = raw_path.expanduser().resolve()
        try:
            destination = convert_one(source, output_dir)
            print(f"CONVERTED\t{source.name}\t{destination}")
        except Exception as exc:  # report per-file failures without losing successful conversions
            failures.append(f"{source.name}: {exc}")
            print(f"FAILED\t{source.name}\t{exc}", file=sys.stderr)

    if failures:
        print("\nErrors:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
