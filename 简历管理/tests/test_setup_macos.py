import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('setup_macos', PROJECT / 'scripts/setup_macos.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class MacSetupSafetyTests(unittest.TestCase):
    def setUp(self):
        runtime = PROJECT / '.runtime'
        runtime.mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=runtime)
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.skill = self.base / 'skill'
        self.local = self.base / 'local'
        self.config = self.base / 'settings/client-config.json'

    def test_reject_overlapping_data_and_shared_hub(self):
        with self.assertRaises(ValueError):
            setup.validate_paths(self.skill, self.local, self.base, self.config, False)
        with self.assertRaises(ValueError):
            setup.validate_paths(self.skill, self.skill / 'data', None, self.config, False)
        self.assertFalse(self.skill.exists())

    def test_reject_source_target_existing_target_and_app_config(self):
        for skill, config in ((setup.SOURCE / 'installed', self.config),
                              (self.skill, self.base / 'Client.app/config.json')):
            with self.assertRaises(ValueError):
                setup.validate_paths(skill, self.local, None, config, False)
        self.skill.mkdir()
        with self.assertRaises(ValueError):
            setup.validate_paths(self.skill, self.local, None, self.config, False)

    def test_config_roundtrip_and_replacement(self):
        setup.atomic_json(self.config, {'actor': '行政甲', 'root': str(self.local)})
        setup.atomic_json(self.config, {'actor': '行政乙'})
        self.assertEqual(json.loads(self.config.read_text('utf-8')), {'actor': '行政乙'})
        self.assertEqual(list(self.config.parent.glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
