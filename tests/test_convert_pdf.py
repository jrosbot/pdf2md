import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "skills" / "convert-pdf-to-markdown" / "scripts" / "convert_pdf.py"
SPEC = importlib.util.spec_from_file_location("convert_pdf", SCRIPT)
convert_pdf = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(convert_pdf)


class ConvertPdfTests(unittest.TestCase):
    def test_safe_stem_removes_nonportable_characters(self):
        self.assertEqual(convert_pdf.safe_stem("terms: 2026/10"), "terms_ 2026_10")

    def test_output_path_avoids_overwriting_existing_markdown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "report.md").write_text("previous", encoding="utf-8")
            self.assertEqual(convert_pdf.output_path(output_dir, "report").name, "report-2.md")

    def test_convert_one_writes_markitdown_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "sample.pdf"
            source.write_bytes(b"test pdf bytes")
            output_dir = root / "out"
            output_dir.mkdir()

            fake_module = types.SimpleNamespace(
                MarkItDown=lambda **kwargs: types.SimpleNamespace(
                    convert_local=lambda path: types.SimpleNamespace(markdown="# Sample\n\nText")
                )
            )
            with patch.dict(sys.modules, {"markitdown": fake_module}):
                destination = convert_pdf.convert_one(source, output_dir)

            self.assertEqual(destination.name, "sample.md")
            self.assertEqual(destination.read_text(encoding="utf-8"), "# Sample\n\nText\n")


if __name__ == "__main__":
    unittest.main()
