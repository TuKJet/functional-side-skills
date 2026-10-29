"""Real loopback server checks; build desktop first. Requires local socket access."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
EXE = Path(os.environ.get('HR_TEST_EXE', str(PROJECT / 'desktop/target/debug/hr-resume-viewer.exe')))


@unittest.skipUnless(os.environ.get('HR_WEB_HTTP_TESTS') == '1', 'Set HR_WEB_HTTP_TESTS=1 after cargo build for loopback integration checks')
class WebServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime = PROJECT / '.runtime'
        runtime.mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='http test ', dir=runtime)
        cls.base = Path(cls.temp.name)
        cls.pool = cls.base / 'local pool'
        cls.pool.mkdir()
        (cls.pool / 'sample.txt').write_text('测试原件', encoding='utf-8')
        (cls.pool / 'evil.html').write_text('<script>alert(1)</script>')
        (cls.base / 'outside.txt').write_text('outside')
        script = cls.base / 'large response.py'
        script.write_text('import json\nprint(json.dumps({"ok":True,"data":{"text":"x"*250000}}))\n', encoding='utf-8')
        cls.proc = subprocess.Popen([str(EXE), '--headless', '--python', sys.executable, '--script', str(script), '--root', str(cls.pool),
                                     '--actor', 'HTTP测试', '--config', str(cls.base / 'client.json'), '--port', '0'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        cls.url = cls.proc.stdout.readline().strip()
        if not cls.url.startswith('http://127.0.0.1:'):
            raise RuntimeError('Rust server did not announce its loopback URL')
        cls.client = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        cls.proc.stdout.close()
        cls.proc.stderr.close()
        cls.temp.cleanup()

    def request(self, path, payload=None, headers=None):
        h = {'Content-Type': 'application/json', 'X-HR-Client': '1'}
        h.update(headers or {})
        request = urllib.request.Request(self.url.rstrip('/') + path, data=json.dumps(payload).encode() if payload is not None else None, headers=h)
        try:
            with self.client.open(request, timeout=15) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.headers, exc.read()
            finally:
                exc.close()

    def test_assets_and_utf8(self):
        for path, mime in [('/', 'text/html'), ('/style.css', 'text/css'), ('/app.js', 'javascript')]:
            status, headers, content = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn(mime, headers.get('Content-Type'))
            self.assertTrue(content.decode('utf-8'))

    def test_reject_cross_origin_host_and_missing_header(self):
        for headers in [{'Origin': 'https://example.com'}, {'Host': 'evil.example'}, {'X-HR-Client': ''}]:
            self.assertEqual(self.request('/api/command', {'command': 'snapshot'}, headers)[0], 403)

    def test_large_subprocess_output_drains_without_deadlock(self):
        status, headers, content = self.request('/api/command', {'command': 'snapshot', 'payload': {}})
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(content)['data']['text']), 250000)

    def test_file_boundaries_and_review_denied(self):
        for path in ['../outside.txt', 'evil.html']:
            self.assertEqual(self.request('/api/file?path=' + path)[0], 403)
        status, headers, content = self.request('/api/file?path=sample.txt')
        self.assertEqual(content.decode('utf-8'), '测试原件')
        self.assertFalse(json.loads(self.request('/api/command', {'command': 'review-submit', 'payload': {}})[2])['ok'])


if __name__ == '__main__':
    unittest.main()
