"""Private, single-worker course transcription service. No school credentials accepted."""
import argparse
import concurrent.futures
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request


def school_url(url):
    p = urlsplit(url)
    if (p.scheme != 'https' or not re.fullmatch(r'lmt\d+\.bjtu\.edu\.cn', p.hostname or '')
            or p.port not in (None, 443, 8880) or p.username or p.password or p.fragment
            or any(ord(c) < 32 for c in url)):
        raise ValueError('只支持学校 lmt 录播服务器的 HTTPS 地址')
    return url


class SchoolRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        school_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HlsCache:
    """Materialize playlists, keys and segments with domain checks before FFmpeg."""
    def __init__(self, root):
        self.root, self.cache, self.total = Path(root), {}, 0
        self.opener = build_opener(SchoolRedirect())
        self.deadline = time.monotonic() + 3600

    def fetch(self, url, depth=0):
        school_url(url)
        if depth > 8 or len(self.cache) > 30000 or time.monotonic() > self.deadline:
            raise ValueError('录像过大或下载超时')
        if url in self.cache:
            return self.cache[url]
        target = self.root / f'{len(self.cache):06d}.bin'
        self.cache[url] = target.name
        with self.opener.open(Request(url, headers={'User-Agent': 'BJTU-Transcript/1'}), timeout=30) as response:
            first = response.read(1024)
            playlist = first.lstrip().startswith(b'#EXTM3U')
            with target.open('wb') as out:
                chunk = first
                size = 0
                while chunk:
                    self.total += len(chunk)
                    size += len(chunk)
                    if self.total > 8 * 1024**3 or size > (2 * 1024**2 if playlist else 128 * 1024**2):
                        raise ValueError('录像超过下载限制')
                    out.write(chunk)
                    chunk = response.read(64 * 1024)
        if playlist:
            lines = []
            for line in target.read_text('utf-8-sig').splitlines():
                if line and not line.startswith('#'):
                    line = self.fetch(urljoin(url, line.strip()), depth + 1)
                elif 'URI=' in line:
                    line = re.sub(r'URI="([^"]+)"', lambda m: 'URI="' + self.fetch(urljoin(url, m[1]), depth + 1) + '"', line)
                    if re.search(r'URI=(?!")', line):
                        raise ValueError('不支持的录像清单')
                lines.append(line)
            target.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return target.name


def ffmpeg_executable():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return shutil.which('ffmpeg') or 'ffmpeg'


def timestamp(seconds, srt=False):
    n = max(0, round(seconds * 1000))
    h, n = divmod(n, 3600000)
    m, n = divmod(n, 60000)
    s, ms = divmod(n, 1000)
    return f'{h:02}:{m:02}:{s:02}' + (f',{ms:03}' if srt else '')


def export_text(title, segments, fmt):
    if fmt == 'srt':
        return '\n\n'.join(f'{i}\n{timestamp(s["start"], True)} --> {timestamp(s["end"], True)}\n{s["text"].strip()}' for i, s in enumerate(segments, 1)) + '\n'
    return title + '\n\n' + '\n'.join(f'[{timestamp(s["start"])}] {s["text"].strip()}' for s in segments) + '\n'


