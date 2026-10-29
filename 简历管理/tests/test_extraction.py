import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "skills" / "manage-hr-resumes" / "scripts"))
from hr_resume.extraction import extract_text


class ExtractionTests(unittest.TestCase):
    def test_text_and_unsupported(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            txt = root / "a.txt"
            txt.write_text("姓名\n工程师", encoding="utf-8")
            self.assertEqual(extract_text(txt)["text_status"], "ready")
            self.assertIn("工程师", extract_text(txt)["text"])
            self.assertEqual(extract_text(root / "x.jpg")["text_status"], "error")

    def test_docx_xml_is_extracted_without_office(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "a.docx"
            xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>候选人</w:t></w:r></w:p></w:body></w:document>'
            with ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", xml)
            result = extract_text(path)
            self.assertEqual(result["text_status"], "ready")
            self.assertEqual(result["text"], "候选人")

    def test_oversize_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "large.txt"
            with path.open("wb") as handle:
                handle.truncate(25 * 1024 * 1024 + 1)
            self.assertEqual(extract_text(path)["text_status"], "error")

    def test_docx_entry_count_limit(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "many.docx"
            with ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'/>")
                for index in range(201):
                    archive.writestr(f"extra/{index}.txt", "x")
            result = extract_text(path)
            self.assertEqual(result["text_status"], "error")


if __name__ == "__main__":
    unittest.main()
