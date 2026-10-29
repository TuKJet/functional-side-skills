import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hr_client', PROJECT / 'skills/manage-hr-resumes/scripts/hr_client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class ClientDownloadTests(unittest.TestCase):
    def setUp(self):
        (PROJECT / '.runtime').mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=PROJECT / '.runtime')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.skill = self.base / 'skill'
        self.skill.mkdir()
        (self.skill / 'workspace.local.json').write_text(json.dumps({
            'python': 'python3', 'script': 'scripts/hr.py', 'root': str(self.base / 'local'), 'actor': '测试',
            'remote_root': '', 'config_path': str(self.base / 'client-config.json'), 'port': 0
        }), encoding='utf-8')
        self.data = b'verified executable bytes'
        self.asset_name, self.sum_name = client._names('windows')
        digest = hashlib.sha256(self.data).hexdigest()
        self.release = {'tag_name': 'hr-client-v0.3.0-run1', 'draft': False, 'prerelease': False,
                        'assets': [{'name': self.asset_name, 'browser_download_url': 'https://objects.githubusercontent.com/exe'},
                                   {'name': self.sum_name, 'browser_download_url': 'https://objects.githubusercontent.com/sum'}]}

    def fake_request(self, url, max_bytes=client.MAX_RELEASE_BYTES):
        if url.endswith('/releases?per_page=30'):
            return json.dumps([self.release]).encode()
        if url.endswith('/sum'):
            return (hashlib.sha256(self.data).hexdigest() + '  ' + self.asset_name + '\n').encode()
        return self.data

    def test_platform_filter_and_check(self):
        with patch.object(client, '_request', side_effect=self.fake_request):
            result = client._release('windows')
            self.assertEqual(result[0]['tag_name'], 'hr-client-v0.3.0-run1')
            with self.assertRaises(client.ClientError):
                client._release('macos-aarch64')

    def test_download_checksum_and_no_overwrite(self):
        with patch.object(client, '_request', side_effect=self.fake_request), patch.object(client, '_config_path', return_value=self.base / 'cfg.json'), patch.dict(os.environ, {'LOCALAPPDATA': str(self.base / 'appdata')}, clear=False), patch.object(client.sys, 'platform', 'win32'):
            result = client.download(self.skill, 'windows')
            target = Path(result['executable'])
            self.assertEqual(target.read_bytes(), self.data)
            self.assertEqual(json.loads((self.base / 'cfg.json').read_text(encoding='utf-8'))['client_release_tag'], 'hr-client-v0.3.0-run1')
            target.write_bytes(b'local changed')
            with self.assertRaises(client.ClientError):
                client.download(self.skill, 'windows')

    def test_checksum_mismatch_rejected(self):
        def bad(url, max_bytes=client.MAX_RELEASE_BYTES):
            if url.endswith('/releases?per_page=30'):
                return json.dumps([self.release]).encode()
            if url.endswith('/sum'):
                return ('0' * 64 + '  ' + self.asset_name).encode()
            return self.data
        with patch.object(client, '_request', side_effect=bad), self.assertRaises(client.ClientError):
            client.download(self.skill, 'windows')

    def test_api_and_asset_hosts_are_https_allowlisted(self):
        self.assertEqual(client.urllib.parse.urlparse(client.API_ROOT).hostname, 'api.github.com')
        with self.assertRaises(client.ClientError):
            client._request('http://api.github.com/repos/x')
        with self.assertRaises(client.ClientError):
            client._request('https://evil.example/release')

    def test_malformed_tag_and_incomplete_config_rejected(self):
        self.release['tag_name'] = 'hr-client-v0.3.0/../run1'
        with patch.object(client, '_request', side_effect=self.fake_request), self.assertRaises(client.ClientError):
            client._release('windows')
        self.release['tag_name'] = 'hr-client-v0.3.0-run1'
        (self.skill / 'workspace.local.json').write_text(json.dumps({'python': 'python3'}), encoding='utf-8')
        with patch.object(client, '_request', side_effect=self.fake_request), self.assertRaises(client.ClientError):
            client.download(self.skill, 'windows')

    def test_shortcut_rejects_unverified_target(self):
        with patch.object(client.sys, 'platform', 'win32'), self.assertRaises(client.ClientError):
            client.shortcut(self.base / 'not-downloaded.exe')

    def test_previous_hr_client_path_is_safe_shortcut_upgrade_candidate(self):
        clients = self.base / 'appdata' / 'HRResume' / 'clients'
        old = clients / 'hr-client-v0.2.0-run7' / 'hr-resume-windows-x64.exe'
        self.assertTrue(client.VERSION_RE.fullmatch(old.parent.name))
        self.assertEqual(old.name, 'hr-resume-windows-x64.exe')
        self.assertNotEqual((self.base / 'other' / old.name).parent.name, old.parent.name)

    def test_intel_mac_is_explicitly_unsupported(self):
        with patch.object(client.sys, 'platform', 'darwin'), patch.object(client.platform, 'machine', return_value='x86_64'):
            with self.assertRaisesRegex(client.ClientError, 'Intel'):
                client._platform()
        with self.assertRaisesRegex(client.ClientError, '不支持'):
            client._names('macos-x86_64')


if __name__ == '__main__':
    unittest.main()
