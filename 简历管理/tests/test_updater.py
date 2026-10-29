import io
import json
import shutil
import sys
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "skills" / "manage-hr-resumes" / "scripts"))
import hr_update


SHA = "a" * 40


class Response:
    def __init__(self, value):
        self.value = value
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit=-1):
        return self.value


def archive(path: str, body: bytes = b"# v2\n", include_requirements: bool = True, malicious: bool = False, requirements: str = "openpyxl>=3.1,<4\npypdf>=5,<7\n") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        prefix = f"functional-side-skills-{SHA}/简历管理/skills/manage-hr-resumes/"
        z.writestr(prefix + "SKILL.md", body if path == "SKILL.md" else b"---\nname: manage-hr-resumes\n---\n")
        if include_requirements:
            z.writestr(prefix + "requirements.txt", requirements)
        z.writestr(prefix + "scripts/hr.py", "print('ok')\n")
        z.writestr(prefix + "scripts/hr_update.py", "print('updated')\n")
        z.writestr(prefix + "skills-upstream.json", '{"schema":1,"provider":"github","repository":"TuKJet/functional-side-skills","path":"简历管理/skills/manage-hr-resumes","branch":"main"}\n')
        if malicious:
            z.writestr(prefix + "../escape.txt", "bad")
    return output.getvalue()


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=Path(__file__).parents[1]))
        self.root = self.tmp / "skill"
        source = Path(__file__).parents[1] / "skills" / "manage-hr-resumes"
        shutil.copytree(source, self.root, ignore=shutil.ignore_patterns("__pycache__", ".update", "workspace.local.json"))
        (self.root / "workspace.local.json").write_text('{"root":"business"}', encoding="utf-8")
        (self.root / ".venv").mkdir()
        hr_update.command_init(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake(self, url):
        if "api.github.com" in url:
            return Response(json.dumps([{"sha": SHA}]).encode())
        return Response(archive("scripts/hr.py"))

    def test_update_preserves_local_config_and_supports_rollback(self):
        with patch.object(hr_update, "_urlopen", self.fake):
            result = hr_update.command_update(self.root)
        self.assertTrue(result["updated"])
        self.assertEqual((self.root / "workspace.local.json").read_text(), '{"root":"business"}')
        self.assertTrue((self.root / ".venv").is_dir())
        self.assertEqual((self.root / "scripts/hr_update.py").read_text(), "print('updated')\n")
        rollback = hr_update.command_rollback(self.root, result["backup"])
        self.assertTrue(rollback["rolled_back"])

    def test_local_changes_are_not_overwritten(self):
        (self.root / "SKILL.md").write_text("local", encoding="utf-8")
        with patch.object(hr_update, "_urlopen", self.fake), self.assertRaises(hr_update.UpdateError) as ctx:
            hr_update.command_update(self.root)
        self.assertEqual(ctx.exception.code, "local_changes")

    def test_corrupt_download_does_not_change_install(self):
        before = (self.root / "SKILL.md").read_bytes()
        def corrupt(url):
            return Response(json.dumps([{"sha": SHA}]).encode()) if "api.github.com" in url else Response(b"not zip")
        with patch.object(hr_update, "_urlopen", corrupt), self.assertRaises(hr_update.UpdateError) as ctx:
            hr_update.command_update(self.root)
        self.assertEqual(ctx.exception.code, "invalid_download")
        self.assertEqual((self.root / "SKILL.md").read_bytes(), before)

    def test_init_is_idempotent_and_ignores_cache(self):
        cache = self.root / "scripts" / "__pycache__"
        cache.mkdir()
        (cache / "fake.pyc").write_bytes(b"cache")
        result = hr_update.command_init(self.root)
        self.assertFalse(result["initialized"])
        self.assertNotIn("scripts/__pycache__/fake.pyc", json.loads((self.root / ".update/manifest.json").read_text())["files"])

    def test_bad_backup_and_local_rollback_modification_are_rejected(self):
        with patch.object(hr_update, "_urlopen", self.fake):
            result = hr_update.command_update(self.root)
        outside = self.tmp / "outside"
        outside.mkdir()
        with self.assertRaises(hr_update.UpdateError) as bad:
            hr_update.command_rollback(self.root, str(outside))
        self.assertEqual(bad.exception.code, "invalid_backup")
        (self.root / "SKILL.md").write_text("changed", encoding="utf-8")
        with self.assertRaises(hr_update.UpdateError) as local:
            hr_update.command_rollback(self.root, result["backup"])
        self.assertEqual(local.exception.code, "local_changes")

    def test_malicious_archive_and_missing_requirements_are_rejected(self):
        def malicious(url):
            return Response(json.dumps([{"sha": SHA}]).encode()) if "api.github.com" in url else Response(archive("SKILL.md", malicious=True))
        with patch.object(hr_update, "_urlopen", malicious), self.assertRaises(hr_update.UpdateError) as bad:
            hr_update.command_update(self.root)
        self.assertEqual(bad.exception.code, "invalid_download")

        def no_requirements(url):
            return Response(json.dumps([{"sha": SHA}]).encode()) if "api.github.com" in url else Response(archive("SKILL.md", include_requirements=False))
        with patch.object(hr_update, "_urlopen", no_requirements), self.assertRaises(hr_update.UpdateError) as missing:
            hr_update.command_update(self.root)
        self.assertEqual(missing.exception.code, "upstream_missing")

    def test_replace_failure_restores_original_files(self):
        before = (self.root / "SKILL.md").read_bytes()
        real_replace = hr_update.os.replace
        calls = {"count": 0}
        def fail_once(source, target):
            calls["count"] += 1
            if calls["count"] == 3:
                raise OSError("injected replace failure")
            return real_replace(source, target)
        with patch.object(hr_update, "_urlopen", self.fake), patch.object(hr_update.os, "replace", fail_once), self.assertRaises(hr_update.UpdateError) as failed:
            hr_update.command_update(self.root)
        self.assertEqual(failed.exception.code, "update_failed")
        self.assertEqual((self.root / "SKILL.md").read_bytes(), before)

    def test_requirements_crlf_bom_matches_upstream_lf(self):
        installed = b"\xef\xbb\xbfopenpyxl>=3.1,<4\r\npypdf>=5,<7\r\n"
        (self.root / "requirements.txt").write_bytes(installed)
        self.assertEqual(hr_update._requirements_content(self.root / "requirements.txt"), "openpyxl>=3.1,<4\npypdf>=5,<7\n")
        state_path = self.root / ".update" / "manifest.json"
        state_path.write_text(json.dumps(hr_update._manifest(self.root), ensure_ascii=False), encoding="utf-8")
        with patch.object(hr_update, "_urlopen", self.fake):
            result = hr_update.command_update(self.root)
        self.assertTrue(result["updated"])

    def test_requirements_version_change_is_rejected(self):
        def changed(url):
            return Response(json.dumps([{"sha": SHA}]).encode()) if "api.github.com" in url else Response(archive("scripts/hr.py", requirements="openpyxl>=3.2,<4\npypdf>=5,<7\n"))
        with patch.object(hr_update, "_urlopen", changed), self.assertRaises(hr_update.UpdateError) as failed:
            hr_update.command_update(self.root)
        self.assertEqual(failed.exception.code, "dependency_change")

    def test_pending_transaction_recovers_missing_manifest_and_rejects_extra_backup(self):
        with patch.object(hr_update, "_urlopen", self.fake):
            result = hr_update.command_update(self.root)
        backup = Path(result["backup"])
        current = self.root / ".update" / "manifest.json"
        current.unlink()
        hr_update._atomic_json(self.root / ".update" / "transaction.json", {
            "schema": 1, "backup": str(backup), "old_manifest": json.loads((backup / "manifest.json").read_text()), "target_commit": SHA
        })
        with self.assertRaises(hr_update.UpdateError) as missing:
            hr_update.command_check(self.root)
        self.assertEqual(missing.exception.code, "pending_transaction")
        (backup / "scripts" / "extra.py").write_text("unexpected", encoding="utf-8")
        with self.assertRaises(hr_update.UpdateError) as extra:
            hr_update.command_rollback(self.root, str(backup))
        self.assertEqual(extra.exception.code, "invalid_backup")

    def test_rollback_failure_keeps_journal_and_retry_succeeds(self):
        with patch.object(hr_update, "_urlopen", self.fake):
            result = hr_update.command_update(self.root)
        original = hr_update._copy_tree
        calls = {"count": 0}
        def fail_once(source, root):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("injected rollback failure")
            return original(source, root)
        with patch.object(hr_update, "_copy_tree", fail_once), self.assertRaises(hr_update.UpdateError) as failed:
            hr_update.command_rollback(self.root, result["backup"])
        self.assertEqual(failed.exception.code, "rollback_failed")
        self.assertTrue((self.root / ".update" / "transaction.json").exists())
        self.assertTrue(hr_update.command_rollback(self.root, None)["rolled_back"])

    def test_backup_script_recovers_deleted_current_updater_and_manifest(self):
        with patch.object(hr_update, "_urlopen", self.fake):
            result = hr_update.command_update(self.root)
        backup = Path(result["backup"])
        (self.root / "scripts" / "hr_update.py").unlink()
        (self.root / ".update" / "manifest.json").unlink()
        completed = subprocess.run(
            [sys.executable, str(backup / "scripts" / "hr_update.py"), "rollback", "--skill-root", str(self.root), "--backup", str(backup)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        self.assertEqual(completed.returncode, 0, (completed.stdout or "") + (completed.stderr or ""))
        self.assertTrue((self.root / "scripts" / "hr_update.py").exists())
        self.assertTrue((self.root / ".update" / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
