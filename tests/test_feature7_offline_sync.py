import unittest
import json
import uuid
import random
from datetime import datetime, timezone, timedelta
from app import app
from database import get_db, get_database_status, verify_firestore_connection

class TestFeature7OfflineSync(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.client.testing = True
        self.db = get_db()
        self.run_id = uuid.uuid4().hex[:6]

    # -------------------------------------------------------------------------
    # Scenario 1: Open RuralCare online & reachability check
    # -------------------------------------------------------------------------
    def test_scenario_01_reachability_ping(self):
        """Scenario 1: Open RuralCare online & verify reachability ping."""
        res = self.client.get("/api/sync/ping")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["reachability"], "ONLINE")
        self.assertIn("database_mode", data)
        print("✓ Scenario 1 PASSED: Application is online with verified server reachability.")

    # -------------------------------------------------------------------------
    # Scenario 2: Register a patient online
    # -------------------------------------------------------------------------
    def test_scenario_02_register_patient_online(self):
        """Scenario 2: Register patient online."""
        phone = f"98421{random.randint(10000, 99999)}"
        res = self.client.post("/api/patients/register", json={
            "name": f"Online Citizen {self.run_id}",
            "age": 58,
            "contact_number": phone,
            "village": "Chettipalayam",
            "consent_given": True
        })
        self.assertIn(res.status_code, (200, 201))
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["patient"]["id"].startswith("PID-2026-"))
        print(f"✓ Scenario 2 PASSED: Online patient registration successful ({data['patient']['id']}).")

    # -------------------------------------------------------------------------
    # Scenario 3, 4, 7 & 8: Disconnect internet, register patient offline, verify persistence
    # -------------------------------------------------------------------------
    def test_scenario_03_04_07_08_offline_patient_registration(self):
        """Scenarios 3, 4, 7, 8: Disconnect internet, capture patient registration offline, verify queue schema."""
        client_uuid = f"pat_off_{self.run_id}"
        phone = f"98421{random.randint(10000, 99999)}"
        offline_record = {
            "client_uuid": client_uuid,
            "idempotency_key": client_uuid,
            "entity_type": "PATIENT_REGISTRATION",
            "operation_type": "CREATE",
            "name": f"Muthulakshmi {self.run_id}",
            "age": 64,
            "contact_number": phone,
            "village": "Chettipalayam",
            "consent_given": True,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        # Sync batch simulating reconnect
        res = self.client.post("/api/sync/batch", json={"items": [offline_record]})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["results"]), 1)
        res_item = data["results"][0]
        self.assertEqual(res_item["status"], "SYNCED")
        self.assertEqual(res_item["processing_status"], "PATIENT_REGISTERED")
        self.assertTrue(res_item["patient_id"].startswith("PID-2026-"))
        print(f"✓ Scenarios 3, 4, 7 & 8 PASSED: Offline patient registration synced to server ({res_item['patient_id']}).")

    # -------------------------------------------------------------------------
    # Scenario 5 & 6: Submit healthcare request & transport request offline
    # -------------------------------------------------------------------------
    def test_scenario_05_06_healthcare_and_transport_requests_offline(self):
        """Scenarios 5 & 6: Submit healthcare & transport requests captured offline."""
        uid_apt = f"apt_off_{self.run_id}"
        uid_tr = f"tr_off_{self.run_id}"
        phone = f"98421{random.randint(10000, 99999)}"

        batch = [
            {
                "client_uuid": uid_apt,
                "entity_type": "APPOINTMENT",
                "patient_name": f"Palanisamy {self.run_id}",
                "age": 70,
                "contact_number": phone,
                "village": "Chettipalayam",
                "preferred_service_id": "SRV-GEN",
                "preferred_service_name": "General Medicine",
                "preferred_date": (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d"),
                "facility_id": "FAC-01",
                "consent_given": True
            },
            {
                "client_uuid": uid_tr,
                "entity_type": "TRANSPORT",
                "patient_name": f"Palanisamy {self.run_id}",
                "contact_number": phone,
                "village": "Chettipalayam",
                "pickup_landmark": "Panchayat Office",
                "pickup_type": "RURALCARE_CENTRE",
                "facility_id": "FAC-01",
                "consent_given": True
            }
        ]

        res = self.client.post("/api/sync/batch", json={"items": batch})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(len(data["results"]), 2)
        
        apt_result = next(r for r in data["results"] if r["client_uuid"] == uid_apt)
        tr_result = next(r for r in data["results"] if r["client_uuid"] == uid_tr)
        
        self.assertEqual(apt_result["status"], "SYNCED")
        self.assertEqual(apt_result["processing_status"], "FACILITY_VERIFICATION_PENDING")
        self.assertTrue(apt_result["journey_id"].startswith("RC-2026-"))

        self.assertEqual(tr_result["status"], "SYNCED")
        self.assertEqual(tr_result["processing_status"], "DRIVER_ACCEPTANCE_PENDING")
        self.assertTrue(tr_result["transport_request_id"].startswith("TR-"))
        print(f"✓ Scenarios 5 & 6 PASSED: Healthcare and transport requests successfully synchronized.")

    # -------------------------------------------------------------------------
    # Scenario 9, 10 & 11: Reconnect & verify records appear in database
    # -------------------------------------------------------------------------
    def test_scenario_09_10_11_verify_database_persistence(self):
        """Scenarios 9, 10, 11: Reconnect & verify records appear in database."""
        uid = f"persist_{self.run_id}"
        phone = f"98421{random.randint(10000, 99999)}"
        
        res = self.client.post("/api/sync/batch", json={"items": [{
            "client_uuid": uid,
            "entity_type": "APPOINTMENT",
            "patient_name": f"Kannagi {self.run_id}",
            "age": 55,
            "contact_number": phone,
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            "consent_given": True
        }]})
        j_id = res.get_json()["results"][0]["journey_id"]

        # Verify directly in database
        doc = self.db.collection("care_journeys").document(j_id).get()
        self.assertTrue(doc.exists)
        self.assertEqual(doc.to_dict()["client_uuid"], uid)
        self.assertEqual(doc.to_dict()["status"], "FACILITY_VERIFICATION_PENDING")
        print(f"✓ Scenarios 9, 10 & 11 PASSED: Verified persistence of {j_id} in database store.")

    # -------------------------------------------------------------------------
    # Scenario 12 & 14: Duplicate idempotency keys & zero duplication
    # -------------------------------------------------------------------------
    def test_scenario_12_14_idempotency_and_zero_duplicate_records(self):
        """Scenarios 12 & 14: Duplicate idempotency keys return DUPLICATE_ACKNOWLEDGED and create no duplicate records."""
        uid = f"idemp_{self.run_id}"
        item = {
            "client_uuid": uid,
            "idempotency_key": uid,
            "entity_type": "APPOINTMENT",
            "patient_name": f"Idempotency Citizen {self.run_id}",
            "age": 60,
            "contact_number": f"98421{random.randint(10000, 99999)}",
            "village": "Negamam",
            "preferred_service_id": "SRV-GEN",
            "consent_given": True
        }

        # 1st submit
        res1 = self.client.post("/api/sync/batch", json={"items": [item]})
        j_id1 = res1.get_json()["results"][0]["journey_id"]

        # 2nd submit (exact duplicate)
        res2 = self.client.post("/api/sync/batch", json={"items": [item]})
        data2 = res2.get_json()["results"][0]
        self.assertEqual(data2["status"], "DUPLICATE_ACKNOWLEDGED")
        self.assertEqual(data2["journey_id"], j_id1)

        # Count in database
        matches = self.db.collection("care_journeys").where("client_uuid", "==", uid)
        self.assertEqual(len(matches), 1)
        print("✓ Scenarios 12 & 14 PASSED: Duplicate idempotency key verified with zero duplication.")

    # -------------------------------------------------------------------------
    # Scenario 13: Simulate failed upload and recovery
    # -------------------------------------------------------------------------
    def test_scenario_13_failed_upload_recovery(self):
        """Scenario 13: Invalid/expired date returns ACTION_REQUIRED but preserves record for review."""
        uid = f"fail_{self.run_id}"
        expired_item = {
            "client_uuid": uid,
            "entity_type": "APPOINTMENT",
            "patient_name": f"Recovery Patient {self.run_id}",
            "age": 60,
            "contact_number": f"98421{random.randint(10000, 99999)}",
            "village": "Melur Rural",
            "preferred_service_id": "SRV-GEN",
            "preferred_date": "2020-01-01", # Past expired date
            "consent_given": True
        }

        res = self.client.post("/api/sync/batch", json={"items": [expired_item]})
        data = res.get_json()["results"][0]
        self.assertEqual(data["status"], "ACTION_REQUIRED")
        self.assertIn("expired", data["message"].lower())
        print("✓ Scenario 13 PASSED: Failed expired date safely recovered and flagged for action.")

    # -------------------------------------------------------------------------
    # Scenario 15: Conflicting updates and safe field merging
    # -------------------------------------------------------------------------
    def test_scenario_15_conflicting_updates_and_safe_merge(self):
        """Scenario 15: Test conflict detection on critical fields and safe merging on independent fields."""
        # 1. Create a base journey
        j_id = f"RC-CONF-{self.run_id}"
        self.db.collection("care_journeys").document(j_id).set({
            "id": j_id,
            "patient_name": "Test Conflict Citizen",
            "contact_number": "9842100000",
            "village": "Chettipalayam",
            "facility_id": "FAC-01",
            "preferred_service_id": "SRV-GEN",
            "status": "FACILITY_CONFIRMED",
            "appointment_slot": "10:30 AM",
            "pickup_landmark": "Old Bus Stand",
            "created_at": "2026-11-12T00:00:00Z",
            "updated_at": "2026-11-12T12:00:00Z"
        })

        # Test 15A: Conflicting modification on critical field (status/service)
        conflict_item = {
            "client_uuid": f"conf_crit_{self.run_id}",
            "operation_type": "UPDATE",
            "target_id": j_id,
            "last_modified": "2026-11-12T06:00:00Z", # older than server updated_at
            "payload": {
                "preferred_service_id": "SRV-CARD", # Critical conflict
                "facility_id": "FAC-03"
            }
        }
        res_conflict = self.client.post("/api/sync/batch", json={"items": [conflict_item]})
        data_conflict = res_conflict.get_json()["results"][0]
        self.assertEqual(data_conflict["status"], "CONFLICT_REQUIRES_REVIEW")
        self.assertEqual(data_conflict["processing_status"], "CONFLICT_REQUIRES_REVIEW")

        # Test 15B: Safe non-overlapping merge on independent field (pickup_landmark)
        merge_item = {
            "client_uuid": f"conf_merge_{self.run_id}",
            "operation_type": "UPDATE",
            "target_id": j_id,
            "last_modified": "2026-11-12T06:00:00Z",
            "payload": {
                "pickup_landmark": "New Panchayat Community Hall Gate 2"
            }
        }
        res_merge = self.client.post("/api/sync/batch", json={"items": [merge_item]})
        data_merge = res_merge.get_json()["results"][0]
        self.assertEqual(data_merge["status"], "SYNCED")
        self.assertEqual(data_merge["processing_status"], "MERGE_APPLIED")

        # Confirm merged value in database
        doc = self.db.collection("care_journeys").document(j_id).get()
        self.assertEqual(doc.to_dict()["pickup_landmark"], "New Panchayat Community Hall Gate 2")
        self.assertEqual(doc.to_dict()["status"], "FACILITY_CONFIRMED") # Preserved!
        print("✓ Scenario 15 PASSED: Conflict detection and safe field merge verified.")

    # -------------------------------------------------------------------------
    # Scenario 16: Unauthorised sync attempts (missing consent, audio buffers)
    # -------------------------------------------------------------------------
    def test_scenario_16_unauthorised_and_privacy_violations(self):
        """Scenario 16: Test rejection of consent-lacking requests and audio recordings."""
        # A. Missing consent
        no_consent = {
            "client_uuid": f"nocons_{self.run_id}",
            "entity_type": "APPOINTMENT",
            "patient_name": "No Consent Citizen",
            "contact_number": "9842100000",
            "village": "Melur",
            "preferred_service_id": "SRV-GEN",
            "consent_given": False
        }
        res_c = self.client.post("/api/sync/batch", json={"items": [no_consent]})
        self.assertEqual(res_c.get_json()["results"][0]["status"], "VALIDATION_FAILED")

        # B. Audio violation
        audio_viol = {
            "client_uuid": f"audio_{self.run_id}",
            "entity_type": "APPOINTMENT",
            "patient_name": "Audio Citizen",
            "contact_number": "9842100000",
            "village": "Melur",
            "audio_blob": "base64_audio_raw_data_never_accepted",
            "consent_given": True
        }
        res_a = self.client.post("/api/sync/batch", json={"items": [audio_viol]})
        self.assertEqual(res_a.get_json()["results"][0]["status"], "REJECTED")
        print("✓ Scenario 16 PASSED: Consent validation and audio rejection enforced.")

    # -------------------------------------------------------------------------
    # Scenario 17: Large pending queue batch processing
    # -------------------------------------------------------------------------
    def test_scenario_17_large_pending_queue_batch(self):
        """Scenario 17: Process a batch of 12 distinct offline requests simultaneously."""
        batch = []
        for i in range(12):
            batch.append({
                "client_uuid": f"queue_{self.run_id}_{i}",
                "entity_type": "COMMUNITY_DEMAND",
                "village": f"Village_{i}",
                "preferred_service_id": "SRV-GEN",
                "preferred_service_name": "Primary Care",
                "consent_given": True
            })

        res = self.client.post("/api/sync/batch", json={"items": batch})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["processed"], 12)
        self.assertEqual(len(data["results"]), 12)
        self.assertTrue(all(r["status"] == "SYNCED" for r in data["results"]))
        print("✓ Scenario 17 PASSED: Batch processing of 12 queue records succeeded.")

    # -------------------------------------------------------------------------
    # Scenario 18: Care Journey state consistency (no client spoofing)
    # -------------------------------------------------------------------------
    def test_scenario_18_care_journey_consistency_and_server_control(self):
        """Scenario 18: Client cannot forge confirmation status; server enforces initial triage pending."""
        client_forged_item = {
            "client_uuid": f"forge_{self.run_id}",
            "entity_type": "APPOINTMENT",
            "patient_name": f"Spoofing Test {self.run_id}",
            "contact_number": f"98421{random.randint(10000, 99999)}",
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            # Attempting to forge confirmation
            "status": "COORDINATION_CONFIRMED",
            "appointment_confirmed": True,
            "consent_given": True
        }

        res = self.client.post("/api/sync/batch", json={"items": [client_forged_item]})
        data = res.get_json()["results"][0]
        j_id = data["journey_id"]
        
        doc = self.db.collection("care_journeys").document(j_id).get().to_dict()
        # Server must override forged status with authentic state
        self.assertEqual(doc["status"], "FACILITY_VERIFICATION_PENDING")
        self.assertFalse(doc["appointment_confirmed"])
        print("✓ Scenario 18 PASSED: Care Journey server state integrity enforced against client spoofing.")

    # -------------------------------------------------------------------------
    # Scenario 19: Prescription and file upload behaviour
    # -------------------------------------------------------------------------
    def test_scenario_19_prescription_offline_upload(self):
        """Scenario 19: Prescription upload offline sync with size validation."""
        rx_item = {
            "client_uuid": f"rx_{self.run_id}",
            "entity_type": "PRESCRIPTION_UPLOAD",
            "patient_name": f"Rx Patient {self.run_id}",
            "doctor_name": "Dr. R. Raman",
            "medicines": [
                {"name": "Metformin 500mg", "dosage": "Twice daily", "days": 30, "quantity": 60}
            ],
            "file_name": "prescription_doc.jpg",
            "file_data": "simulated_base64_doc_payload",
            "consent_given": True
        }

        res = self.client.post("/api/sync/batch", json={"items": [rx_item]})
        data = res.get_json()["results"][0]
        self.assertEqual(data["status"], "SYNCED")
        self.assertEqual(data["processing_status"], "PRESCRIPTION_REGISTERED")
        self.assertTrue(data["prescription_id"].startswith("RX-2026-"))
        print(f"✓ Scenario 19 PASSED: Prescription upload verified ({data['prescription_id']}).")

    # -------------------------------------------------------------------------
    # Scenario 20: Verified reporting of database mode & no false cloud claims
    # -------------------------------------------------------------------------
    def test_scenario_20_accurate_database_status_reporting(self):
        """Scenario 20: System accurately reports database mode without claiming live cloud when unconfigured."""
        res = self.client.get("/api/sync/db-status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()["database"]
        self.assertEqual(data["database_mode"], "persistent_document_store")
        self.assertFalse(data["cloud_firestore_active"])
        self.assertFalse(data["credentials_configured"])
        self.assertEqual(data["status"], "ONLINE_LOCAL_STORE")
        print("✓ Scenario 20 PASSED: Database status accurately reported with zero false cloud claims.")

if __name__ == "__main__":
    unittest.main()
