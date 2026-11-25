"""Offline integration test: actual HLS/AES/FFmpeg to PCM, no school data or model."""
import io
import array
import subprocess
import tempfile
import wave
from pathlib import Path
from urllib.parse import urlsplit
from server import HlsCache, extract_audio, ffmpeg_executable


def check_audio_pipeline():
    with tempfile.TemporaryDirectory(prefix='bjtu-audio-test-') as folder:
        root = Path(folder)
        source = root / 'source'
        source.mkdir()
        (source / 'key.key').write_bytes(bytes(range(16)))
        (source / 'key-info.txt').write_text('key.key\n' + str(source / 'key.key') + '\n', encoding='utf-8')
        subprocess.run([ffmpeg_executable(), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-c:a', 'aac', '-f', 'hls', '-hls_time', '1', '-hls_list_size', '0', '-hls_key_info_file', str(source / 'key-info.txt'), str(source / 'index.m3u8')], check=True)
        (source / 'master.m3u8').write_text('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=64000\nindex.m3u8\n', encoding='utf-8')
        class FixtureOpener:
            def open(self, request, timeout):
                name = Path(urlsplit(request.full_url).path).name
                if name == 'no-extension': name = 'index.m3u8'
                return io.BytesIO((source / name).read_bytes())
        for entry in ('index.m3u8', 'master.m3u8', 'no-extension'):
            target = root / entry.replace('.', '_')
            target.mkdir()
            cache = HlsCache(target)
            cache.opener = FixtureOpener()
            playlist = cache.fetch('https://lmt1.bjtu.edu.cn/' + entry)
            if not playlist.endswith('.m3u8'): raise AssertionError('Playlist extension lost')
            audio = target / 'audio.wav'
            extract_audio(target / playlist, audio)
            with wave.open(str(audio), 'rb') as wav:
                assert wav.getnchannels() == 1
                assert wav.getframerate() == 16000
                assert 24000 < wav.getnframes() < 64000
                pcm = array.array("h", wav.readframes(wav.getnframes()))
                assert max(abs(sample) for sample in pcm) > 100
    return True
