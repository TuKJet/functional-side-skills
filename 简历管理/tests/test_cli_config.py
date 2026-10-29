import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


class ConfiguredCliTests(unittest.TestCase):
    def setUp(self):
        runtime = PROJECT / '.runtime'
        runtime.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='配置 cli ', dir=runtime)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.skill = self.base / '已安装 Skill'
        shutil.copytree(PROJECT / 'skills/manage-hr-resumes/scripts', self.skill / 'scripts',
                        ignore=shutil.ignore_patterns('__pycache__'))
        self.entry = self.skill / 'scripts/hr.py'
        self.root = self.base / '本地副本'
        self.config = self.skill / 'workspace.local.json'

    def configure(self, **extra):
        data = dict(root=str(self.root), actor='行政甲', **extra)
        self.config.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8-sig')

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, str(self.entry), *args], cwd=self.base,
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0 if success else 1, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['ok'], success)
        return value.get('data', value)

    def test_defaults_work_outside_install_directory(self):
        self.configure()
        self.cli('init')
        self.assertTrue(self.cli('doctor')['healthy'])
        self.assertTrue((self.root / 'catalog.json').is_file())

    def test_explicit_flags_override_defaults_without_touching_configured_pool(self):
        self.configure()
        other = self.base / '其他工作副本'
        self.cli('--root', str(other), '--actor', '行政乙', 'init')
        self.assertTrue((other / 'catalog.json').is_file())
        self.assertFalse(self.root.exists())

    def test_explicit_config_and_remote_default(self):
        remote = self.base / '模拟 NAS'
        self.configure(remote_root=str(remote))
        other_config = self.base / '指定配置.json'
        self.config.rename(other_config)
        self.cli('--config', str(other_config), 'init')
        result = self.cli('--config', str(other_config), 'sync', '--payload', '{"initialize_remote":true}')
        self.assertTrue(result['synced'])
        self.assertTrue((remote / 'catalog.json').is_file())

    def test_missing_or_malformed_configuration_is_actionable(self):
        result = self.cli('doctor', success=False)
        self.assertIn('setup.ps1', result['error'])
        self.config.write_text('[]', encoding='utf-8')
        result = self.cli('init', success=False)
        self.assertIn('JSON 对象', result['error'])
        self.assertFalse(self.root.exists())


if __name__ == '__main__':
    unittest.main()
