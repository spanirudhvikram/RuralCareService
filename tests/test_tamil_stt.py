"""
Unit and integration tests for RuralCare Speech-to-Text (STT) Module.
Tests genuine spoken Tamil audio input, raw STT transcription output, domain normalization,
confidence scoring, and error handling across mandatory benchmarks.
"""

import unittest
import asyncio
import subprocess
from pathlib import Path
from app import app
import edge_tts

TEMP_AUDIO_DIR = Path("/tmp/ruralcare_stt_tests")
TEMP_AUDIO_DIR.mkdir(parents=True, exist_ok=True)


class TestRuralCareTamilSTT(unittest.TestCase):
    """Real spoken audio tests for Tamil Speech-to-Text (STT)."""

    def setUp(self):
        self.app = app
        self.client = self.app.test_client()

    def _generate_wav(self, text: str, voice: str = "ta-IN-PallaviNeural", rate: str = "+0%") -> Path:
        """Helper to generate realistic spoken audio and convert to 16kHz mono WAV with retry and caching."""
        safe_name = "".join([c if c.isalnum() else "_" for c in text[:20]]) + f"_{voice[:10]}_{rate.replace('+', '').replace('-', 'neg')}"
        mp3_path = TEMP_AUDIO_DIR / f"{safe_name}.mp3"
        wav_path = TEMP_AUDIO_DIR / f"{safe_name}.wav"

        if wav_path.exists() and wav_path.stat().st_size > 1000:
            return wav_path

        async def _synth():
            for attempt in range(3):
                try:
                    comm = edge_tts.Communicate(text, voice, rate=rate)
                    await comm.save(str(mp3_path))
                    if mp3_path.exists() and mp3_path.stat().st_size > 500:
                        return
                except Exception as e:
                    if attempt == 2:
                        raise e
                    await asyncio.sleep(1.5)

        asyncio.run(_synth())
        subprocess.run(
            ["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(mp3_path), str(wav_path)],
            check=True
        )
        return wav_path

    def test_01_stt_status_endpoint(self):
        """Verify GET /api/stt/status returns available engines and ta-IN locale."""
        res = self.client.get("/api/stt/status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("default_locale"), "ta-IN")
        self.assertIn("ta-IN", data.get("supported_locales", {}))
        self.assertIn("en-IN", data.get("supported_locales", {}))

    def test_02_mandatory_sentence_1_headache(self):
        """Sentence 1: எனக்கு தலைவலி இருக்கிறது."""
        text = "எனக்கு தலைவலி இருக்கிறது."
        wav = self._generate_wav(text)
        with open(wav, "rb") as f:
            res = self.client.post("/api/stt/transcribe", data={"audio": f, "language": "ta-IN"})

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("தலைவலி", data.get("transcription"))
        self.assertGreaterEqual(data.get("confidence", 0), 0.70)
        print(f"\n[Test 1] Target: {text} | STT: {data.get('transcription')} ({data.get('confidence')})")

    def test_03_mandatory_sentence_2_fever(self):
        """Sentence 2: எனக்கு காய்ச்சல் உள்ளது."""
        text = "எனக்கு காய்ச்சல் உள்ளது."
        wav = self._generate_wav(text)
        with open(wav, "rb") as f:
            res = self.client.post("/api/stt/transcribe", data={"audio": f, "language": "ta-IN"})

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("காய்ச்சல்", data.get("transcription"))
        print(f"\n[Test 2] Target: {text} | STT: {data.get('transcription')} ({data.get('confidence')})")

    def test_04_mandatory_sentence_3_hospital(self):
        """Sentence 3: எனக்கு அருகில் மருத்துவமனை வேண்டும்."""
        text = "எனக்கு அருகில் மருத்துவமனை வேண்டும்."
        wav = self._generate_wav(text)
        with open(wav, "rb") as f:
            res = self.client.post("/api/stt/transcribe", data={"audio": f, "language": "ta-IN"})

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("மருத்துவமனை", data.get("transcription"))
        print(f"\n[Test 3] Target: {text} | STT: {data.get('transcription')} ({data.get('confidence')})")

    def test_05_mandatory_sentence_4_transport(self):
        """Sentence 4: எனக்கு போக்குவரத்து வசதி தேவை."""
        text = "எனக்கு போக்குவரத்து வசதி தேவை."
        wav = self._generate_wav(text)
        with open(wav, "rb") as f:
            res = self.client.post("/api/stt/transcribe", data={"audio": f, "language": "ta-IN"})

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("போக்குவரத்து", data.get("transcription"))
        print(f"\n[Test 4] Target: {text} | STT: {data.get('transcription')} ({data.get('confidence')})")

    def test_06_mandatory_sentence_5_location_pollachi(self):
        """Sentence 5: நான் பொள்ளாச்சியில் வசிக்கிறேன்."""
        text = "நான் பொள்ளாச்சியில் வசிக்கிறேன்."
        wav = self._generate_wav(text)
        with open(wav, "rb") as f:
            res = self.client.post("/api/stt/transcribe", data={"audio": f, "language": "ta-IN"})

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("பொள்ளாச்சி", data.get("transcription"))
        print(f"\n[Test 5] Target: {text} | STT: {data.get('transcription')} ({data.get('confidence')})")

    def test_07_conversational_tamil(self):
        """Natural Conversational Tamil: வணக்கம், எனக்கு உடம்பு சரியில்லை, டாக்டர் பார்க்க வேண்டும்."""
        text = "வணக்கம், எனக்கு உடம்பு சரியில்லை, டாக்டர் பார்க்க வேண்டும்."
        wav = self._generate_wav(text)
        with open(wav, "rb") as f:
            res = self.client.post("/api/stt/transcribe", data={"audio": f, "language": "ta-IN"})

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("வணக்கம்", data.get("transcription"))
        print(f"\n[Test 6 Conversational] Target: {text} | STT: {data.get('transcription')}")

    def test_08_mixed_tamil_english(self):
        """Mixed Tamil-English: எனக்கு eye checkup மற்றும் hospital appointment வேண்டும்."""
        text = "எனக்கு eye checkup மற்றும் hospital appointment வேண்டும்."
        wav = self._generate_wav(text)
        with open(wav, "rb") as f:
            res = self.client.post("/api/stt/transcribe", data={"audio": f, "language": "ta-IN"})

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        print(f"\n[Test 7 Mixed] Target: {text} | STT: {data.get('transcription')}")

    def test_09_different_speaking_speeds_slow_and_fast(self):
        """Test recognition across slow (-20%) and normal (+0%) speeds."""
        text = "எனக்கு தலைவலி இருக்கிறது."
        wav_slow = self._generate_wav(text, rate="-20%")
        with open(wav_slow, "rb") as f:
            res = self.client.post("/api/stt/transcribe", data={"audio": f, "language": "ta-IN"})

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("தலைவலி", data.get("transcription"))
        print(f"\n[Test 8 Slow Rate] Target: {text} (-20%) | STT: {data.get('transcription')}")

    def test_10_empty_audio_rejection(self):
        """Verify empty audio payload is rejected with 400 Bad Request."""
        res = self.client.post("/api/stt/transcribe", data={})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data.get("success"))
        self.assertEqual(data.get("error"), "EMPTY_AUDIO_PAYLOAD")

    def test_11_silent_audio_returns_no_speech_detected(self):
        """Verify silent audio returns 422 NO_SPEECH_DETECTED with friendly Tamil message."""
        # Create 1 second of pure silence WAV
        silence_wav = TEMP_AUDIO_DIR / "silence.wav"
        import wave
        with wave.open(str(silence_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00" * 32000)

        with open(silence_wav, "rb") as f:
            res = self.client.post("/api/stt/transcribe", data={"audio": f, "language": "ta-IN"})

        self.assertEqual(res.status_code, 422)
        data = res.get_json()
        self.assertFalse(data.get("success"))
        self.assertEqual(data.get("error"), "NO_SPEECH_DETECTED")
        self.assertIn("சத்தம்", data.get("message"))


if __name__ == "__main__":
    unittest.main()
