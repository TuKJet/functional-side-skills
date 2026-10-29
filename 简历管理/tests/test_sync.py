import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "skills" / "manage-hr-resumes" / "scripts"))
from hr_resume.service import Service
from hr_resume.store import Store
from hr_resume.sync import synchronize, sync_status
import hr_resume.sync as sync_module


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        root = Path(self.tmp.name)
        self.local, self.remote = root / "local", root / "remote"
        self.source = root / "resume.txt"
        self.source.write_text("Python backend", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _candidate(self, root, name):
        service = Service(root, "hr")
        if not (Path(root) / "catalog.json").exists():
            service.execute("init")
        return service.execute("candidate-add", {"name": name, "resume_path": str(self.source)})

    def test_remote_requires_explicit_initialization_and_then_syncs(self):
        self._candidate(self.local, "本地候选人")
        with self.assertRaises(ValueError):
            synchronize(Store(self.local, "hr"), self.remote)
        result = synchronize(Store(self.local, "hr"), self.remote, initialize_remote=True)
        self.assertTrue(result["synced"])
        self.assertEqual(len(Store(self.remote, "hr").snapshot()["candidates"]), 1)
        self.assertTrue(sync_status(Store(self.local, "hr"))["synced"])

    def test_same_record_conflict_keeps_both_sides(self):
        candidate = self._candidate(self.local, "原始")
        synchronize(Store(self.local, "hr"), self.remote, initialize_remote=True)
        Service(self.local, "local").execute("candidate-update", {"id": candidate["id"], "expected_version": 1, "name": "本地修改"})
        Service(self.remote, "remote").execute("candidate-update", {"id": candidate["id"], "expected_version": 1, "name": "远端修改"})
        result = synchronize(Store(self.local, "hr"), self.remote, initialize_remote=True)
        self.assertFalse(result["synced"])
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(Store(self.local, "hr").snapshot()["candidates"][0]["name"], "本地修改")
        self.assertEqual(Store(self.remote, "hr").snapshot()["candidates"][0]["name"], "远端修改")

    def test_conflict_resolution_creates_new_version_and_checks_sha(self):
        candidate = self._candidate(self.local, "原始")
        synchronize(Store(self.local, "hr"), self.remote, initialize_remote=True)
        Service(self.local, "local").execute("candidate-update", {"id": candidate["id"], "expected_version": 1, "name": "本地修改"})
        Service(self.remote, "remote").execute("candidate-update", {"id": candidate["id"], "expected_version": 1, "name": "远端修改"})
        conflict = synchronize(Store(self.local, "hr"), self.remote)["conflicts"][0]
        with self.assertRaises(ValueError):
            synchronize(Store(self.local, "hr"), self.remote, resolutions=[{"kind": "candidates", "id": candidate["id"], "take": "remote", "remote_sha256": "expired"}])
        result = synchronize(Store(self.local, "hr"), self.remote, resolutions=[{"kind": "candidates", "id": candidate["id"], "take": "remote", "local_sha256": conflict['local']['sha256'], "remote_sha256": conflict["remote"]["sha256"]}])
        self.assertTrue(result["synced"])
        self.assertEqual(Store(self.local, "hr").snapshot()["candidates"][0]["version"], 3)
        self.assertEqual(Store(self.local, "hr").snapshot()["candidates"][0]["name"], "远端修改")
        with self.assertRaises(ValueError):
            Service(self.local, "hr").execute("candidate-update", {"id": candidate["id"], "expected_version": 2, "name": "旧版本"})

    def test_disjoint_changes_merge_and_download_empty_local(self):
        c1 = self._candidate(self.local, "一号")
        synchronize(Store(self.local, "hr"), self.remote, initialize_remote=True)
        c2 = self._candidate(self.local, "二号")
        result = synchronize(Store(self.local, "hr"), self.remote, initialize_remote=True)
        self.assertTrue(result["synced"])
        self.assertEqual(len(Store(self.remote, "hr").snapshot()["candidates"]), 2)
        empty = Path(self.tmp.name) / "empty"
        Service(empty, "new").execute("init")
        result = synchronize(Store(empty, "new"), self.remote)
        self.assertTrue(result["synced"])
        self.assertEqual(len(Store(empty, "new").snapshot()["candidates"]), 2)

    def test_path_escape_is_rejected_and_lock_is_recoverable(self):
        local = Store(self.local, "hr")
        with self.assertRaises(ValueError):
            local.safe("../outside")
        self._candidate(self.local, "一号")
        self.remote.mkdir()
        (self.remote / ".write.lock").mkdir()
        with self.assertRaises(ValueError):
            synchronize(local, self.remote, initialize_remote=True)
        (self.remote / ".write.lock").rmdir()

    def test_retry_after_publish_interruption(self):
        self._candidate(self.local, "可重试")
        original = sync_module._publish
        calls = [0]
        def interrupted(store, refs, revision):
            calls[0] += 1
            if calls[0] == 2:
                raise OSError("模拟远端发布中断")
            return original(store, refs, revision)
        sync_module._publish = interrupted
        try:
            with self.assertRaises(OSError):
                synchronize(Store(self.local, "hr"), self.remote, initialize_remote=True)
        finally:
            sync_module._publish = original
        result = synchronize(Store(self.local, "hr"), self.remote, initialize_remote=True)
        self.assertTrue(result["synced"])
        self.assertEqual(len(Store(self.remote, "hr").snapshot()["candidates"]), 1)

    def test_three_clients_share_hub_and_merge_different_records(self):
        a = self._candidate(self.local, 'A候选人')
        synchronize(Store(self.local, 'A'), self.remote, initialize_remote=True)
        roots = [Path(self.tmp.name) / 'clientB', Path(self.tmp.name) / 'clientC']
        for root in roots:
            Service(root, root.name).execute('init')
            synchronize(Store(root, root.name), self.remote)
        b = self._candidate(roots[0], 'B候选人')
        c = self._candidate(roots[1], 'C候选人')
        Service(self.local, 'A').execute('candidate-update', dict(id=a['id'], expected_version=1, notes='A独立修改'))
        for root in [roots[0], roots[1], self.local, roots[0], roots[1]]:
            self.assertTrue(synchronize(Store(root, '同事'), self.remote)['synced'])
        for root in [*roots, self.local, self.remote]:
            self.assertEqual({x['id'] for x in Store(root, '测试').snapshot()['candidates']}, {a['id'], b['id'], c['id']})
        self.assertFalse((self.remote / '.sync').exists())

    def test_remote_change_immediately_before_lock_is_not_lost(self):
        a = self._candidate(self.local, '待同步')
        synchronize(Store(self.local, 'A'), self.remote, initialize_remote=True)
        original_lock = Store.lock
        injected = False
        @contextmanager
        def racing_lock(store):
            nonlocal injected
            if store.root == self.remote.resolve() and not injected:
                injected = True
                Service(self.remote, '刚好提交的同事').execute('candidate-update', dict(id=a['id'], expected_version=1, notes='不得丢失'))
            with original_lock(store):
                yield
        with patch.object(Store, 'lock', racing_lock):
            synchronize(Store(self.local, 'A'), self.remote)
        self.assertEqual(Store(self.local, 'A').snapshot()['candidates'][0]['notes'], '不得丢失')

    def test_decision_must_match_both_versions(self):
        a = self._candidate(self.local, '原始')
        synchronize(Store(self.local, 'A'), self.remote, initialize_remote=True)
        for root, note in [(self.local, '本地'), (self.remote, '远端')]:
            Service(root, note).execute('candidate-update', dict(id=a['id'], expected_version=1, notes=note))
        conflict = synchronize(Store(self.local, 'A'), self.remote)['conflicts'][0]
        self.assertEqual(conflict['local_summary']['notes'], '本地')
        decision = dict(kind='candidates', id=a['id'], take='remote', local_sha256=conflict['local']['sha256'], remote_sha256=conflict['remote']['sha256'])
        Service(self.local, 'A').execute('candidate-update', dict(id=a['id'], expected_version=2, notes='选择期间又改了'))
        with self.assertRaisesRegex(ValueError, 'SHA'):
            synchronize(Store(self.local, 'A'), self.remote, resolutions=[decision])

    def test_offline_does_not_create_remote_directory(self):
        self._candidate(self.local, '离线')
        with self.assertRaisesRegex(ValueError, '不可用'):
            synchronize(Store(self.local, 'A'), self.remote)
        self.assertFalse(self.remote.exists())


if __name__ == "__main__":
    unittest.main()
