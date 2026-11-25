"""App child process. Request on stdin; status-only protocol on stdout."""
import json
from pathlib import Path
import sys
from server import Service, export_text, ffmpeg_executable


def run(request, output):
    service = Service(model=request.get('model', 'small'))
    service.jobs['local'] = dict(status='queued', progress=0, message='', title=str(request.get('title', '课堂文字稿'))[:200], segments=[], created=0)
    update = service.update
    def report(key, **values):
        update(key, **values)
        if 'message' in values:
            print(json.dumps({'status': values['message']}, ensure_ascii=False), flush=True)
    service.update = report
    try:
        service.run('local', request['url'])
        job = service.jobs['local']
        if job['status'] != 'done': return 1
        for fmt in ('txt', 'srt'):
            (Path(output) / ('transcript.' + fmt)).write_text(export_text(job['title'], job['segments'], fmt), encoding='utf-8')
        return 0
    finally:
        service.pool.shutdown()


if __name__ == '__main__':
    if sys.argv[1:] == ['--self-test']:
        import subprocess
        from faster_whisper import WhisperModel
        subprocess.run([ffmpeg_executable(), '-version'], check=True, stdout=subprocess.DEVNULL)
        print('TRANSCRIPT_RUNTIME_OK', flush=True)
        sys.exit(0)
    try:
        request = json.loads(sys.stdin.buffer.readline(16385).decode('utf-8'))
        sys.exit(run(request, sys.argv[1]))
    except Exception:
        print(json.dumps({'status': '本机转写失败，请检查环境、模型和校园网'}, ensure_ascii=False), flush=True)
        sys.exit(1)
