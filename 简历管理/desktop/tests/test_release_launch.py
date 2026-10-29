import os
import concurrent.futures
import ctypes
import pathlib
import subprocess
import tempfile
import time
import unittest


def app_windows(pid):
    user32 = ctypes.windll.user32
    found = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_ssize_t)
    def visit(hwnd, _):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(owner))
        title = ctypes.create_unicode_buffer(128)
        user32.GetWindowTextW(ctypes.c_void_p(hwnd), title, 128)
        if owner.value == pid and title.value == '招聘工作台' and user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
            found.append(hwnd)
        return True
    user32.EnumWindows(callback_type(visit), 0)
    return found


class ReleaseLaunchTests(unittest.TestCase):
    def setUp(self):
        if os.name != "nt" or os.environ.get("HR_RELEASE_TESTS") != "1":
            self.skipTest("set HR_RELEASE_TESTS=1 on Windows to run release launch tests")
        self.exe = pathlib.Path(__file__).parents[1] / "target" / "release" / "hr-resume-viewer.exe"
        if not self.exe.exists():
            self.skipTest("build release first")
        self.procs = []

    def close_window(self, process):
        windows = app_windows(process.pid)
        self.assertEqual(len(windows), 1, 'expected an independent native desktop window')
        ctypes.windll.user32.PostMessageW(ctypes.c_void_p(windows[0]), 0x0010, 0, 0)
        self.assertEqual(process.wait(timeout=20), 0)

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5)
            if p.stdout:
                p.stdout.close()
            if p.stderr:
                p.stderr.close()

    def start(self, cfg, extra=()):
        p = subprocess.Popen([str(self.exe), "--config", str(cfg), "--root", str(cfg.parent), "--actor", "test", *extra], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.procs.append(p)
        return p

    def read_url(self, p):
        reader = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        task = reader.submit(p.stdout.readline)
        try:
            line = task.result(timeout=10).strip()
        except concurrent.futures.TimeoutError:
            p.kill()
            p.wait(timeout=5)
            self.fail('release did not print its URL within 10 seconds')
        finally:
            reader.shutdown(wait=True)
        self.assertRegex(line, r"^http://127\.0\.0\.1:\d+/$")
        return line

    def launch(self, cfg, extra=()):
        p = self.start(cfg, extra)
        return p, self.read_url(p)

    def test_reuse_restart_and_headless_isolation(self):
        runtime = pathlib.Path(__file__).resolve().parents[2] / '.runtime'
        runtime.mkdir(exist_ok=True)
        # Keep diagnostic profiles under ignored .runtime: the shared WebView2
        # runtime may hold cache handles after our application process has exited.
        cfg = pathlib.Path(tempfile.mkdtemp(prefix='native launch ', dir=runtime)) / "client-config.json"
        first = self.start(cfg)
        second = self.start(cfg)
        url, reused = self.read_url(first), self.read_url(second)
        self.assertEqual(url, reused)
        time.sleep(0.2)
        if first.poll() is not None:
            first, second = second, first
        self.assertEqual(second.wait(timeout=5), 0)
        self.assertIsNone(first.poll())
        self.close_window(first)
        self.assertFalse((cfg.parent / 'client-instance.json').exists())
        third, restarted = self.launch(cfg)
        headless, isolated = self.launch(cfg, ("--headless",))
        self.assertNotEqual(restarted, isolated)
        self.assertEqual(app_windows(headless.pid), [])
        headless.kill(); headless.wait(timeout=5)
        self.close_window(third)


if __name__ == "__main__":
    unittest.main()
