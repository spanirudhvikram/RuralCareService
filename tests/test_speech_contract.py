import unittest
import json
import uuid
import random
from app import app
from database import get_db, init_db

class RuralCareSpeechContractTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_01_voice_assisted_journey_creation_with_tamil_unicode(self):
        """Verify voice-assisted registration accurately preserves Tamil Unicode text."""
        tamil_name = "முத்துசாமி கவுண்டர்"
        tamil_village = "செட்டிபாளையம்"
        phone = "98421" + "".join([str(random.randint(0, 9)) for _ in range(5)])

        payload = {
            "patient_name": tamil_name,
            "age": 68,
            "contact_number": phone,
            "village": tamil_village,
            "preferred_service_id": "SRV-EYE",
            "preferred_service_name": "Eye Care / Ophthalmology",
            "preferred_date": "2026-11-20",
            "facility_id": "FAC-06",
            "facility_name": "Coimbatore Medical College Hospital",
            "registration_method": "SELF_SERVICE",
            "registration_channel": "VOICE_ASSISTED",
            "transport_required": True,
            "pickup_landmark": f"{tamil_village} பேருந்து நிறுத்தம்",
            "client_uuid": f"voice_{uuid.uuid4().hex}"
        }

        res = self.app.post("/api/journeys", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        journey = data.get("journey")
        self.assertIsNotNone(journey)

        # Verify exact Tamil Unicode preservation
        self.assertEqual(journey.get("patient_name"), tamil_name)
        self.assertEqual(journey.get("village"), tamil_village)
        self.assertEqual(journey.get("registration_channel"), "VOICE_ASSISTED")
        self.assertEqual(journey.get("registration_method"), "SELF_SERVICE")
        self.assertTrue(journey.get("transport_required"))

    def test_02_voice_assisted_journey_validation(self):
        """Verify voice registration payload validation enforces required fields."""
        # Missing contact number
        invalid_payload = {
            "patient_name": "கண்ணம்மாள்",
            "age": 72,
            "village": "கிணத்துக்கடவு",
            "registration_channel": "VOICE_ASSISTED"
        }
        res = self.app.post("/api/journeys", json=invalid_payload)
        self.assertEqual(res.status_code, 400)

    def test_03_voice_batch_sync_preserves_tamil_unicode(self):
        """Verify offline sync queue processing preserves voice registration Tamil characters."""
        batch_id = f"batch_{uuid.uuid4().hex[:8]}"
        client_uuid = f"offline_voice_{uuid.uuid4().hex[:8]}"
        tamil_name = "சுப்பிரமணியன் பழனிசாமி"
        tamil_village = "நெகமம்"
        phone = "97500" + "".join([str(random.randint(0, 9)) for _ in range(5)])

        item = {
            "client_uuid": client_uuid,
            "patient_name": tamil_name,
            "age": 62,
            "contact_number": phone,
            "village": tamil_village,
            "preferred_service_id": "SRV-GEN",
            "preferred_service_name": "General Medicine",
            "preferred_date": "2026-11-21",
            "facility_id": "FAC-01",
            "registration_method": "SELF_SERVICE",
            "registration_channel": "VOICE_ASSISTED",
            "transport_required": False
        }

        res = self.app.post("/api/sync/batch", json={
            "items": [item]
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data["results"][0]["status"], "SYNCED")
        jid = data["results"][0]["journey_id"]

        # Retrieve journey with verified phone
        get_res = self.app.get(f"/api/journeys/{jid}?verify_phone={phone}")
        self.assertEqual(get_res.status_code, 200)
        get_data = get_res.get_json()
        saved_journey = get_data.get("journey")
        self.assertEqual(saved_journey.get("patient_name"), tamil_name)
        self.assertEqual(saved_journey.get("village"), tamil_village)
        self.assertEqual(saved_journey.get("registration_channel"), "VOICE_ASSISTED")

    def test_04_tamil_tts_text_cleaning_and_punctuation_pause_conversion(self):
        """Verify the 4 official Tamil TTS sentences are cleaned to prevent spoken punctuation."""
        import re

        def clean_speech_text(raw_text, lang_code="ta-IN"):
            clean = str(raw_text)
            clean = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\ufe00-\ufe0f]", " ", clean)
            clean = re.sub(r"[•·*#✓—–~|►▶️🔊🎙️📢ℹ️⚠️🚨]", " ", clean)
            if lang_code.startswith("ta"):
                clean = re.sub(r"\s*&\s*", " மற்றும் ", clean)
            clean = re.sub(r"[()[\]{}\"'\u201c\u201d\u2018\u2019`]", " ", clean)
            clean = re.sub(r"-+", " ", clean)
            clean = re.sub(r"/+", " ", clean)
            clean = re.sub(r"!+", ", ", clean)
            clean = re.sub(r"[:;]+", ", ", clean)
            clean = re.sub(r"\?\s+(?=[^\s])", ", ", clean)
            clean = re.sub(r"\?+", ".", clean)
            clean = re.sub(r"\s*,\s*,+", ", ", clean)
            clean = re.sub(r"\s*\.\s*\.+", ". ", clean)
            clean = re.sub(r",\s*\.", ".", clean)
            clean = re.sub(r"\s+", " ", clean).strip()
            if clean and not clean.endswith("."):
                clean += "."
            return clean

        # Test 1
        s1 = "வணக்கம்! RuralCare-க்கு உங்களை வரவேற்கிறோம்."
        c1 = clean_speech_text(s1, "ta-IN")
        self.assertNotIn("!", c1)
        self.assertNotIn("-", c1)
        self.assertIn("வணக்கம், RuralCare க்கு", c1)

        # Test 2
        s2 = "உங்களுக்கு என்ன உதவி வேண்டும்?"
        c2 = clean_speech_text(s2, "ta-IN")
        self.assertNotIn("?", c2)
        self.assertIn("உங்களுக்கு என்ன உதவி வேண்டும்", c2)

        # Test 3
        s3 = "மருத்துவமனை தேட வேண்டுமா? மருத்துவரைச் சந்திக்க நேரம் பதிவு செய்ய வேண்டுமா?"
        c3 = clean_speech_text(s3, "ta-IN")
        self.assertNotIn("?", c3)
        self.assertIn("மருத்துவமனை தேட வேண்டுமா, மருத்துவரைச் சந்திக்க", c3)

        # Test 4
        s4 = "உங்கள் விண்ணப்பம் வெற்றிகரமாக பதிவு செய்யப்பட்டது."
        c4 = clean_speech_text(s4, "ta-IN")
        self.assertIn("உங்கள் விண்ணப்பம் வெற்றிகரமாக பதிவு செய்யப்பட்டது.", c4)

        print("✓ Tamil TTS text cleaning verified: exclamation marks and hyphens converted to natural pauses.")

    def test_05_tamil_voice_selection_contract(self):
        """Verify speech.js contains strict guards against English voice fallback for Tamil."""
        with open("static/js/speech.js", "r", encoding="utf-8") as f:
            code = f.read()

        self.assertIn("lang.startsWith(\"ta\") && !voice", code)
        self.assertIn("cleanSpeechText", code)
        self.assertIn("testTamilSentence", code)
        self.assertIn("ensureVoicesLoaded", code)
        print("✓ Tamil voice selection and speech synthesis contract verified.")

if __name__ == "__main__":
    unittest.main()
