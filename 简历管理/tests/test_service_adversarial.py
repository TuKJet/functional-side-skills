"""Read-only reviewer probes for service invariants (synthetic files only)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "skills" / "manage-hr-resumes" / "scripts"))
from hr_resume.service import Service


class ServiceAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.tmp.name) / "pool"
        self.source = Path(self.tmp.name) / "resume.txt"
        self.source.write_text("Python 后端工程师，五年经验", encoding="utf-8")
        self.s = Service(self.root, "审查员")
        self.s.execute("init")
        self.c = self.s.execute("candidate-add", {"name": "候选人", "resume_path": str(self.source)})
        self.j = self.s.execute("jd-put", {"department": "研发", "title": "后端", "month": "2026-10", "description": "后端开发", "criteria": [{"id": "py", "label": "Python", "weight": 1, "required": True}]})
        self.a = self.s.execute("application-add", {"candidate_id": self.c["id"], "job_id": self.j["id"]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_optimistic_application_conflict(self):
        first = self.s.execute("application-update", {"id": self.a["id"], "expected_version": 1, "status": "初面"})
        self.assertEqual(first["version"], 2)
        with self.assertRaises(ValueError):
            self.s.execute("application-update", {"id": self.a["id"], "expected_version": 1, "status": "二面"})

    def test_queue_blocks_non_searchable_resume(self):
        # A non-text source is retained but must not enter automated review.
        image = Path(self.tmp.name) / "resume.png"
        image.write_bytes(b"not an image")
        s = Service(Path(self.tmp.name) / "imagepool", "审查员")
        s.execute("init")
        c = s.execute("candidate-add", {"name": "图像候选人", "resume_path": str(image)})
        j = s.execute("jd-put", {"department": "研发", "title": "后端", "month": "2026-10", "description": "后端开发", "criteria": [{"id": "x", "label": "Python", "weight": 1, "required": True}]})
        s.execute("application-add", {"candidate_id": c["id"], "job_id": j["id"]})
        queue = s.execute("review-queue")
        self.assertEqual(queue["items"], [])
        self.assertIn("缺少可审核文本", queue["blocked"][0]["reason"])

    def test_review_rejects_unproven_rating_and_ranking_keeps_pending(self):
        queue = self.s.execute("review-queue")["items"][0]
        self.assertRaises(ValueError, self.s.execute, "review-submit", {"application_id": self.a["id"], "application_version": queue["application_version"], "job_version": queue["job_version"], "resume_sha256": queue["resume_sha256"], "text_sha256": queue["text_sha256"], "reviewer": "Agent", "criteria": [{"criterion_id": "py", "rating": 4, "evidence": "不存在的证据", "reason": "看起来符合"}]})
        result = self.s.execute("review-submit", {"application_id": self.a["id"], "application_version": queue["application_version"], "job_version": queue["job_version"], "resume_sha256": queue["resume_sha256"], "text_sha256": queue["text_sha256"], "reviewer": "Agent", "criteria": [{"criterion_id": "py", "rating": None, "evidence": "", "reason": "简历没有足够证据"}]})
        self.assertIsNone(result["score"])
        self.assertEqual(self.s.execute("ranking")["groups"][0]["pending"][0]["application_id"], self.a["id"])


if __name__ == "__main__":
    unittest.main()
