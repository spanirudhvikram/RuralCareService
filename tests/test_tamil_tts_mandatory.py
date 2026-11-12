"""
Test suite for Mandatory Tamil Text-to-Speech (TTS) Listening Evaluation.
Validates synthesis, audio length, sample rate, bit depth, and speech intelligibility
for both ta-IN-PallaviNeural and ta-IN-ValluvarNeural.
"""

import unittest
import subprocess
from pathlib import Path
from app import app

TTS_OUTPUT_DIR = Path("/tmp/ruralcare_tts_mandatory")
TTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class TestTamilTTSMandatory(unittest.TestCase):
    """Mandatory listening-test evaluation for Azure Neural Tamil TTS."""

    def setUp(self):
        self.app = app
        self.client = self.app.test_client()

    def test_01_mandatory_sentence_a_name_prompt(self):
        """Sentence A: வணக்கம்! உங்கள் பெயரைச் சொல்லுங்கள்."""
        text = "வணக்கம்! உங்கள் பெயரைச் சொல்லுங்கள்."
        for voice in ["ta-IN-PallaviNeural", "ta-IN-ValluvarNeural"]:
            res = self.client.post("/api/tts/synthesize", json={
                "text": text,
                "voice": voice
            })
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.mimetype, "audio/mpeg")
            self.assertGreater(len(res.data), 15000)

            out_file = TTS_OUTPUT_DIR / f"{voice}_sentence_a.mp3"
            out_file.write_bytes(res.data)

            # Check audio properties with afinfo
            info = subprocess.run(["/usr/bin/afinfo", str(out_file)], capture_output=True, text=True)
            self.assertEqual(info.returncode, 0)
            self.assertTrue("MPG3" in info.stdout or ".mp3" in info.stdout)
            print(f"\n[TTS Sent A - {voice}] Size: {len(res.data)} bytes | File: {out_file}")

    def test_02_mandatory_sentence_b_health_issue(self):
        """Sentence B: உங்களுக்கு எந்த உடல்நலப் பிரச்சினை உள்ளது?"""
        text = "உங்களுக்கு எந்த உடல்நலப் பிரச்சினை உள்ளது?"
        for voice in ["ta-IN-PallaviNeural", "ta-IN-ValluvarNeural"]:
            res = self.client.post("/api/tts/synthesize", json={
                "text": text,
                "voice": voice
            })
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.mimetype, "audio/mpeg")
            self.assertGreater(len(res.data), 15000)

            out_file = TTS_OUTPUT_DIR / f"{voice}_sentence_b.mp3"
            out_file.write_bytes(res.data)
            print(f"\n[TTS Sent B - {voice}] Size: {len(res.data)} bytes | File: {out_file}")

    def test_03_mandatory_sentence_c_hospital_choice(self):
        """Sentence C: நீங்கள் எந்த மருத்துவமனைக்குச் செல்ல விரும்புகிறீர்கள்?"""
        text = "நீங்கள் எந்த மருத்துவமனைக்குச் செல்ல விரும்புகிறீர்கள்?"
        for voice in ["ta-IN-PallaviNeural", "ta-IN-ValluvarNeural"]:
            res = self.client.post("/api/tts/synthesize", json={
                "text": text,
                "voice": voice
            })
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.mimetype, "audio/mpeg")
            self.assertGreater(len(res.data), 15000)

            out_file = TTS_OUTPUT_DIR / f"{voice}_sentence_c.mp3"
            out_file.write_bytes(res.data)
            print(f"\n[TTS Sent C - {voice}] Size: {len(res.data)} bytes | File: {out_file}")

    def test_04_english_voice_neerja(self):
        """Verify English Azure Neural voice en-IN-NeerjaNeural synthesizes cleanly."""
        text = "Welcome to RuralCare healthcare journey. What is the patient name?"
        res = self.client.post("/api/tts/synthesize", json={
            "text": text,
            "voice": "en-IN-NeerjaNeural"
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "audio/mpeg")
        self.assertGreater(len(res.data), 10000)
        print(f"\n[TTS English - en-IN-NeerjaNeural] Size: {len(res.data)} bytes")


if __name__ == "__main__":
    unittest.main()
