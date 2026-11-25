import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import server


class TranscriptTests(unittest.TestCase):
    def test_domain_boundary(self):
        for url in ['http://lmt1.bjtu.edu.cn/a', 'https://evil.com/a', 'https://lmt1.bjtu.edu.cn.evil.com/a', 'file:///etc/passwd', 'https://user@lmt1.bjtu.edu.cn/a', 'https://lmt1.bjtu.edu.cn:22/a']:
            with self.subTest(url=url), self.assertRaises(ValueError): server.school_url(url)
        server.school_url('https://lmt1.bjtu.edu.cn:8880/a/index.m3u8?token=example')

    def test_export_milliseconds_and_unicode(self):
        segments = [dict(start=59.9996, end=61.234, text=' 数据结构 ')]
        self.assertIn('00:01:00,000 --> 00:01:01,234', server.export_text('课', segments, 'srt'))
        self.assertIn('[00:01:00] 数据结构', server.export_text('课', segments, 'txt'))

    def test_hls_localization_and_external_rejection(self):
        class Response(io.BytesIO): pass
        playlist = b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:1,\nseg.ts\n'
        with tempfile.TemporaryDirectory() as tmp:
            cache = server.HlsCache(tmp)
            with patch.object(cache.opener, 'open', side_effect=lambda req, timeout: Response(playlist if req.full_url.endswith('m3u8') else b'abc')):
                name = cache.fetch('https://lmt1.bjtu.edu.cn/a.m3u8')
                text = (Path(tmp) / name).read_text()
                self.assertNotIn('https://', text)
                self.assertIn('URI="000001.bin"', text)
                self.assertIn('000002.bin', text)
        with tempfile.TemporaryDirectory() as tmp:
            cache = server.HlsCache(tmp)
            with patch.object(cache.opener, 'open', return_value=Response(b'#EXTM3U\nhttps://evil.com/seg.ts\n')):
                with self.assertRaises(ValueError): cache.fetch('https://lmt1.bjtu.edu.cn/a.m3u8')

    def test_authenticated_http_lifecycle(self):
        service = server.Service()
        token = 'test-' + 'x' * 32
        http = ThreadingHTTPServer(('127.0.0.1', 0), server.handler(service, token))
        thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
        base = 'http://127.0.0.1:' + str(http.server_port)
        def request(path, data=None, auth=True):
            req = Request(base + path, data=None if data is None else json.dumps(data).encode(), headers={'Authorization': 'Bearer ' + token} if auth else {})
            return urlopen(req).read().decode()
        try:
            with self.assertRaises(HTTPError) as err: request('/health', auth=False)
            self.assertEqual(err.exception.code, 401)
            self.assertEqual(json.loads(request('/health'))['status'], 'ok')
            with patch.object(service.pool, 'submit'):
                job = json.loads(request('/jobs', dict(url='https://lmt1.bjtu.edu.cn/a.m3u8', title='课')))['id']
            self.assertEqual(json.loads(request('/jobs/' + job))['status'], 'queued')
            with self.assertRaises(HTTPError) as err: request('/jobs/' + job + '/txt')
            self.assertEqual(err.exception.code, 409)
            service.update(job, status='done', progress=1, segments=[dict(start=0, end=1, text='测试')])
            self.assertIn('测试', request('/jobs/' + job + '/txt'))
            self.assertIn('00:00:01,000', request('/jobs/' + job + '/srt'))
            self.assertNotIn('segments', json.loads(request('/jobs/' + job)))
            with self.assertRaises(HTTPError): request('/jobs', dict(url='https://evil.com/a'))
        finally:
            http.shutdown(); http.server_close(); service.pool.shutdown()

    def test_worker_failure_redacts_url(self):
        service = server.Service()
        with patch.object(service.pool, 'submit'):
            key = service.submit('https://lmt1.bjtu.edu.cn/a.m3u8?secret=hidden', '课')
        with patch.object(server.HlsCache, 'fetch', side_effect=Exception('secret=hidden')):
            service.run(key, 'https://lmt1.bjtu.edu.cn/a.m3u8?secret=hidden')
        self.assertEqual(service.jobs[key]['status'], 'failed')
        self.assertNotIn('hidden', service.jobs[key]['message'])
        service.pool.shutdown()

if __name__ == '__main__': unittest.main()
