import unittest
import json
import uuid
import random
from app import app
from database import get_db

class RuralCareTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
        self.db = get_db()

    def test_01_health_and_emergency(self):
        res = self.app.get("/api/health")
        data = json.loads(res.data)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(data["status"], "healthy")

        res_em = self.app.get("/api/emergency")
        em_data = json.loads(res_em.data)
        self.assertEqual(res_em.status_code, 200)
        numbers = [c["number"] for c in em_data["contacts"]]
        self.assertIn("108", numbers)
        self.assertIn("112", numbers)
        print("✓ Health check and emergency dispatch info verified.")

    def test_02_facilities_and_services(self):
        res = self.app.get("/api/facilities")
        data = json.loads(res.data)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(len(data["facilities"]) >= 5)

        # Check statuses
        statuses = [f["verification_status"] for f in data["facilities"]]
        self.assertIn("VERIFIED", statuses)
        self.assertIn("EXPIRED", statuses)
        self.assertIn("UNVERIFIED", statuses)
        print("✓ Healthcare facilities and verification statuses verified.")

    def test_03_patient_assisted_registration_and_readiness_engine(self):
        # Ensure facility FAC-01 is verified initially
        self.app.post("/api/facilities/FAC-01/verify", 
                      headers={"X-User-Username": "admin@ruralcare.gov.in"},
                      json={
                          "verification_status": "VERIFIED",
                          "valid_hours": 48,
                          "verified_by": "Dr. K. Revathi (CMO)"
                      })

        unique_phone = f"98421{random.randint(10000, 99999)}"
        payload = {
            "patient_name": "Kuppusamy M.",
            "age": 72,
            "contact_number": unique_phone,
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            "preferred_service_name": "General Medicine",
            "preferred_date": "2026-11-14",
            "facility_id": "FAC-01",
            "facility_name": "Pollachi Government Taluk Hospital",
            "registration_method": "ASSISTED_SERVICE",
            "registration_channel": "TOUCH_BASED",
            "operator_id": "KIOSK-OPERATOR-CP01",
            "operator_name": "Selvam",
            "transport_required": True,
            "pickup_landmark": "Chettipalayam Panchayat Library"
        }

        # 1. Create Journey
        res = self.app.post("/api/journeys", json=payload)
        data = json.loads(res.data)
        self.assertEqual(res.status_code, 201)
        self.assertTrue(data["success"])
        journey = data["journey"]
        jid = journey["id"]
        self.assertTrue(jid.startswith("RC-2026-"))
        self.assertEqual(journey["registration_method"], "ASSISTED_SERVICE")
        self.assertEqual(journey["operator_id"], "KIOSK-OPERATOR-CP01")
        print(f"✓ Journey created: {jid} via ASSISTED_SERVICE.")

        # 2. Check initial readiness: should NOT be ready yet because appointment & driver pending
        readiness = data["readiness"]
        self.assertFalse(readiness["is_ready_for_departure"])
        self.assertIn(journey["status"], ["FACILITY_VERIFICATION_PENDING", "APPOINTMENT_PENDING"])

        # 3. Duplicate prevention test: submit same patient, phone, date, service
        dup_res = self.app.post("/api/journeys", json=payload)
        dup_data = json.loads(dup_res.data)
        self.assertTrue(dup_data.get("is_duplicate", False))
        print("✓ Duplicate request prevention verified.")

        # 4. Facility Staff confirms appointment
        staff_res = self.app.post(
            f"/api/journeys/{jid}/appointment-action",
            headers={"X-User-Username": "dr.revathi@pollachigh.tn.gov.in"},
            json={
                "action": "accept",
                "staff_name": "Dr. K. Revathi (CMO)",
                "appointment_slot": "09:30 AM Room 3",
                "notes": "Elderly patient priority"
            }
        )
        staff_data = json.loads(staff_res.data)
        self.assertTrue(staff_data["success"])
        self.assertTrue(staff_data["journey"]["appointment_confirmed"])
        print("✓ Facility appointment confirmed.")

        # 5. Driver accepts transport request
        tr_id = journey["transport_request_id"]
        drv_res = self.app.post(
            f"/api/transport/requests/{tr_id}/accept",
            headers={"X-User-Username": "9842177101"},
            json={
                "driver_id": "DRV-01",
                "driver_name": "Murugan S.",
                "driver_phone": "+91 98421 77101",
                "vehicle_reg": "TN-38-AX-4412",
                "fare_amount": 160
            }
        )
        drv_data = json.loads(drv_res.data)
        self.assertTrue(drv_data["success"])
        print("✓ Driver accepted transport and quoted fare ₹160.")

        # 6. Patient accepts disclosed fare
        fare_res = self.app.post(
            f"/api/journeys/{jid}/patient-accept-fare",
            headers={"X-Verify-Phone": unique_phone},
            json={"accepted": True}
        )
        fare_data = json.loads(fare_res.data)
        self.assertTrue(fare_data["success"])
        print("✓ Patient fare acceptance recorded.")

        # 7. Patient confirms readiness to travel
        ready_res = self.app.post(
            f"/api/journeys/{jid}/patient-readiness",
            headers={"X-Verify-Phone": unique_phone},
            json={"patient_readiness_confirmed": True}
        )
        ready_data = json.loads(ready_res.data)
        self.assertTrue(ready_data["success"])
        print("✓ Patient departure readiness confirmed.")

        # 8. Check Journey Status: ONLY the backend readiness engine elevated to COORDINATION_CONFIRMED!
        check_res = self.app.get(f"/api/journeys/{jid}?verify_phone={unique_phone}")
        final_data = json.loads(check_res.data)
        final_journey = final_data["journey"]
        self.assertEqual(final_journey["status"], "COORDINATION_CONFIRMED")
        self.assertTrue(final_data["readiness"]["is_ready_for_departure"])
        print("✓ Departure Readiness Check: COORDINATION_CONFIRMED verified successfully!")

        # 9. Expired Verification test: if facility verification expires, status drops to RECHECK_REQUIRED
        self.app.post(
            f"/api/facilities/{journey['facility_id']}/verify",
            headers={"X-User-Username": "admin@ruralcare.gov.in"},
            json={
                "verification_status": "EXPIRED",
                "valid_hours": 0
            }
        )
        recheck_res = self.app.get(f"/api/journeys/{jid}?verify_phone={unique_phone}")
        recheck_data = json.loads(recheck_res.data)
        self.assertEqual(recheck_data["journey"]["status"], "RECHECK_REQUIRED")
        self.assertFalse(recheck_data["readiness"]["is_ready_for_departure"])
        print("✓ Verification expiration correctly triggered RECHECK_REQUIRED.")

    def test_04_driver_privacy(self):
        """Verify that drivers never receive confidential medical details in transport lists."""
        res = self.app.get("/api/transport/requests")
        data = json.loads(res.data)
        self.assertTrue(data["success"])
        for req in data["requests"]:
            self.assertNotIn("preferred_service_id", req)
            self.assertNotIn("medical_notes", req)
            self.assertNotIn("diagnosis", req)
        print("✓ Driver privacy constraints strictly enforced.")

    def test_05_offline_batch_sync(self):
        """Verify offline sync queue processing and duplicate handling."""
        test_uuid = f"offline_uuid_{uuid.uuid4().hex[:8]}"
        batch_items = [
            {
                "client_uuid": test_uuid,
                "patient_name": "Meenakshi Ammal",
                "age": 60,
                "contact_number": f"98421{random.randint(10000, 99999)}",
                "village": "Melur Rural",
                "preferred_service_id": "SRV-EYE",
                "preferred_service_name": "Ophthalmology / Eye Care",
                "preferred_date": "2026-11-15",
                "facility_id": "FAC-03",
                "facility_name": "Madurai District Referral Medical College Hospital",
                "transport_required": False
            }
        ]
        res = self.app.post("/api/sync/batch", json={"items": batch_items})
        data = json.loads(res.data)
        self.assertTrue(data["success"])
        self.assertEqual(data["results"][0]["status"], "SYNCED")

        # Second sync of same item should report duplicate acknowledged safely
        res_dup = self.app.post("/api/sync/batch", json={"items": batch_items})
        data_dup = json.loads(res_dup.data)
        self.assertEqual(data_dup["results"][0]["status"], "DUPLICATE_ACKNOWLEDGED")
        print("✓ Offline batch synchronization & idempotency verified.")

if __name__ == "__main__":
    unittest.main()
