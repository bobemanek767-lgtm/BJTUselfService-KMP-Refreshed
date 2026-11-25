import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import worker

class WorkerTests(unittest.TestCase):
    def test_bundled_resources_match(self):
        root = Path(__file__).resolve().parents[2]
        resources = root / 'multiplatform/shared/src/desktopMain/resources/transcription'
        for name in ('server.py', 'worker.py', 'audio_smoke.py'):
            self.assertEqual((resources/name).read_bytes(), (Path(__file__).parent/name).read_bytes())

    def test_success_exports_unicode_and_reports_status(self):
        def fake_run(service, key, url):
            service.update(key, status='done', message='文字稿已生成', segments=[dict(start=0.1, end=2.2, text='数据结构')])
        with tempfile.TemporaryDirectory() as folder, patch.object(worker.Service, 'run', fake_run), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(worker.run(dict(url='https://lmt1.bjtu.edu.cn/a', title='课程'), folder), 0)
            self.assertIn('数据结构', (Path(folder)/'transcript.txt').read_text(encoding='utf-8'))
            self.assertIn('00:00:00,100 --> 00:00:02,200', (Path(folder)/'transcript.srt').read_text(encoding='utf-8'))
            self.assertIn('status', output.getvalue())

    def test_failure_does_not_export(self):
        def fake_run(service, key, url):
            service.update(key, status='failed', message='失败')
        with tempfile.TemporaryDirectory() as folder, patch.object(worker.Service, 'run', fake_run), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(worker.run(dict(url='invalid'), folder), 1)
            self.assertEqual(list(Path(folder).iterdir()), [])

if __name__ == '__main__': unittest.main()
