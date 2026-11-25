import unittest
from audio_smoke import check_audio_pipeline

class AudioPipelineTests(unittest.TestCase):
    def test_actual_encrypted_hls_to_pcm(self):
        self.assertTrue(check_audio_pipeline())

if __name__ == '__main__': unittest.main()
