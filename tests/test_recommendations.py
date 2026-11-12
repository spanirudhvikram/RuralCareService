import unittest
import json
import random
from datetime import datetime, timezone, timedelta
from app import app
from database import get_db

class RuralCareAlternativeRecommendationsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.db = get_db()
        self.staff_headers = {
            "Content-Type": "application/json",
            "X-User-Username": "dr.revathi@pollachigh.tn.gov.in"
        }
        self.admin_headers = {
            "Content-Type": "application/json",
            "X-User-Username": "admin@ruralcare.gov.in"
        }

    def test_01_exact_service_matching(self):
        """Verify recommendations match the exact requested service."""
        res = self.client.get("/api/facilities/recommendations?service=SRV-EYE")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("service"), "SRV-EYE")
        recs = data.get("recommendations", [])
        self.assertGreater(len(recs), 0)
        for r in recs:
            self.assertIn("SRV-EYE", r.get("available_services", []))

    def test_02_exclusion_of_primary_facility(self):
        """Verify exclusion of unavailable or failed primary facility."""
        res = self.client.get("/api/facilities/recommendations?service=SRV-EYE&exclude_facility=FAC-01")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        for r in data.get("recommendations", []):
            self.assertNotEqual(r.get("id"), "FAC-01", "Primary excluded facility FAC-01 must not appear in recommendations")

    def test_03_expired_verification_exclusion(self):
        """Verify facilities with expired verification are never returned as VERIFIED ALTERNATIVE."""
        # Ensure FAC-04 is expired
        now_utc = datetime.now(timezone.utc)
        expired_time = (now_utc - timedelta(hours=2)).isoformat()
        self.db.collection("facilities").document("FAC-04").update({
            "verification_status": "EXPIRED",
            "verification_valid_until": expired_time
        })

        res = self.client.get("/api/facilities/recommendations?service=SRV-GEN&exclude_facility=FAC-01")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        
        fac_04 = next((r for r in data.get("recommendations", []) if r.get("id") == "FAC-04"), None)
        if fac_04:
            self.assertNotEqual(fac_04.get("recommendation_label"), "VERIFIED ALTERNATIVE")
            self.assertEqual(fac_04.get("recommendation_label"), "UNVERIFIED OPTION")
            self.assertFalse(fac_04.get("is_confirmed_available"))

    def test_04_unverified_facilities_not_shown_as_confirmed(self):
        """Verify unverified facilities are labelled UNVERIFIED OPTION and not shown as confirmed availability."""
        res = self.client.get("/api/facilities/recommendations?service=SRV-GEN")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        
        fac_05 = next((r for r in data.get("recommendations", []) if r.get("id") == "FAC-05"), None)
        self.assertIsNotNone(fac_05, "FAC-05 should be evaluated as an unverified option for general medicine")
        self.assertEqual(fac_05.get("recommendation_label"), "UNVERIFIED OPTION")
        self.assertFalse(fac_05.get("is_confirmed_available"))

    def test_05_correct_ranking_when_coordinates_exist(self):
        """Verify closer facility is ranked higher when valid coordinates exist."""
        # Chettipalayam: lat=10.9230, lng=77.0390
        # FAC-06 (Coimbatore GMC) ~11.8 km vs FAC-03 (Madurai GRH) ~162.1 km
        res = self.client.get("/api/facilities/recommendations?service=SRV-EYE&exclude_facility=FAC-01&village=Chettipalayam")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        recs = data.get("recommendations", [])
        self.assertGreaterEqual(len(recs), 2)
        
        # FAC-06 should be ranked first due to smaller distance
        first = recs[0]
        second = recs[1]
        self.assertEqual(first.get("id"), "FAC-06")
        self.assertEqual(second.get("id"), "FAC-03")
        self.assertIsNotNone(first.get("distance_km"))
        self.assertIsNotNone(second.get("distance_km"))
        self.assertLess(first["distance_km"], second["distance_km"])

    def test_06_safe_behaviour_when_coordinates_absent(self):
        """Verify no distance is invented when coordinates are absent, falling back safely to district/freshness."""
        res = self.client.get("/api/facilities/recommendations?service=SRV-EYE&exclude_facility=FAC-01&district=Coimbatore")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        recs = data.get("recommendations", [])
        self.assertGreaterEqual(len(recs), 2)
        
        # When no reference coordinates are passed, distance_km must be None (do not invent distance)
        for r in recs:
            self.assertIsNone(r.get("distance_km"))
            self.assertIsNone(r.get("distance_text"))
            self.assertFalse(r.get("has_coordinates"))

        # FAC-06 is in Coimbatore district, FAC-03 is in Madurai.
        # FAC-06 should still rank first because of matching district priority!
        self.assertEqual(recs[0].get("id"), "FAC-06")
        self.assertTrue(recs[0].get("is_same_district"))

    def test_07_empty_results_when_no_match(self):
        """Verify clear empty result when no facility offers requested service."""
        res = self.client.get("/api/facilities/recommendations?service=SRV-NONEXISTENT")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("total_found"), 0)
        self.assertEqual(data.get("verified_count"), 0)
        self.assertFalse(data.get("has_verified_alternatives"))
        self.assertEqual(len(data.get("recommendations")), 0)

    def test_08_appointment_rejection_triggering_recommendations(self):
        """Verify appointment rejection marks journey and recommendations return valid alternative."""
        now_utc = datetime.now(timezone.utc)
        jid = f"RC-2026-TEST-{random.randint(1000, 9999)}"
        journey_data = {
            "id": jid,
            "patient_name": "Kandasamy P.",
            "age": 74,
            "contact_number": "9842199999",
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-EYE",
            "preferred_service_name": "Ophthalmology / Eye Care",
            "preferred_date": "2026-11-20",
            "facility_id": "FAC-01",
            "facility_name": "Pollachi Government Taluk Hospital",
            "status": "APPOINTMENT_PENDING",
            "appointment_confirmed": False,
            "created_at": now_utc.isoformat(),
            "updated_at": now_utc.isoformat(),
            "transport_required": False
        }
        self.db.collection("care_journeys").document(jid).set(journey_data)

        # Facility staff rejects appointment
        reject_res = self.client.post(
            f"/api/journeys/{jid}/appointment-action",
            headers=self.staff_headers,
            json={"action": "reject", "notes": "Cataract surgeon unavailable on this date."}
        )
        self.assertEqual(reject_res.status_code, 200)
        rej_data = reject_res.get_json()
        self.assertEqual(rej_data.get("journey", {}).get("status"), "CANCELLED")

        # Now query recommendations excluding FAC-01
        rec_res = self.client.get(f"/api/facilities/recommendations?service=SRV-EYE&exclude_facility=FAC-01&village=Chettipalayam")
        self.assertEqual(rec_res.status_code, 200)
        rec_data = rec_res.get_json()
        self.assertTrue(rec_data.get("has_verified_alternatives"))
        recs = rec_data.get("recommendations", [])
        self.assertTrue(any(r["id"] == "FAC-06" for r in recs))

    def test_09_patient_confirmation_before_creating_new_request(self):
        """Verify rebooking requires patient confirmation payload, creates new linked journey, and does NOT auto-confirm appointment."""
        now_utc = datetime.now(timezone.utc)
        jid = f"RC-2026-TEST-{random.randint(1000, 9999)}"
        journey_data = {
            "id": jid,
            "patient_name": "Muthusamy C.",
            "age": 70,
            "contact_number": "9842188888",
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-EYE",
            "preferred_service_name": "Ophthalmology / Eye Care",
            "preferred_date": "2026-11-22",
            "facility_id": "FAC-01",
            "facility_name": "Pollachi Government Taluk Hospital",
            "status": "CANCELLED",
            "cancellation_reason": "Facility doctor unavailable",
            "appointment_confirmed": False,
            "created_at": now_utc.isoformat(),
            "updated_at": now_utc.isoformat(),
            "transport_required": True,
            "pickup_landmark": "Chettipalayam Bus Stand"
        }
        self.db.collection("care_journeys").document(jid).set(journey_data)

        # Attempt without alternative_facility_id should fail
        bad_res = self.client.post(f"/api/journeys/{jid}/rebook-alternative", json={})
        self.assertEqual(bad_res.status_code, 400)

        # Patient confirms selection of FAC-06
        rebook_res = self.client.post(
            f"/api/journeys/{jid}/rebook-alternative",
            json={"alternative_facility_id": "FAC-06"}
        )
        self.assertEqual(rebook_res.status_code, 201)
        rebook_data = rebook_res.get_json()
        self.assertTrue(rebook_data.get("success"))
        new_j = rebook_data.get("new_journey", {})
        
        # New journey ID must be distinct
        self.assertNotEqual(new_j.get("id"), jid)
        # Must link to original journey
        self.assertEqual(new_j.get("linked_original_journey_id"), jid)
        self.assertEqual(new_j.get("facility_id"), "FAC-06")
        # Crucial requirement: appointment is NOT accepted automatically
        self.assertFalse(new_j.get("appointment_confirmed"))
        self.assertIsNone(new_j.get("appointment_slot"))
        self.assertEqual(new_j.get("status"), "APPOINTMENT_PENDING")

    def test_10_original_journey_history_preservation(self):
        """Verify original journey record and its event history are strictly preserved when rebooking to an alternative."""
        now_utc = datetime.now(timezone.utc)
        jid = f"RC-2026-TEST-{random.randint(1000, 9999)}"
        journey_data = {
            "id": jid,
            "patient_name": "Subbulakshmi R.",
            "age": 65,
            "contact_number": "9842177777",
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-EYE",
            "preferred_service_name": "Ophthalmology / Eye Care",
            "preferred_date": "2026-11-25",
            "facility_id": "FAC-01",
            "facility_name": "Pollachi Government Taluk Hospital",
            "status": "CANCELLED",
            "cancellation_reason": "Facility doctor on emergency leave",
            "appointment_confirmed": False,
            "created_at": now_utc.isoformat(),
            "updated_at": now_utc.isoformat(),
            "transport_required": False
        }
        self.db.collection("care_journeys").document(jid).set(journey_data)

        # Add initial timeline event
        from api.readiness import log_journey_event
        log_journey_event(self.db, jid, "JOURNEY_CREATED", "Patient", "Initial journey created")
        log_journey_event(self.db, jid, "APPOINTMENT_REJECTED", "Staff", "Specialist unavailable")

        # Rebook alternative
        rebook_res = self.client.post(
            f"/api/journeys/{jid}/rebook-alternative",
            json={"alternative_facility_id": "FAC-06"}
        )
        self.assertEqual(rebook_res.status_code, 201)
        new_jid = rebook_res.get_json().get("new_journey", {}).get("id")

        # Original journey record must still exist
        orig_doc = self.db.collection("care_journeys").document(jid).get()
        self.assertTrue(orig_doc.exists)
        orig_data = orig_doc.to_dict()
        self.assertEqual(orig_data.get("id"), jid)
        self.assertEqual(orig_data.get("rebooked_to_journey_id"), new_jid)
        self.assertEqual(orig_data.get("status"), "CANCELLED")

        # Verify event history on original journey is preserved and appended
        events = self.db.collection("journey_events").where("journey_id", "==", jid)
        event_types = [e.to_dict().get("event_type") for e in events]
        self.assertIn("JOURNEY_CREATED", event_types)
        self.assertIn("APPOINTMENT_REJECTED", event_types)
        self.assertIn("ALTERNATIVE_FACILITY_SELECTED", event_types)

if __name__ == "__main__":
    unittest.main()
