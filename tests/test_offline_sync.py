import unittest
import json
import uuid
import random
from app import app
from database import get_db

class TestOfflineSync(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.client.testing = True
        self.db = get_db()
        # Reset FAC-01 to verified for fresh test starts
        self.client.post("/api/facilities/FAC-01/verify", 
                         headers={"X-User-Username": "admin@ruralcare.gov.in"},
                         json={"verification_status": "VERIFIED"})

    def test_01_offline_batch_all_five_request_types(self):
        """Verify processing of all 5 distinct request types in offline batch."""
        uid_base = f"off_test_{uuid.uuid4().hex[:6]}"
        phone_rand = f"98421{random.randint(10000, 99999)}"

        batch = [
            # 1. APPOINTMENT (Standard Care Journey)
            {
                "client_uuid": f"{uid_base}_apt",
                "request_type": "APPOINTMENT",
                "patient_name": "Lakshmi Ammal",
                "age": 62,
                "contact_number": phone_rand,
                "village": "Chettipalayam",
                "preferred_service_id": "SRV-GEN",
                "preferred_service_name": "General Medicine",
                "preferred_date": "2026-11-20",
                "facility_id": "FAC-01",
                "facility_name": "Pollachi Government Taluk Hospital",
                "transport_required": True,
                "consent_given": True
            },
            # 2. HOSPITAL_CHECK (Facility & Service Recheck)
            {
                "client_uuid": f"{uid_base}_hosp",
                "request_type": "HOSPITAL_CHECK",
                "facility_id": "FAC-01",
                "preferred_service_id": "SRV-GEN",
                "consent_given": True
            },
            # 3. TRANSPORT (Standalone Transport Coordination)
            {
                "client_uuid": f"{uid_base}_trans",
                "request_type": "TRANSPORT",
                "patient_name": "Chinnasamy K.",
                "contact_number": f"98421{random.randint(10000, 99999)}",
                "village": "Melur Rural",
                "pickup_landmark": "Melur Bus Stand",
                "facility_id": "FAC-03",
                "facility_name": "Madurai District Referral Hospital",
                "consent_given": True
            },
            # 4. MEDICINE (Pharmacy Stock Replenishment)
            {
                "client_uuid": f"{uid_base}_med",
                "request_type": "MEDICINE",
                "patient_name": "Ramasamy V.",
                "contact_number": f"98421{random.randint(10000, 99999)}",
                "village": "Negamam",
                "medicines": "Metformin 500mg, Atorvastatin 10mg",
                "consent_given": True
            },
            # 5. COMMUNITY_DEMAND (Demand Radar Signal)
            {
                "client_uuid": f"{uid_base}_dem",
                "request_type": "COMMUNITY_DEMAND",
                "village": "Valparai Hills",
                "preferred_service_id": "SRV-EYE",
                "preferred_service_name": "Ophthalmology / Eye Care Camp",
                "notes": "Elderly tea plantation workers requesting mobile cataract checkup.",
                "consent_given": True
            }
        ]

        res = self.client.post("/api/sync/batch", json={"items": batch})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["results"]), 5)

        # Verify APPOINTMENT status: waits for facility acceptance
        apt_res = next(r for r in data["results"] if r["client_uuid"] == f"{uid_base}_apt")
        self.assertEqual(apt_res["status"], "SYNCED")
        self.assertEqual(apt_res["processing_status"], "FACILITY_VERIFICATION_PENDING")
        self.assertTrue(apt_res["journey_id"].startswith("RC-"))

        # Verify HOSPITAL_CHECK status
        hosp_res = next(r for r in data["results"] if r["client_uuid"] == f"{uid_base}_hosp")
        self.assertEqual(hosp_res["status"], "SYNCED")
        self.assertEqual(hosp_res["processing_status"], "FACILITY_SERVICE_VERIFIED")

        # Verify TRANSPORT status: waits for driver acceptance
        trans_res = next(r for r in data["results"] if r["client_uuid"] == f"{uid_base}_trans")
        self.assertEqual(trans_res["status"], "SYNCED")
        self.assertEqual(trans_res["processing_status"], "DRIVER_ACCEPTANCE_PENDING")
        self.assertTrue(trans_res["transport_request_id"].startswith("TR-"))

        # Verify MEDICINE status: waits for pharmacy stock confirmation
        med_res = next(r for r in data["results"] if r["client_uuid"] == f"{uid_base}_med")
        self.assertEqual(med_res["status"], "SYNCED")
        self.assertEqual(med_res["processing_status"], "PHARMACY_STOCK_PENDING")
        self.assertTrue(med_res["medicine_request_id"].startswith("MED-"))

        # Verify COMMUNITY_DEMAND status: aggregated into Demand Radar
        dem_res = next(r for r in data["results"] if r["client_uuid"] == f"{uid_base}_dem")
        self.assertEqual(dem_res["status"], "SYNCED")
        self.assertEqual(dem_res["processing_status"], "COMMUNITY_DEMAND_RECORDED")

        print("✓ All 5 offline request types verified with request-specific server processing statuses.")

    def test_02_idempotency_and_duplicate_prevention(self):
        """Verify re-synchronization of duplicate UUID returns DUPLICATE_ACKNOWLEDGED without creating duplicate records."""
        uid = f"off_dup_{uuid.uuid4().hex[:6]}"
        item = {
            "client_uuid": uid,
            "request_type": "APPOINTMENT",
            "patient_name": "Perumal S.",
            "age": 55,
            "contact_number": f"98421{random.randint(10000, 99999)}",
            "village": "Omalur",
            "preferred_service_id": "SRV-GEN",
            "preferred_service_name": "General Care",
            "preferred_date": "2026-11-25",
            "facility_id": "FAC-01",
            "consent_given": True
        }

        # 1st sync
        res1 = self.client.post("/api/sync/batch", json={"items": [item]})
        data1 = res1.get_json()
        self.assertEqual(data1["results"][0]["status"], "SYNCED")
        journey_id_1 = data1["results"][0]["journey_id"]

        # 2nd sync (e.g. client retried)
        res2 = self.client.post("/api/sync/batch", json={"items": [item]})
        data2 = res2.get_json()
        self.assertEqual(data2["results"][0]["status"], "DUPLICATE_ACKNOWLEDGED")
        self.assertEqual(data2["results"][0]["journey_id"], journey_id_1)

        # Confirm exactly 1 record exists in database
        records = self.db.collection("care_journeys").where("client_uuid", "==", uid)
        self.assertEqual(len(records), 1)
        print("✓ Idempotency and duplicate prevention verified across multiple sync cycles.")

    def test_03_invalid_and_expired_request_handling(self):
        """Verify invalid inputs and expired dates are handled safely."""
        uid_inv = f"off_inv_{uuid.uuid4().hex[:6]}"
        invalid_item = {
            "client_uuid": uid_inv,
            "patient_name": "", # invalid empty name
            "contact_number": "123", # invalid phone
            "village": "Melur",
            "preferred_service_id": "SRV-GEN"
        }

        res = self.client.post("/api/sync/batch", json={"items": [invalid_item]})
        data = res.get_json()
        self.assertEqual(data["results"][0]["status"], "VALIDATION_FAILED")

        # Expired preferred date: date in the past
        uid_exp = f"off_exp_{uuid.uuid4().hex[:6]}"
        expired_item = {
            "client_uuid": uid_exp,
            "patient_name": "Muthuvel K.",
            "age": 68,
            "contact_number": f"98421{random.randint(10000, 99999)}",
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            "preferred_date": "2020-01-01", # Past expired date
            "facility_id": "FAC-01",
            "consent_given": True
        }

        res_exp = self.client.post("/api/sync/batch", json={"items": [expired_item]})
        data_exp = res_exp.get_json()
        self.assertEqual(data_exp["results"][0]["status"], "ACTION_REQUIRED")
        self.assertIn("expired", data_exp["results"][0]["message"].lower())
        print("✓ Validation failure and expired past appointment dates safely handled.")

    def test_04_emergency_keyword_override(self):
        """Verify medical emergency symptoms bypass routine offline sync with immediate 108 guidance."""
        uid_emg = f"off_emg_{uuid.uuid4().hex[:6]}"
        emergency_item = {
            "client_uuid": uid_emg,
            "patient_name": "Emergency Patient",
            "contact_number": f"98421{random.randint(10000, 99999)}",
            "village": "Omalur",
            "preferred_service_name": "General Medicine",
            "notes": "Patient experiencing severe chest pain and breathlessness (மாரடைப்பு அவசரம்)",
            "consent_given": True
        }

        res = self.client.post("/api/sync/batch", json={"items": [emergency_item]})
        data = res.get_json()
        emg_result = data["results"][0]
        self.assertEqual(emg_result["status"], "EMERGENCY_OVERRIDE")
        self.assertEqual(emg_result["emergency_contact"], "108")
        self.assertIn("108", emg_result["message"])
        print("✓ Emergency symptoms trigger immediate 108 ambulance dispatch override.")

    def test_05_privacy_and_audio_rejection(self):
        """Verify strict privacy protection: attempted transmission of raw audio recordings is rejected."""
        uid_priv = f"off_priv_{uuid.uuid4().hex[:6]}"
        audio_violating_item = {
            "client_uuid": uid_priv,
            "patient_name": "Privacy Test",
            "contact_number": f"98421{random.randint(10000, 99999)}",
            "village": "Chettipalayam",
            "audio_blob": "base64_raw_microphone_bytes_should_never_be_stored",
            "consent_given": True
        }

        res = self.client.post("/api/sync/batch", json={"items": [audio_violating_item]})
        data = res.get_json()
        self.assertEqual(data["results"][0]["status"], "REJECTED")
        self.assertIn("recordings must not be transmitted", data["results"][0]["error"])
        print("✓ Privacy protection verified: raw audio recordings strictly prohibited.")

    def test_06_sync_status_lookup_endpoint(self):
        """Verify GET /api/sync/status/<client_uuid> returns correct server processing status."""
        uid_check = f"off_chk_{uuid.uuid4().hex[:6]}"
        phone = f"98421{random.randint(10000, 99999)}"
        item = {
            "client_uuid": uid_check,
            "patient_name": "Sundaramurthy A.",
            "contact_number": phone,
            "village": "Thirumangalam West",
            "preferred_service_id": "SRV-DIA",
            "preferred_service_name": "Dialysis & Renal Care",
            "facility_id": "FAC-04",
            "consent_given": True
        }

        # Check status before sync -> 404
        res_before = self.client.get(f"/api/sync/status/{uid_check}")
        self.assertEqual(res_before.status_code, 404)

        # Sync
        self.client.post("/api/sync/batch", json={"items": [item]})

        # Check status after sync -> 200 with masked name
        res_after = self.client.get(f"/api/sync/status/{uid_check}")
        self.assertEqual(res_after.status_code, 200)
        data = res_after.get_json()
        self.assertTrue(data["found"])
        self.assertEqual(data["type"], "CARE_JOURNEY")
        self.assertEqual(data["village"], "Thirumangalam West")
        self.assertTrue(data["patient_name_masked"].endswith("****"))
        print("✓ Offline request sync status lookup endpoint verified with privacy masking.")

if __name__ == "__main__":
    unittest.main()
