from __future__ import annotations

import re
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from PIL import Image


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "generate_report.py"


class GenerateProcessReportTests(unittest.TestCase):
    def make_fixture(self, root: Path, include_display: bool = True) -> Path:
        media = root / "nested" / "deeper"
        media.mkdir(parents=True)
        for index in range(1, 13):
            Image.new("RGB", (180 + index, 120), (index * 12, 80, 150)).save(media / f"sample-{index}.jpg")
        if include_display:
            Image.new("RGB", (240, 150), (0, 180, 210)).save(media / "display-overview.jpg")

        headers = [f"字段{index}" for index in range(1, 32)]
        headers[2] = "批次编号"
        headers[5] = "工步"
        headers[6] = "工步描述"
        headers[8] = "设备参数详情"
        headers[23] = "产品规格"
        headers[27] = "文件绝对路径"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "GOM"
        sheet.append(headers)

        row_without_images = [None] * 31
        row_without_images[2] = "BATCH-001"
        row_without_images[5] = "10.10"
        row_without_images[6] = "清洗"
        row_without_images[8] = '{"温度":"25℃","循环":{"次数":2,"模式":"标准"}}'
        row_without_images[23] = "INTERNAL-SECRET-SPEC-A"
        row_without_images[27] = None
        sheet.append(row_without_images)

        row_with_images = [None] * 31
        row_with_images[2] = "BATCH-001"
        row_with_images[5] = "10.20"
        row_with_images[6] = "曝光"
        row_with_images[8] = '{"曝光时间":"12s","能量":150}'
        row_with_images[23] = "INTERNAL-SECRET-SPEC-B"
        row_with_images[27] = str(root / "nested")
        sheet.append(row_with_images)

        path = root / "fixture.xlsx"
        workbook.save(path)
        return path

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env=environment,
        )

    def test_markdown_preserves_row_order_and_omits_empty_ab_gallery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = self.make_fixture(root)
            output = root / "report.md"
            result = self.run_script(str(workbook), "--output", str(output), "--max-images", "10")
            self.assertEqual(result.returncode, 0, result.stderr)
            content = output.read_text(encoding="utf-8")
            first = content.index("## 01 · 清洗")
            second = content.index("## 02 · 曝光")
            self.assertLess(first, second)
            self.assertNotIn("### 样品图片", content[first:second])
            self.assertIn("循环.次数", content)
            self.assertIn("display-overview.jpg", content)
            self.assertNotIn("产品规格", content)
            self.assertNotIn("INTERNAL-SECRET-SPEC", content)
            self.assertEqual(content.count("!["), 1)
            self.assertNotIn("sample-", content)
            assets = root / "report_assets"
            self.assertTrue(assets.is_dir())
            self.assertEqual(len(list(assets.glob("*.jpg"))), content.count("![") * 2)

    def test_html_requires_explicit_post_markdown_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = self.make_fixture(root)
            blocked = root / "blocked.html"
            result = self.run_script(str(workbook), "--output", str(blocked), "--format", "html")
            self.assertEqual(result.returncode, 2)
            self.assertIn("门禁阻止", result.stderr)
            self.assertFalse(blocked.exists())

            approved = root / "approved.html"
            result = self.run_script(
                str(workbook),
                "--output",
                str(approved),
                "--format",
                "html",
                "--approved-html",
                "--max-images",
                "3",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            content = approved.read_text(encoding="utf-8")
            self.assertIn("data:image/jpeg;base64,", content)
            self.assertNotRegex(content, re.compile(r"(?:src|href)=[\"']https?://", re.I))
            self.assertIn("display-overview.jpg", content)
            self.assertNotIn("产品规格", content)
            self.assertNotIn("INTERNAL-SECRET-SPEC", content)
            self.assertEqual(content.count('class="image-card"'), 1)
            self.assertNotIn("sample-", content)
            self.assertIn('href="#process-001"', content)
            self.assertIn('id="process-001"', content)
            self.assertIn("按“工步描述”快速定位", content)
            self.assertIn('id="nav-toggle"', content)
            self.assertIn('aria-expanded="false"', content)
            self.assertIn('class="nav-list" id="process-nav"', content)
            self.assertNotIn('id="process-nav" hidden', content)
            self.assertIn("max-height:60px", content)
            self.assertIn("nav.classList.toggle('expanded',expanded)", content)
            self.assertNotIn("overflow-x:auto", content)
            self.assertLess(content.index(">清洗</b></a>"), content.index(">曝光</b></a>"))

    def test_without_display_images_fills_requested_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = self.make_fixture(root, include_display=False)
            output = root / "ordinary.md"
            result = self.run_script(str(workbook), "--output", str(output), "--max-images", "3")
            self.assertEqual(result.returncode, 0, result.stderr)
            content = output.read_text(encoding="utf-8")
            self.assertEqual(content.count("!["), 3)
            self.assertIn("sample-", content)


if __name__ == "__main__":
    unittest.main()
