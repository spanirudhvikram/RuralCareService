import unittest
from pathlib import Path
from app import app

class TestTamilAzureNeuralTTS(unittest.TestCase):
    """
    Unit and integration tests for Microsoft Azure Neural Tamil Text-to-Speech (TTS).
    Validates ta-IN-PallaviNeural (Primary Female) and ta-IN-ValluvarNeural (Alternative Male)
    across the 5 official benchmark Tamil sentences and dynamic wizard prompts.
    """

    def setUp(self):
        self.app = app
        self.client = self.app.test_client()

    def test_01_get_supported_voices(self):
        """Test GET /api/tts/voices returns Pallavi and Valluvar neural voices."""
        response = self.client.get("/api/tts/voices")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("default_voice"), "ta-IN-PallaviNeural")
        
        voice_names = [v["name"] for v in data.get("voices", [])]
        self.assertIn("ta-IN-PallaviNeural", voice_names)
        self.assertIn("ta-IN-ValluvarNeural", voice_names)

    def test_02_official_sentence_1_welcome_pallavi(self):
        """Sentence 1: வணக்கம்! ரூரல்கேர் மருத்துவ சேவைக்கு உங்களை வரவேற்கிறோம்."""
        text = "வணக்கம்! ரூரல்கேர் மருத்துவ சேவைக்கு உங்களை வரவேற்கிறோம்."
        response = self.client.post("/api/tts/synthesize", json={
            "text": text,
            "voice": "ta-IN-PallaviNeural"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "audio/mpeg")
        self.assertGreater(len(response.data), 1000)
        self.assertEqual(response.headers.get("X-TTS-Voice"), "ta-IN-PallaviNeural")

    def test_03_official_sentence_2_name_pallavi(self):
        """Sentence 2: உங்கள் பெயரைச் சொல்லுங்கள்."""
        text = "உங்கள் பெயரைச் சொல்லுங்கள்."
        response = self.client.post("/api/tts/synthesize", json={
            "text": text,
            "voice": "ta-IN-PallaviNeural"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "audio/mpeg")
        self.assertGreater(len(response.data), 1000)

    def test_04_official_sentence_3_service_pallavi(self):
        """Sentence 3: உங்களுக்கு எந்த மருத்துவ உதவி தேவை?"""
        text = "உங்களுக்கு எந்த மருத்துவ உதவி தேவை?"
        response = self.client.post("/api/tts/synthesize", json={
            "text": text,
            "voice": "ta-IN-PallaviNeural"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "audio/mpeg")
        self.assertGreater(len(response.data), 1000)

    def test_05_official_sentence_4_discovery_pallavi(self):
        """Sentence 4: அருகிலுள்ள மருத்துவமனைகளைத் தேடுகிறோம். தயவுசெய்து காத்திருக்கவும்."""
        text = "அருகிலுள்ள மருத்துவமனைகளைத் தேடுகிறோம். தயவுசெய்து காத்திருக்கவும்."
        response = self.client.post("/api/tts/synthesize", json={
            "text": text,
            "voice": "ta-IN-PallaviNeural"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "audio/mpeg")
        self.assertGreater(len(response.data), 1000)

    def test_06_official_sentence_5_confirmation_pallavi(self):
        """Sentence 5: உங்கள் பயண விவரங்களை உறுதிப்படுத்துங்கள்."""
        text = "உங்கள் பயண விவரங்களை உறுதிப்படுத்துங்கள்."
        response = self.client.post("/api/tts/synthesize", json={
            "text": text,
            "voice": "ta-IN-PallaviNeural"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "audio/mpeg")
        self.assertGreater(len(response.data), 1000)

    def test_07_alternative_voice_valluvar(self):
        """Sentence 1 with Alternative Voice: ta-IN-ValluvarNeural."""
        text = "வணக்கம்! ரூரல்கேர் மருத்துவ சேவைக்கு உங்களை வரவேற்கிறோம்."
        response = self.client.post("/api/tts/synthesize", json={
            "text": text,
            "voice": "ta-IN-ValluvarNeural"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "audio/mpeg")
        self.assertGreater(len(response.data), 1000)
        self.assertEqual(response.headers.get("X-TTS-Voice"), "ta-IN-ValluvarNeural")

    def test_08_dynamic_spoken_readback(self):
        """Test dynamic readback with patient data in Tamil."""
        text = "நோயாளி மீனாட்சி, வயது 62, மருத்துவ சேவை பொது மருத்துவம். பதிவு செய்ய ஆம் என்று கூறவும்."
        response = self.client.post("/api/tts/synthesize", json={
            "text": text,
            "voice": "ta-IN-PallaviNeural",
            "json": True
        })
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("audio_url", data)

        # Verify static audio serving
        audio_res = self.client.get(data["audio_url"])
        self.assertEqual(audio_res.status_code, 200)
        self.assertEqual(audio_res.mimetype, "audio/mpeg")

    def test_09_empty_text_error(self):
        """Test validation error when text is empty."""
        response = self.client.post("/api/tts/synthesize", json={
            "text": "   "
        })
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertFalse(data.get("success"))

    def test_10_missing_audio_file(self):
        """Test 404 response for non-existent audio cache."""
        response = self.client.get("/api/tts/audio/non_existent_audio_file.mp3")
        self.assertEqual(response.status_code, 404)

if __name__ == "__main__":
    unittest.main()