class Service:
    def __init__(self, model='small', device='cpu'):
        self.model_name, self.device, self.model = model, device, None
        self.jobs, self.lock = {}, threading.Lock()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def submit(self, url, title):
        school_url(url)
        with self.lock:
            # Prune completed jobs after 24 hours; bound queued work and memory.
            self.jobs = {k: v for k, v in self.jobs.items() if time.time() - v['created'] < 86400 or v['status'] in ('queued', 'running')}
            if len(self.jobs) >= 100 or sum(j['status'] in ('queued', 'running') for j in self.jobs.values()) >= 8:
                raise ValueError('任务已满，请稍后重试')
            key = secrets.token_hex(16)
            self.jobs[key] = dict(status='queued', progress=0, message='等待转写', title=title[:200], segments=[], created=time.time())
        self.pool.submit(self.run, key, url)
        return key

    def update(self, key, **values):
        with self.lock:
            self.jobs[key].update(values)

    def run(self, key, url):
        try:
            self.update(key, status='running', message='下载录像并提取音频')
            with tempfile.TemporaryDirectory(prefix='bjtu-transcript-') as folder:
                manifest = HlsCache(folder).fetch(url)
                audio = str(Path(folder) / 'audio.wav')
                subprocess.run([ffmpeg_executable(), '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,crypto', '-allowed_extensions', 'ALL', '-i', str(Path(folder) / manifest), '-vn', '-ac', '1', '-ar', '16000', audio], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3600)
                self.update(key, message='加载模型（首次运行需要下载）', progress=0.1)
                if self.model is None:
                    from faster_whisper import WhisperModel
                    self.model = WhisperModel(self.model_name, device=self.device, compute_type='int8' if self.device == 'cpu' else 'float16')
                self.update(key, message='正在识别课堂语音')
                segments, info = self.model.transcribe(audio, language='zh', vad_filter=True, initial_prompt='以下是大学课堂的中文授课内容。')
                result = []
                for segment in segments:
                    result.append(dict(start=segment.start, end=segment.end, text=segment.text))
                    self.update(key, progress=min(0.99, 0.1 + 0.89 * segment.end / max(1, info.duration)))
                if not result:
                    raise ValueError('未识别到语音，请选择另一录像画面')
                self.update(key, status='done', progress=1, message='文字稿已生成', segments=result)
        except Exception:
            # Never return URLs, filesystem paths, tokens or upstream exception text.
            self.update(key, status='failed', message='转写失败：检查电脑校园网、FFmpeg、模型下载及录像是否含音轨')


def handler(service, token):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def log_message(self, *args):
            pass

        def respond(self, code, value, content_type='application/json; charset=utf-8'):
            data = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, dict) else value.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.respond(401, {'error': '连接密钥不正确'})
                return False
            return True

        def do_POST(self):
            if not self.authorized(): return
            if self.path != '/jobs': return self.respond(404, {'error': '不存在'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384: raise ValueError('请求过大')
                body = json.loads(self.rfile.read(length))
                key = service.submit(str(body['url']), str(body.get('title', '课程回放')))
                self.respond(202, {'id': key})
            except (ValueError, KeyError, TypeError):
                self.respond(400, {'error': '录像地址或请求不正确，或队列已满'})

        def do_GET(self):
            if not self.authorized(): return
            if self.path == '/health': return self.respond(200, {'status': 'ok'})
            match = re.fullmatch(r'/jobs/([a-f0-9]{32})(?:/(txt|srt))?', self.path)
            if not match: return self.respond(404, {'error': '不存在'})
            with service.lock:
                job = dict(service.jobs.get(match[1], {}))
            if not job: return self.respond(404, {'error': '任务不存在或已过期'})
            fmt = match[2]
            if fmt:
                if job['status'] != 'done': return self.respond(409, {'error': '文字稿尚未生成'})
                return self.respond(200, export_text(job['title'], job['segments'], fmt), 'text/plain; charset=utf-8')
            self.respond(200, {k: v for k, v in job.items() if k not in ('segments', 'created')})
    return Handler


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--model', default='small')
    p.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
    args = p.parse_args()
    token = os.environ.get('BJTU_TRANSCRIPT_TOKEN', '')
    if len(token) < 24: p.error('设置 BJTU_TRANSCRIPT_TOKEN 环境变量，至少 24 字符')
    if args.host not in ('127.0.0.1', 'localhost'): p.error('服务仅监听本机；手机请通过可信 HTTPS 反向代理连接')
    server = ThreadingHTTPServer((args.host, args.port), handler(Service(args.model, args.device), token))
    server.daemon_threads = True
    print(f'转写服务已启动，端口 {args.port}；手机请连接此电脑的 HTTPS 代理地址')
    server.serve_forever()

if __name__ == '__main__': main()
