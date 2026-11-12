import unittest
import json
import random
import uuid
from app import app
from database import get_db

class RuralCareSecurityAndAuditTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
        self.db = get_db()
        # Reset FAC-01 to verified for fresh test starts
        self.app.post("/api/facilities/FAC-01/verify", 
                      headers={"X-User-Username": "admin@ruralcare.gov.in"},
                      json={"verification_status": "VERIFIED", "valid_hours": 48})

    def test_01_backend_controls_coordination_confirmed(self):
        """Verify client cannot inject COORDINATION_CONFIRMED status directly."""
        unique_phone = f"98421{random.randint(10000, 99999)}"
        payload = {
            "patient_name": "Tamilarasi S.",
            "age": 45,
            "contact_number": unique_phone,
            "village": "Omalur",
            "preferred_service_id": "SRV-GEN",
            "facility_id": "FAC-01",
            "status": "COORDINATION_CONFIRMED", # malicious status injection attempt
            "service_verified": True # malicious flag injection
        }
        res = self.app.post("/api/journeys", json=payload)
        data = json.loads(res.data)
        self.assertEqual(res.status_code, 201)
        # Server MUST ignore injected status and evaluate through Departure Readiness Engine
        self.assertNotEqual(data["journey"]["status"], "COORDINATION_CONFIRMED")
        self.assertEqual(data["journey"]["status"], "APPOINTMENT_PENDING")
        self.assertFalse(data["readiness"]["is_ready_for_departure"])
        print("✓ Backend alone controls status: malicious status injection prevented.")

    def test_02_id_guessing_privacy_protection(self):
        """Verify unauthenticated user cannot view patient private details by guessing Journey ID."""
        unique_phone = f"98421{random.randint(10000, 99999)}"
        payload = {
            "patient_name": "Subramanian V.",
            "age": 70,
            "contact_number": unique_phone,
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            "facility_id": "FAC-01"
        }
        res = self.app.post("/api/journeys", json=payload)
        jid = json.loads(res.data)["journey"]["id"]

        # 1. Unauthenticated stranger guesses ID without phone verification
        stranger_res = self.app.get(f"/api/journeys/{jid}")
        stranger_data = json.loads(stranger_res.data)
        self.assertTrue(stranger_data["is_restricted_preview"])
        # Name and phone MUST be masked
        self.assertNotIn("Subramanian V.", stranger_data["journey"]["patient_name"])
        self.assertIn("****", stranger_data["journey"]["patient_name"])
        self.assertIn("XXXXXX", stranger_data["journey"]["contact_number"])
        self.assertEqual(stranger_data["journey"]["age"], "**")
        print("✓ Patient privacy protected: strangers guessing Journey ID receive restricted masked preview.")

        # 2. Legitimate patient provides verification phone
        verified_res = self.app.get(f"/api/journeys/{jid}?verify_phone={unique_phone}")
        verified_data = json.loads(verified_res.data)
        self.assertFalse(verified_data["is_restricted_preview"])
        self.assertEqual(verified_data["journey"]["patient_name"], "Subramanian V.")
        self.assertEqual(verified_data["journey"]["contact_number"], unique_phone)
        print("✓ Verified patient unlocks full journey record with phone verification.")

    def test_03_role_based_access_control(self):
        """Verify endpoints reject unauthorized or unauthenticated actions."""
        # 1. Unauthenticated facility verification must fail (401)
        unauth_fac = self.app.post("/api/facilities/FAC-01/verify", json={"verification_status": "EXPIRED"})
        self.assertEqual(unauth_fac.status_code, 401)
        print("✓ Facility verification requires authentication (401 rejected).")

        # 2. Facility staff cannot verify a hospital they do not belong to (403)
        # Dr. Karthik is staff at FAC-02 (Omalur CHC), attempting to verify FAC-01 (Pollachi GH)
        wrong_fac = self.app.post(
            "/api/facilities/FAC-01/verify",
            headers={"X-User-Username": "dr.karthik@omalurchc.tn.gov.in"},
            json={"verification_status": "VERIFIED", "valid_hours": 24}
        )
        self.assertEqual(wrong_fac.status_code, 403)
        print("✓ Hospital staff boundary enforced: cross-facility tampering rejected (403).")

        # 3. Unauthenticated driver ride acceptance must fail (401)
        unauth_drv = self.app.post("/api/transport/requests/TR-1001/accept", json={"fare_amount": 150})
        self.assertEqual(unauth_drv.status_code, 401)
        print("✓ Transport acceptance requires driver authentication (401 rejected).")

        # 4. Non-admin accessing admin portal endpoints must fail (401/403)
        unauth_admin = self.app.get("/api/admin/overview")
        self.assertEqual(unauth_admin.status_code, 401)

        staff_admin = self.app.get("/api/admin/overview", headers={"X-User-Username": "dr.revathi@pollachigh.tn.gov.in"})
        self.assertEqual(staff_admin.status_code, 403)
        print("✓ Admin endpoints strictly restricted to district administrators.")

    def test_04_driver_data_privacy(self):
        """Verify driver transport queue strictly masks patient phone and contains NO medical service info."""
        res = self.app.get("/api/transport/requests")
        data = json.loads(res.data)
        self.assertTrue(data["success"])
        for req in data["requests"]:
            phone = req.get("patient_phone", "")
            self.assertTrue(phone.startswith("XXXXXX") or phone == "Protected Contact")
            self.assertNotIn("preferred_service_id", req)
            self.assertNotIn("preferred_service_name", req)
            self.assertNotIn("medical_notes", req)
        print("✓ Driver queue privacy: patient phone masked and medical service details completely omitted.")

    def test_05_input_sanitization_and_validation(self):
        """Verify input validation against script injection, invalid ages, and malformed phone numbers."""
        # 1. Script injection in patient name
        res_xss = self.app.post("/api/journeys", json={
            "patient_name": "<script>alert('pwned')</script>Marimuthu",
            "age": 55,
            "contact_number": "9842100099",
            "village": "Melur Rural",
            "preferred_service_id": "SRV-GEN",
            "facility_id": "FAC-01"
        })
        self.assertEqual(res_xss.status_code, 201)
        saved_name = json.loads(res_xss.data)["journey"]["patient_name"]
        self.assertNotIn("<script>", saved_name)
        self.assertNotIn("</script>", saved_name)
        print("✓ XSS injection sanitized from patient name.")

        # 2. Invalid negative age
        res_age = self.app.post("/api/journeys", json={
            "patient_name": "Valid Name",
            "age": -5,
            "contact_number": "9842100099",
            "village": "Melur Rural",
            "preferred_service_id": "SRV-GEN"
        })
        self.assertEqual(res_age.status_code, 400)
        print("✓ Invalid age rejected.")

        # 3. Invalid non-10-digit phone
        res_phone = self.app.post("/api/journeys", json={
            "patient_name": "Valid Name",
            "age": 45,
            "contact_number": "12345",
            "village": "Melur Rural",
            "preferred_service_id": "SRV-GEN"
        })
        self.assertEqual(res_phone.status_code, 400)
        print("✓ Invalid phone length rejected.")

    def test_06_lifecycle_rejections_and_failures(self):
        """Test appointment rejection, driver cancellation, and verification expiry status transitions."""
        unique_phone = f"98421{random.randint(10000, 99999)}"
        # 1. Test appointment rejection by hospital
        res = self.app.post("/api/journeys", json={
            "patient_name": "Gomathi N.",
            "age": 50,
            "contact_number": unique_phone,
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            "facility_id": "FAC-01",
            "transport_required": True
        })
        j_data = json.loads(res.data)["journey"]
        jid = j_data["id"]

        reject_res = self.app.post(
            f"/api/journeys/{jid}/appointment-action",
            headers={"X-User-Username": "dr.revathi@pollachigh.tn.gov.in"},
            json={"action": "reject", "notes": "Specialist on leave"}
        )
        self.assertEqual(reject_res.status_code, 200)
        refreshed = json.loads(reject_res.data)["journey"]
        self.assertEqual(refreshed["status"], "CANCELLED")
        self.assertIn("Specialist on leave", refreshed.get("cancellation_reason", ""))
        print("✓ Appointment rejection correctly transitions status to CANCELLED.")

        # 2. Test driver cancellation transition to TRANSPORT_FAILED
        unique_phone_2 = f"98421{random.randint(10000, 99999)}"
        res2 = self.app.post("/api/journeys", json={
            "patient_name": "Palanisamy T.",
            "age": 62,
            "contact_number": unique_phone_2,
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-GEN",
            "facility_id": "FAC-01",
            "transport_required": True
        })
        j2 = json.loads(res2.data)["journey"]
        tr_id = j2["transport_request_id"]

        cancel_drv_res = self.app.post(
            f"/api/transport/requests/{tr_id}/cancel",
            headers={"X-User-Username": "9842177101"},
            json={"reason": "Tire puncture on rural transit route"}
        )
        self.assertEqual(cancel_drv_res.status_code, 200)
        check_j2 = self.app.get(f"/api/journeys/{j2['id']}?verify_phone={unique_phone_2}")
        j2_data = json.loads(check_j2.data)["journey"]
        self.assertEqual(j2_data["status"], "TRANSPORT_FAILED")
        print("✓ Driver cancellation correctly transitions status to TRANSPORT_FAILED.")

if __name__ == "__main__":
    unittest.main()
