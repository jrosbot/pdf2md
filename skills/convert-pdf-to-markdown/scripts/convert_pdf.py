#!/usr/bin/env python3
"""Convert local PDF files to Markdown with Microsoft MarkItDown."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

# The script is intentionally executable directly from the repository root.
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pdf2md_core import convert_one, output_path, safe_stem


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
