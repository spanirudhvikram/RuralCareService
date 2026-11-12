import unittest
import json
import time
import random
import uuid
from datetime import datetime, timezone, timedelta
from app import app
from database import get_db

class TestCompleteWorkflowIntegration(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.db = get_db()
        self.phone = f"9842{int(time.time() * 1000) % 1000000:06d}"

    # -------------------------------------------------------------------------
    # STEP 1 & 2: Patient Registration & Unique Patient ID
    # -------------------------------------------------------------------------
    def test_step_01_02_register_patient_and_id_assignment(self):
        """Step 1 & 2: Register new patient and verify unique Patient ID assigned."""
        reg_payload = {
            "name": "Meenakshi Sundaram",
            "age": 62,
            "contact_number": self.phone,
            "village": "Negamam",
            "consent_given": True
        }
        res = self.client.post("/api/patients/register", json=reg_payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data["success"])
        patient = data["patient"]
        self.assertIn("PID-2026-", patient["id"])
        self.assertEqual(patient["name"], "Meenakshi Sundaram")
        self.assertEqual(patient["age"], 62)
        self.assertEqual(patient["contact_number"], self.phone)
        self.assertEqual(patient["village"], "Negamam")
        self.assertTrue(patient["consent_given"])
        print(f"✓ Step 1 & 2 PASSED: Registered patient assigned ID {patient['id']}")

    # -------------------------------------------------------------------------
    # STEP 3: Returning Patient Login & Security Verification
    # -------------------------------------------------------------------------
    def test_step_03_login_returning_patient_and_security(self):
        """Step 3: Login as returning patient with phone verification, reconnecting records."""
        # Pre-register patient
        self.client.post("/api/patients/register", json={
            "name": "Meenakshi Sundaram",
            "age": 62,
            "contact_number": self.phone,
            "village": "Negamam",
            "consent_given": True
        })

        # Mismatched phone verification must be rejected (anti-enumeration check)
        bad_res = self.client.post("/api/patients/login", json={
            "contact_number": self.phone,
            "phone_verification": "9999"
        })
        self.assertEqual(bad_res.status_code, 401)
        self.assertFalse(bad_res.get_json()["success"])

        # Correct phone verification succeeds and returns patient profile
        good_res = self.client.post("/api/patients/login", json={
            "contact_number": self.phone,
            "phone_verification": self.phone[-4:] # Last 4 digits
        })
        self.assertEqual(good_res.status_code, 200)
        data = good_res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["patient"]["contact_number"], self.phone)
        self.assertIn("journeys", data)
        self.assertIn("prescriptions", data)
        print(f"✓ Step 3 PASSED: Secure returning patient login verified with anti-enumeration protection")

    # -------------------------------------------------------------------------
    # STEP 4: Voice-Assisted Care Journey Request
    # -------------------------------------------------------------------------
    def test_step_04_voice_request_creation(self):
        """Step 4: Voice request created with General Medicine, Valparai Hills, transport needed."""
        payload = {
            "patient_name": "Meenakshi Sundaram",
            "age": 62,
            "contact_number": self.phone,
            "village": "Valparai Hills",
            "preferred_service_id": "SRV-GEN",
            "preferred_service_name": "General Medicine",
            "patient_reported_symptoms": "Recurring joint swelling and mild fever",
            "registration_method": "SELF_SERVICE",
            "registration_channel": "VOICE_ASSISTED",
            "transport_required": True,
            "pickup_choice": "RURALCARE_CENTRE",
            "consent_given": True
        }
        res = self.client.post("/api/journeys", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data["success"])
        journey = data["journey"]
        self.assertIn("RC-2026-", journey["id"])
        self.assertTrue(journey["transport_required"])
        self.assertEqual(journey["pickup_choice"], "RURALCARE_CENTRE")
        self.assertEqual(journey["patient_reported_symptoms"], "Recurring joint swelling and mild fever")
        self.assertIsNotNone(journey["patient_id"])
        print(f"✓ Step 4 PASSED: Voice request created: {journey['id']} linked to {journey['patient_id']}")

    # -------------------------------------------------------------------------
    # STEP 5: Automatic Hospital Discovery
    # -------------------------------------------------------------------------
    def test_step_05_automatic_hospital_discovery(self):
        """Step 5: Automatic hospital discovery returns verified facilities ranked by proximity & freshness."""
        res = self.client.get("/api/facilities/recommendations?service=SRV-GEN&village=Valparai+Hills")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertGreater(data["total_found"], 0)
        top = data["recommendations"][0]
        self.assertEqual(top["recommendation_label"], "VERIFIED ALTERNATIVE")
        self.assertTrue(top["is_confirmed_available"])
        self.assertIsNotNone(top["distance_km"])
        print(f"✓ Step 5 PASSED: Hospital discovery matched {top['name']} ({top['distance_text']})")

    # -------------------------------------------------------------------------
    # STEP 6, 7 & 8: Doctor Portal Review, Urgency & Slot Assignment
    # -------------------------------------------------------------------------
    def test_step_06_07_08_doctor_review_and_slot_assignment(self):
        """Step 6, 7 & 8: Doctor reviews patient, sets clinical urgency URGENT, assigns slot, checks double booking."""
        test_date = f"2027-01-{(int(time.time() * 1000) % 25) + 1:02d}"
        slot_name = f"10:30 AM - Slot {int(time.time() * 1000) % 100000}"
        # 1. Create journey at FAC-01
        create_res = self.client.post("/api/journeys", json={
            "patient_name": "Meenakshi Sundaram",
            "age": 62,
            "contact_number": self.phone,
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            "facility_id": "FAC-01",
            "preferred_date": test_date,
            "transport_required": True,
            "consent_given": True
        })
        jid = create_res.get_json()["journey"]["id"]

        # 2. Doctor logs in and reviews assigned journey
        doc_res = self.client.post("/api/facilities/doctor/review", json={
            "journey_id": jid,
            "doctor_name": "Dr. K. Revathi (CMO)",
            "action": "accept",
            "clinical_urgency": "URGENT",
            "appointment_slot": slot_name,
            "doctor_notes": "Clinical review: urgent consultation indicated. Vitals monitoring required.",
            "prescription_notes": "Paracetamol 650mg, 1 tablet as needed\nAmoxicillin 500mg, 1 tablet twice daily, 5 days"
        }, headers={"X-User-Username": "dr.revathi@pollachigh.tn.gov.in"})

        self.assertEqual(doc_res.status_code, 200)
        doc_data = doc_res.get_json()
        self.assertTrue(doc_data["success"])
        self.assertEqual(doc_data["clinical_urgency"], "URGENT")
        self.assertEqual(doc_data["slot"], slot_name)
        j_refreshed = doc_data["journey"]
        self.assertEqual(j_refreshed["clinical_urgency"], "URGENT")
        self.assertIsNotNone(j_refreshed["doctor_assessment"])
        self.assertTrue(j_refreshed["prescription_uploaded"])

        # 3. Double-booking prevention check: another patient requesting SAME slot on SAME date at FAC-01
        create_res2 = self.client.post("/api/journeys", json={
            "patient_name": "Second Patient",
            "age": 55,
            "contact_number": "9842199002",
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            "facility_id": "FAC-01",
            "preferred_date": test_date,
            "transport_required": False,
            "consent_given": True
        })
        jid2 = create_res2.get_json()["journey"]["id"]

        conflict_res = self.client.post("/api/facilities/doctor/review", json={
            "journey_id": jid2,
            "doctor_name": "Dr. K. Revathi",
            "action": "accept",
            "clinical_urgency": "ROUTINE",
            "appointment_slot": slot_name # Same slot on test_date!
        }, headers={"X-User-Username": "dr.revathi@pollachigh.tn.gov.in"})

        self.assertEqual(conflict_res.status_code, 409)
        self.assertEqual(conflict_res.get_json()["code"], "SLOT_CONFLICT")
        print(f"✓ Step 6, 7 & 8 PASSED: Doctor review assigned URGENT & 10:30 AM slot; double-booking rejected with 409")

    # -------------------------------------------------------------------------
    # STEP 9: Patient Confirms Slot
    # -------------------------------------------------------------------------
    def test_step_09_patient_confirms_appointment_slot(self):
        """Step 9: Patient confirms acceptance of doctor-assigned slot."""
        test_date = f"2027-02-{(int(time.time() * 1000) % 25) + 1:02d}"
        c_res = self.client.post("/api/journeys", json={
            "patient_name": "Meenakshi Sundaram",
            "age": 62,
            "contact_number": self.phone,
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            "facility_id": "FAC-01",
            "preferred_date": test_date,
            "consent_given": True
        })
        jid = c_res.get_json()["journey"]["id"]

        slot_str = f"11:00 AM - Slot {int(time.time() * 1000) % 100000}"
        doc_res = self.client.post("/api/facilities/doctor/review", json={
            "journey_id": jid,
            "doctor_name": "Dr. K. Revathi",
            "action": "accept",
            "clinical_urgency": "ROUTINE",
            "appointment_slot": slot_str
        }, headers={"X-User-Username": "dr.revathi@pollachigh.tn.gov.in"})
        self.assertEqual(doc_res.status_code, 200)

        confirm_res = self.client.post(f"/api/journeys/{jid}/patient-confirm-slot")
        self.assertEqual(confirm_res.status_code, 200)
        self.assertTrue(confirm_res.get_json()["success"])
        self.assertTrue(confirm_res.get_json()["journey"]["appointment_confirmed"])
        print(f"✓ Step 9 PASSED: Patient confirmed doctor-assigned slot")

    # -------------------------------------------------------------------------
    # STEP 10: Transport Pickup Choice
    # -------------------------------------------------------------------------
    def test_step_10_transport_pickup_location_choice(self):
        """Step 10: Patient chooses pickup at RuralCare Self-Service Centre vs Home."""
        res = self.client.post("/api/transport/requests/TR-1001/pickup-location", json={
            "pickup_choice": "RURALCARE_CENTRE"
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["pickup_choice"], "RURALCARE_CENTRE")
        self.assertIn("RuralCare Self-Service Centre", data["pickup_landmark"])
        print(f"✓ Step 10 PASSED: Transport pickup choice set to {data['pickup_landmark']}")

    # -------------------------------------------------------------------------
    # STEP 11, 12 & 13: Driver Acceptance, Fare Confirmation & Departure Readiness
    # -------------------------------------------------------------------------
    def test_step_11_12_13_driver_acceptance_fare_and_readiness(self):
        """Step 11, 12 & 13: Driver accepts with ₹140 fare quote, patient accepts -> COORDINATION_CONFIRMED."""
        test_date = f"2027-04-{(int(time.time() * 1000) % 25) + 1:02d}"
        # 1. Create fresh journey
        c_res = self.client.post("/api/journeys", json={
            "patient_name": "Meenakshi Sundaram",
            "age": 62,
            "contact_number": self.phone,
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            "facility_id": "FAC-01",
            "transport_required": True,
            "preferred_date": test_date,
            "consent_given": True
        })
        j_data = c_res.get_json()["journey"]
        jid = j_data["id"]
        tr_id = j_data["transport_request_id"]

        # 2. Doctor accepts
        doc_res = self.client.post("/api/facilities/doctor/review", json={
            "journey_id": jid,
            "doctor_name": "Dr. K. Revathi",
            "action": "accept",
            "clinical_urgency": "ROUTINE",
            "appointment_slot": f"09:30 AM Slot {random.randint(1000, 9999)}"
        }, headers={"X-User-Username": "dr.revathi@pollachigh.tn.gov.in"})
        self.assertEqual(doc_res.status_code, 200)

        # 3. Driver Murugan accepts and quotes fare ₹140
        drv_res = self.client.post(f"/api/transport/requests/{tr_id}/accept", json={
            "driver_id": "DRV-01",
            "driver_name": "Murugan S.",
            "driver_phone": "+91 98421 77101",
            "vehicle_reg": "TN-38-AX-4412",
            "fare_amount": 140
        }, headers={"X-User-Username": "9842177101"})
        self.assertEqual(drv_res.status_code, 200)

        # 4. Patient accepts fare
        fare_res = self.client.post(f"/api/journeys/{jid}/patient-accept-fare", json={"accepted": True})
        self.assertEqual(fare_res.status_code, 200)

        # 5. Patient readiness confirmed
        r_res = self.client.post(f"/api/journeys/{jid}/patient-readiness", json={"patient_readiness_confirmed": True})
        self.assertEqual(r_res.status_code, 200)

        # 6. Check Departure Readiness Gate -> COORDINATION_CONFIRMED
        j_doc = self.client.get(f"/api/journeys/{jid}?verify_phone={self.phone}").get_json()["journey"]
        self.assertEqual(j_doc["status"], "COORDINATION_CONFIRMED")
        self.assertTrue(j_doc["departure_readiness_check"]["is_ready_for_departure"])
        print(f"✓ Step 11, 12 & 13 PASSED: Fare ₹140 accepted; Departure Readiness confirmed: COORDINATION_CONFIRMED")

    # -------------------------------------------------------------------------
    # STEP 14: Driver In-Transit & Completion
    # -------------------------------------------------------------------------
    def test_step_14_driver_pickup_and_journey_completion(self):
        """Step 14: Driver picks up patient -> IN_TRANSIT -> COMPLETED."""
        test_date = f"2027-05-{(int(time.time() * 1000) % 25) + 1:02d}"
        # Pre-seed confirmed journey
        c_res = self.client.post("/api/journeys", json={
            "patient_name": "Meenakshi Sundaram",
            "age": 62,
            "contact_number": self.phone,
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            "facility_id": "FAC-01",
            "transport_required": True,
            "preferred_date": test_date,
            "consent_given": True
        })
        j_data = c_res.get_json()["journey"]
        jid = j_data["id"]
        tr_id = j_data["transport_request_id"]

        # Doctor accepts, driver accepts, fare accepted, patient ready
        doc_res = self.client.post("/api/facilities/doctor/review", json={"journey_id": jid, "doctor_name": "Dr. Revathi", "action": "accept", "appointment_slot": "10:00 AM"}, headers={"X-User-Username": "dr.revathi@pollachigh.tn.gov.in"})
        self.assertEqual(doc_res.status_code, 200)
        self.client.post(f"/api/transport/requests/{tr_id}/accept", json={"driver_id": "DRV-01", "driver_name": "Murugan", "fare_amount": 140}, headers={"X-User-Username": "9842177101"})
        self.client.post(f"/api/journeys/{jid}/patient-accept-fare", json={"accepted": True})
        self.client.post(f"/api/journeys/{jid}/patient-readiness", json={"patient_readiness_confirmed": True})

        # Mark in transit
        self.client.post(f"/api/transport/requests/{tr_id}/status", json={"status": "IN_TRANSIT"}, headers={"X-User-Username": "9842177101"})
        # Mark completed
        comp_res = self.client.post(f"/api/transport/requests/{tr_id}/status", json={"status": "COMPLETED"}, headers={"X-User-Username": "9842177101"})
        self.assertEqual(comp_res.status_code, 200)

        j_doc = self.client.get(f"/api/journeys/{jid}?verify_phone={self.phone}").get_json()["journey"]
        self.assertEqual(j_doc["status"], "COMPLETED")
        print(f"✓ Step 14 PASSED: Driver transit completed; journey reached COMPLETED")

    # -------------------------------------------------------------------------
    # STEP 15 & 16: Prescription & Pharmacy Order
    # -------------------------------------------------------------------------
    def test_step_15_16_prescription_and_pharmacy_ordering(self):
        """Step 15 & 16: View prescription, verify pharmacy stock, and place order."""
        # 1. Fetch prescription RX-2026-1001
        rx_res = self.client.get("/api/prescriptions/RX-2026-1001")
        self.assertEqual(rx_res.status_code, 200)
        rx = rx_res.get_json()["prescription"]
        self.assertEqual(rx["clinical_urgency"], "ROUTINE")

        # 2. Check pharmacy stock
        pharm_res = self.client.get("/api/prescriptions/pharmacies?village=Chettipalayam")
        self.assertEqual(pharm_res.status_code, 200)
        pharmacies = pharm_res.get_json()["pharmacies"]
        self.assertGreater(len(pharmacies), 0)

        # 3. Confirm pharmacy order for pickup
        order_res = self.client.post("/api/prescriptions/RX-2026-1001/order", json={
            "pharmacy_id": "PHARM-01",
            "fulfillment_type": "PICKUP"
        })
        self.assertEqual(order_res.status_code, 201)
        ord_data = order_res.get_json()["order"]
        self.assertIn("ORD-2026-", ord_data["id"])
        self.assertEqual(ord_data["fulfillment_type"], "PICKUP")
        print(f"✓ Step 15 & 16 PASSED: Pharmacy order confirmed: {ord_data['id']}")

    # -------------------------------------------------------------------------
    # STEP 17: 30-Day Refill Reminder
    # -------------------------------------------------------------------------
    def test_step_17_schedule_refill_reminder(self):
        """Step 17: Schedule 30-day chronic medication refill reminder."""
        res = self.client.post("/api/prescriptions/RX-2026-1001/refill-reminder", json={"reminder_days": 30})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIsNotNone(data["due_date"])
        print(f"✓ Step 17 PASSED: 30-day refill reminder scheduled for {data['due_date']}")

    # -------------------------------------------------------------------------
    # STEP 18: Village Health Demand Radar Integration
    # -------------------------------------------------------------------------
    def test_step_18_demand_radar_integration_and_outreach_proposal(self):
        """Step 18: Verify demand radar aggregates unmet needs, elevates priority, and generates proposals."""
        res = self.client.get("/api/radar/demand-clusters")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertGreater(data["summary"]["total_unmet_demand"], 0)

        # Check clusters
        clusters = data["clusters"]
        high_clusters = [c for c in clusters if c["priority"] == "HIGH"]
        self.assertGreater(len(high_clusters), 0)
        top_clust = high_clusters[0]

        # Generate outreach proposal
        prop_res = self.client.post("/api/radar/outreach-proposals/generate", json={
            "cluster_id": top_clust["cluster_id"]
        }, headers={"X-User-Username": "admin@ruralcare.gov.in"})
        self.assertIn(prop_res.status_code, (200, 201))
        prop_data = prop_res.get_json()
        self.assertTrue(prop_data["success"])
        print(f"✓ Step 18 PASSED: Demand Radar identified cluster {top_clust['cluster_id']} (Priority: {top_clust['priority']}) and generated outreach proposal")

    # -------------------------------------------------------------------------
    # STEP 19: Tamil Voice Quality & TTS Pause Cleaning
    # -------------------------------------------------------------------------
    def test_step_19_tamil_voice_quality_and_tts(self):
        """Step 19: Verify Microsoft Azure Neural Tamil TTS (ta-IN-PallaviNeural & ta-IN-ValluvarNeural)."""
        # Test 1: Voices endpoint
        v_res = self.client.get("/api/tts/voices")
        self.assertEqual(v_res.status_code, 200)
        v_data = v_res.get_json()
        self.assertEqual(v_data.get("default_voice"), "ta-IN-PallaviNeural")

        # Test 2: Synthesize official Sentence 1 with PallaviNeural
        s1 = "வணக்கம்! ரூரல்கேர் மருத்துவ சேவைக்கு உங்களை வரவேற்கிறோம்."
        synth_res = self.client.post("/api/tts/synthesize", json={
            "text": s1,
            "voice": "ta-IN-PallaviNeural"
        })
        self.assertEqual(synth_res.status_code, 200)
        self.assertEqual(synth_res.mimetype, "audio/mpeg")
        self.assertGreater(len(synth_res.data), 1000)
        self.assertEqual(synth_res.headers.get("X-TTS-Voice"), "ta-IN-PallaviNeural")

        # Test 3: Synthesize with ValluvarNeural alternative
        synth_alt = self.client.post("/api/tts/synthesize", json={
            "text": "உங்கள் பெயரைச் சொல்லுங்கள்.",
            "voice": "ta-IN-ValluvarNeural"
        })
        self.assertEqual(synth_alt.status_code, 200)
        self.assertEqual(synth_alt.mimetype, "audio/mpeg")
        print(f"✓ Step 19 PASSED: Microsoft Azure Neural Tamil TTS verified for Pallavi & Valluvar")

    # -------------------------------------------------------------------------
    # STEP 20: Offline Resilience & Batch Sync
    # -------------------------------------------------------------------------
    def test_step_20_offline_batch_synchronization(self):
        """Step 20: Offline request synced with client_uuid idempotency and duplicate prevention."""
        uuid_key = f"offline_test_{datetime.now().timestamp()}"
        sync_payload = {
            "items": [
                {
                    "client_uuid": uuid_key,
                    "request_type": "APPOINTMENT",
                    "patient_name": "Meenakshi Sundaram",
                    "age": 62,
                    "contact_number": self.phone,
                    "village": "Chettipalayam",
                    "preferred_service_id": "SRV-GEN",
                    "preferred_service_name": "General Medicine",
                    "preferred_date": (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d"),
                    "facility_id": "FAC-01",
                    "consent_given": True
                }
            ]
        }
        res = self.client.post("/api/sync/batch", json=sync_payload)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["results"][0]["status"], "SYNCED")

        # Second sync attempt: must acknowledge duplicate without re-creating
        res2 = self.client.post("/api/sync/batch", json=sync_payload)
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.get_json()["results"][0]["status"], "DUPLICATE_ACKNOWLEDGED")
        print(f"✓ Step 20 PASSED: Offline sync and duplicate prevention verified")

if __name__ == "__main__":
    unittest.main()
