"""
RuralCare End-to-End Validation:
Part A: Doctor Appointment Synchronisation & Persistence
Part B & C: Conversational Voice Automation & Transport Integration
"""
import unittest
import json
import random
from app import app
from database import get_db

class TestDoctorAndConversationalVoice(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.client = app.test_client()
        self.db = get_db()

    def test_01_patient_registration_generates_valid_pid(self):
        """Verify new patient registration produces a unique, persistent PID-2026-XXXX."""
        unique_phone = f"98421{random.randint(10000, 99999)}"
        payload = {
            "name": "ஆனந்தன் குமார்",
            "age": 58,
            "contact_number": unique_phone,
            "village": "Chettipalayam",
            "consent_given": True
        }
        res = self.client.post("/api/patients/register", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        pid = data["patient"]["id"]
        self.assertTrue(pid.startswith("PID-2026-"))
        self.assertEqual(data["patient"]["name"], "ஆனந்தன் குமார்")

        # Verify duplicate registration returns existing record
        dup_res = self.client.post("/api/patients/register", json=payload)
        self.assertEqual(dup_res.status_code, 200)
        dup_data = dup_res.get_json()
        self.assertTrue(dup_data.get("is_returning"))
        self.assertEqual(dup_data["patient"]["id"], pid)

    def test_02_voice_journey_creation_and_auto_pickup_point(self):
        """
        Verify conversational voice journey creation:
        - Auto-assigns RuralCare Self-Service Centre (Chettipalayam)
        - Never requires separate pickup location entry
        - Saves requested pickup time and patient reported symptoms
        """
        phone = f"98422{random.randint(10000, 99999)}"
        payload = {
            "patient_name": "செல்வராஜ் முத்துசாமி",
            "age": 63,
            "contact_number": phone,
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            "preferred_service_name": "General Medicine",
            "patient_reported_symptoms": "காய்ச்சல் மற்றும் கடுமையான தலைவலி (Severe fever & headache)",
            "preferred_date": "2026-11-20",
            "facility_id": "FAC-01",
            "facility_name": "Pollachi Government Taluk Hospital",
            "registration_method": "SELF_SERVICE",
            "registration_channel": "VOICE_ASSISTED",
            "transport_required": True,
            "requested_pickup_time": "09:30 AM",
            "consent_given": True
        }
        res = self.client.post("/api/journeys", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        journey = data["journey"]
        jid = journey["id"]
        self.assertTrue(jid.startswith("RC-2026-"))
        self.assertTrue(journey["patient_id"].startswith("PID-2026-"))
        self.assertEqual(journey["pickup_choice"], "RURALCARE_CENTRE")
        self.assertIn("RuralCare Self-Service Centre", journey["pickup_landmark"])
        self.assertEqual(journey["requested_pickup_time"], "09:30 AM")
        self.assertEqual(journey["patient_reported_symptoms"], "காய்ச்சல் மற்றும் கடுமையான தலைவலி (Severe fever & headache)")

        # Verify transport request was automatically created with matching details
        tr_id = journey.get("transport_request_id")
        self.assertIsNotNone(tr_id)
        tr_doc = self.db.collection("transport_requests").document(tr_id).get()
        self.assertTrue(tr_doc.exists)
        tr_data = tr_doc.to_dict()
        self.assertEqual(tr_data["requested_pickup_time"], "09:30 AM")
        self.assertIn("RuralCare Self-Service Centre", tr_data["pickup_landmark"])
        self.assertEqual(tr_data["status"], "REQUESTED")

    def test_03_doctor_assigned_journeys_shows_complete_patient_details(self):
        """
        Verify doctor portal endpoint returns:
        - Patient Name, Patient ID, Age, Village
        - Patient Reported Symptoms
        - Requested Time & Preferred Date
        - Clinical Urgency & Appointment Status
        """
        phone = f"98423{random.randint(10000, 99999)}"
        # 1. Register patient
        reg_res = self.client.post("/api/patients/register", json={
            "name": "மீனாட்சி அம்மாள்",
            "age": 71,
            "contact_number": phone,
            "village": "Chettipalayam",
            "consent_given": True
        })
        pid = reg_res.get_json()["patient"]["id"]

        # 2. Create Care Journey for Pollachi GH (FAC-01)
        j_res = self.client.post("/api/journeys", json={
            "patient_id": pid,
            "patient_name": "மீனாட்சி அம்மாள்",
            "age": 71,
            "contact_number": phone,
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-EYE",
            "preferred_service_name": "Eye Care / Ophthalmology",
            "patient_reported_symptoms": "கண் பார்வை மங்கலாக தெரிகிறது (Blurry vision)",
            "preferred_date": "2026-11-22",
            "facility_id": "FAC-01",
            "facility_name": "Pollachi Government Taluk Hospital",
            "registration_method": "SELF_SERVICE",
            "registration_channel": "VOICE_ASSISTED",
            "transport_required": True,
            "requested_pickup_time": "10:00 AM"
        })
        jid = j_res.get_json()["journey"]["id"]

        # 3. Retrieve facility assigned journeys as Doctor
        fac_res = self.client.get("/api/facilities/FAC-01/assigned-journeys")
        self.assertEqual(fac_res.status_code, 200)
        fac_data = fac_res.get_json()
        self.assertTrue(fac_data.get("success"))
        journeys = fac_data.get("journeys", [])
        
        target = next((j for j in journeys if j["id"] == jid), None)
        self.assertIsNotNone(target, f"Journey {jid} not found in facility assigned journeys")
        self.assertEqual(target["patient_id"], pid)
        self.assertEqual(target["patient_name"], "மீனாட்சி அம்மாள்")
        self.assertEqual(target["age"], 71)
        self.assertEqual(target["village"], "Chettipalayam")
        self.assertEqual(target["symptoms"], "கண் பார்வை மங்கலாக தெரிகிறது (Blurry vision)")
        self.assertEqual(target["requested_pickup_time"], "10:00 AM")
        self.assertEqual(target["status"], "APPOINTMENT_PENDING")

    def test_04_doctor_clinical_review_and_double_booking_prevention(self):
        """
        Verify Doctor Review:
        - Sets explicit clinical urgency (ROUTINE, URGENT, MONITORING)
        - Locks appointment slot
        - Prevents double booking on same slot and date
        """
        # Login as Doctor Revathi (CMO Pollachi GH)
        login_res = self.client.post("/api/auth/login", json={
            "username": "dr.revathi@pollachigh.tn.gov.in",
            "password": "staff"
        })
        self.assertEqual(login_res.status_code, 200)

        # Create two separate patient journeys for same facility & date
        phone1 = f"98424{random.randint(10000, 99999)}"
        phone2 = f"98425{random.randint(10000, 99999)}"

        test_date = f"2026-12-{random.randint(10, 28):02d}"
        target_slot = f"10:30 AM Room {random.randint(10, 99)}"
        alt_slot = f"11:30 AM Room {random.randint(10, 99)}"

        j1 = self.client.post("/api/journeys", json={
            "patient_name": "நோயாளி 1",
            "age": 50,
            "contact_number": phone1,
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            "preferred_date": test_date,
            "facility_id": "FAC-01",
            "patient_reported_symptoms": "Fever"
        }).get_json()["journey"]["id"]

        j2 = self.client.post("/api/journeys", json={
            "patient_name": "நோயாளி 2",
            "age": 52,
            "contact_number": phone2,
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            "preferred_date": test_date,
            "facility_id": "FAC-01",
            "patient_reported_symptoms": "Joint pain"
        }).get_json()["journey"]["id"]

        # Doctor accepts j1 for target_slot
        rev1 = self.client.post("/api/facilities/doctor/review", json={
            "journey_id": j1,
            "action": "accept",
            "clinical_urgency": "URGENT",
            "appointment_slot": target_slot,
            "doctor_notes": "Immediate OPD consultation scheduled."
        })
        self.assertEqual(rev1.status_code, 200)
        self.assertTrue(rev1.get_json()["success"])

        # Doctor attempts to book j2 on the EXACT same slot and date -> must reject with 409
        rev2_conflict = self.client.post("/api/facilities/doctor/review", json={
            "journey_id": j2,
            "action": "accept",
            "clinical_urgency": "ROUTINE",
            "appointment_slot": target_slot,
            "doctor_notes": "Attempting conflict slot."
        })
        self.assertEqual(rev2_conflict.status_code, 409)
        self.assertEqual(rev2_conflict.get_json()["code"], "SLOT_CONFLICT")

        # Doctor books j2 for alternative slot -> succeeds
        rev2_ok = self.client.post("/api/facilities/doctor/review", json={
            "journey_id": j2,
            "action": "accept",
            "clinical_urgency": "ROUTINE",
            "appointment_slot": alt_slot,
            "doctor_notes": "Confirmed alternative slot."
        })
        self.assertEqual(rev2_ok.status_code, 200)

    def test_05_transport_driver_flow_and_fare_acceptance(self):
        """
        Verify Transport Driver Coordination:
        - Driver views request with pickup landmark and ready time
        - Driver accepts and discloses fare quote
        - Patient must confirm fare before coordination is completed
        """
        phone = f"98426{random.randint(10000, 99999)}"
        j_res = self.client.post("/api/journeys", json={
            "patient_name": "சுந்தரம் பிள்ளை",
            "age": 60,
            "contact_number": phone,
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            "preferred_date": "2026-11-26",
            "facility_id": "FAC-01",
            "transport_required": True,
            "registration_channel": "VOICE_ASSISTED",
            "requested_pickup_time": "08:45 AM"
        })
        j_data = j_res.get_json()["journey"]
        tr_id = j_data["transport_request_id"]
        jid = j_data["id"]

        # Driver views requests
        t_list_res = self.client.get("/api/transport/requests")
        self.assertEqual(t_list_res.status_code, 200)
        requests = t_list_res.get_json()["requests"]
        matched_tr = next((r for r in requests if r["id"] == tr_id), None)
        self.assertIsNotNone(matched_tr)
        self.assertEqual(matched_tr["requested_pickup_time"], "08:45 AM")
        self.assertIn("RuralCare Self-Service Centre", matched_tr["pickup_landmark"])

        # Driver Murugan logs in and accepts with fare ₹150
        drv_login = self.client.post("/api/auth/login", json={
            "username": "9842177101",
            "password": "driver"
        })
        self.assertEqual(drv_login.status_code, 200)

        accept_res = self.client.post(f"/api/transport/requests/{tr_id}/accept", json={
            "driver_id": "DRV-01",
            "driver_name": "Murugan S.",
            "fare_amount": 150
        })
        self.assertEqual(accept_res.status_code, 200)
        self.assertEqual(accept_res.get_json()["fare_amount"], 150.0)

        # Before patient accepts fare, patient_fare_accepted is False
        check_j1 = self.client.get(f"/api/journeys/{jid}?verify_phone={phone}").get_json()["journey"]
        self.assertFalse(check_j1.get("patient_fare_accepted"))

        # Patient formally confirms fare acceptance
        fare_confirm_res = self.client.post(f"/api/transport/requests/{tr_id}/record-fare-acceptance", json={
            "patient_fare_accepted": True
        })
        self.assertEqual(fare_confirm_res.status_code, 200)

        # Verify journey now reflects confirmed fare
        check_j2 = self.client.get(f"/api/journeys/{jid}?verify_phone={phone}").get_json()["journey"]
        self.assertTrue(check_j2.get("patient_fare_accepted"))

    def test_06_persistence_across_page_refresh_and_session_restore(self):
        """
        Verify /api/auth/me and /api/patients/me persist state across reloads.
        """
        # 1. Staff login session persistence
        self.client.post("/api/auth/login", json={
            "username": "dr.revathi@pollachigh.tn.gov.in",
            "password": "staff"
        })
        me_res = self.client.get("/api/auth/me")
        me_data = me_res.get_json()
        self.assertTrue(me_data.get("authenticated"))
        self.assertEqual(me_data["user"]["role"], "facility_staff")
        self.assertEqual(me_data["user"]["facility_id"], "FAC-01")

        # 2. Patient session persistence
        phone = f"98427{random.randint(10000, 99999)}"
        self.client.post("/api/patients/register", json={
            "name": "சுப்பிரமணி",
            "age": 64,
            "contact_number": phone,
            "village": "Chettipalayam",
            "consent_given": True
        })
        pat_me_res = self.client.get("/api/patients/me")
        pat_me_data = pat_me_res.get_json()
        self.assertTrue(pat_me_data.get("success"))
        self.assertEqual(pat_me_data["patient"]["name"], "சுப்பிரமணி")
        self.assertEqual(pat_me_data["patient"]["contact_number"], phone)

if __name__ == "__main__":
    unittest.main()
