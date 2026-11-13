import os
import unittest
import json
from app import app
from database import get_db, init_db

class AzureOptionalAndFallbacksTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_01_flask_starts_and_health_check_succeeds(self):
        """Verify health check succeeds without requiring Azure credentials."""
        res = self.app.get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn(data["database"], ("firebase_firestore", "persistent_document_store"))
        print("✓ Health check functional with zero Azure dependency.")

    def test_02_tts_voices_reports_fallback_availability(self):
        """Verify voices endpoint reports voices and fallback availability."""
        res = self.app.get("/api/tts/voices")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIn("ta-IN-PallaviNeural", [v["name"] for v in data["voices"]])
        self.assertTrue(data.get("fallback_to_browser_available"))
        print("✓ TTS voices endpoint functional; browser fallback flagged available.")

    def test_03_stt_status_reports_status_without_azure(self):
        """Verify STT status endpoint works cleanly without Azure keys."""
        res = self.app.get("/api/stt/status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["default_locale"], "ta-IN")
        self.assertIn("ta-IN", data["supported_locales"])
        print("✓ STT status endpoint functional.")

    def test_04_patient_registration_workflow_unaffected(self):
        """Verify patient registration continues to function cleanly."""
        res = self.app.post("/api/patients/register", json={
            "name": "ஆறுமுகம் (Fallback Test)",
            "age": 52,
            "contact_number": "9842109988",
            "village": "செட்டிபாளையம்",
            "consent_given": True
        })
        self.assertIn(res.status_code, (200, 201))
        data = res.get_json()
        self.assertTrue(data["success"])
        pat = data["patient"]
        self.assertTrue(pat["id"].startswith("PID-2026-"))
        self.assertEqual(pat["name"], "ஆறுமுகம் (Fallback Test)")
        print(f"✓ Patient registration successful: {pat['id']}")

    def test_05_patient_login_workflow_unaffected(self):
        """Verify patient login continues to function cleanly."""
        res = self.app.post("/api/patients/login", json={
            "patient_id": "PID-2026-1001",
            "contact_number": "9842100001"
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["patient"]["name"], "Ramasamy Gounder")
        print("✓ Patient login successful.")

    def test_06_manual_tamil_text_entry_payload_acceptance(self):
        """Verify manual Tamil text is accepted directly into journey creation."""
        res = self.app.post("/api/journeys", json={
            "patient_id": "PID-2026-1001",
            "patient_name": "Ramasamy Gounder",
            "age": 68,
            "contact_number": "9842100001",
            "village": "செட்டிபாளையம்",
            "preferred_service_id": "SRV-GEN",
            "preferred_date": "2026-11-30",
            "facility_id": "FAC-01",
            "transport_required": False,
            "symptoms": "காய்ச்சல் மற்றும் உடல் வலி (Manual Tamil Text Entry)"
        })
        self.assertIn(res.status_code, (200, 201))
        data = res.get_json()
        self.assertTrue(data["success"])
        j = data["journey"]
        self.assertIn("காய்ச்சல்", j["patient_reported_symptoms"])
        print(f"✓ Manual Tamil text entry verified in journey: {j['id']}")

if __name__ == "__main__":
    unittest.main()
