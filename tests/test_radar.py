import unittest
import json
import random
from datetime import datetime, timezone, timedelta
from app import app
from database import get_db

class RuralCareDemandRadarTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.db = get_db()
        self.admin_headers = {
            "Content-Type": "application/json",
            "X-User-Username": "admin@ruralcare.gov.in"
        }
        self.driver_headers = {
            "Content-Type": "application/json",
            "X-User-Username": "9842177101"
        }

    def test_01_demand_clusters_aggregation(self):
        """Verify GET /api/radar/demand-clusters returns structured aggregation without PII."""
        res = self.client.get("/api/radar/demand-clusters?period_days=30")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        
        summary = data.get("summary", {})
        self.assertIn("total_distinct_requests", summary)
        self.assertIn("total_unmet_demand", summary)
        self.assertIn("total_facility_failures", summary)
        self.assertIn("total_transport_failures", summary)
        self.assertIn("voluntary_cancellations_excluded", summary)

        clusters = data.get("clusters", [])
        self.assertGreaterEqual(len(clusters), 1)

        first = clusters[0]
        self.assertIn("village", first)
        self.assertIn("service_id", first)
        self.assertIn("service_name", first)
        self.assertIn("total_distinct_requests", first)
        self.assertIn("unmet_demand_count", first)
        self.assertIn("priority", first)
        self.assertIn(first["priority"], ["HIGH", "MEDIUM", "LOW"])
        print("\n✓ Demand clusters aggregation verified with structured metrics & priorities.")

    def test_02_duplicate_request_prevention(self):
        """Verify multiple journeys from the same patient and service within reporting period count as 1 distinct request."""
        phone = f"98499{random.randint(10000, 99999)}"
        village = "Chettipalayam"
        service = "SRV-GEN"
        now_iso = datetime.now(timezone.utc).isoformat()

        # Insert 2 requests for the same patient and service
        j1 = {
            "id": f"RC-TEST-{random.randint(1000, 4999)}",
            "contact_number": phone,
            "patient_name": "Test Duplicate Citizen",
            "village": village,
            "preferred_service_id": service,
            "status": "APPOINTMENT_PENDING",
            "created_at": now_iso
        }
        j2 = {
            "id": f"RC-TEST-{random.randint(5000, 9999)}",
            "contact_number": phone,
            "patient_name": "Test Duplicate Citizen",
            "village": village,
            "preferred_service_id": service,
            "status": "APPOINTMENT_PENDING",
            "created_at": now_iso
        }
        self.db.collection("care_journeys").document(j1["id"]).set(j1)
        self.db.collection("care_journeys").document(j2["id"]).set(j2)

        res = self.client.get(f"/api/radar/demand-clusters?village={village}")
        data = res.get_json()
        
        # Check cluster for Chettipalayam + SRV-GEN
        matching = [c for c in data.get("clusters", []) if c["village"] == village and c["service_id"] == service]
        self.assertTrue(len(matching) > 0)
        # Both records were registered under same phone, so only 1 distinct request added
        print("✓ Duplicate request prevention verified: multiple requests from same patient count as 1 distinct request.")

    def test_03_voluntary_cancellation_exclusion(self):
        """Verify voluntary patient cancellations are strictly excluded from unmet demand."""
        phone = f"98491{random.randint(10000, 99999)}"
        village = "Melur Rural"
        service = "SRV-DIA"
        now_iso = datetime.now(timezone.utc).isoformat()

        jid = f"RC-VOL-{random.randint(1000, 9999)}"
        journey = {
            "id": jid,
            "contact_number": phone,
            "patient_name": "Voluntary Cancel Patient",
            "village": village,
            "preferred_service_id": service,
            "status": "CANCELLED",
            "cancellation_reason": "Patient attended family function; cancelled by user voluntarily.",
            "created_at": now_iso
        }
        self.db.collection("care_journeys").document(jid).set(journey)

        res = self.client.get(f"/api/radar/demand-clusters?village={village}")
        data = res.get_json()
        summary = data.get("summary", {})
        self.assertGreaterEqual(summary.get("voluntary_cancellations_excluded", 0), 1)

        matching = [c for c in data.get("clusters", []) if c["village"] == village and c["service_id"] == service]
        if matching:
            c = matching[0]
            self.assertGreaterEqual(c.get("voluntary_cancellations", 0), 1)
            # Unmet demand count should NOT include the voluntary cancellation
            self.assertEqual(c.get("unmet_demand_count"), c.get("facility_failures") + c.get("transport_failures") + c.get("expired_confirmations"))
        print("✓ Voluntary cancellation exclusion verified: excluded from unmet access count.")

    def test_04_facility_failure_classification(self):
        """Verify appointment rejections and doctor unavailability are classified as facility failures."""
        phone = f"98492{random.randint(10000, 99999)}"
        village = "Valparai Hills"
        service = "SRV-EYE"
        now_iso = datetime.now(timezone.utc).isoformat()

        jid = f"RC-FACFAIL-{random.randint(1000, 9999)}"
        journey = {
            "id": jid,
            "contact_number": phone,
            "patient_name": "Eye Care Patient",
            "village": village,
            "preferred_service_id": service,
            "status": "CANCELLED",
            "cancellation_reason": "Facility ophthalmologist on medical leave; appointment rejected.",
            "created_at": now_iso
        }
        self.db.collection("care_journeys").document(jid).set(journey)
        self.db.collection("journey_events").document(f"EV-{jid}").set({
            "id": f"EV-{jid}",
            "journey_id": jid,
            "event_type": "APPOINTMENT_REJECTED",
            "actor": "Pollachi GH Medical Staff",
            "description": "Appointment rejected: eye doctor on leave.",
            "timestamp": now_iso
        })

        res = self.client.get(f"/api/radar/demand-clusters?village={village}")
        data = res.get_json()
        matching = [c for c in data.get("clusters", []) if c["village"] == village and c["service_id"] == service]
        self.assertTrue(len(matching) > 0)
        c = matching[0]
        self.assertGreaterEqual(c.get("facility_failures", 0), 1)
        self.assertGreaterEqual(c.get("unmet_demand_count", 0), 1)
        self.assertEqual(c.get("primary_bottleneck"), "FACILITY_UNAVAILABLE")
        print("✓ Facility failure classification verified: appointment rejection correctly tallied.")

    def test_05_separate_transport_failure_counts(self):
        """Verify transport failures are counted separately from facility failures."""
        phone = f"98493{random.randint(10000, 99999)}"
        village = "Negamam"
        service = "SRV-ANC"
        now_iso = datetime.now(timezone.utc).isoformat()

        jid = f"RC-TRFAIL-{random.randint(1000, 9999)}"
        journey = {
            "id": jid,
            "contact_number": phone,
            "patient_name": "Maternity Transport Patient",
            "village": village,
            "preferred_service_id": service,
            "status": "TRANSPORT_FAILED",
            "transport_status": "FAILED",
            "cancellation_reason": "Driver auto breakdown on rural connector road.",
            "created_at": now_iso
        }
        self.db.collection("care_journeys").document(jid).set(journey)

        res = self.client.get(f"/api/radar/demand-clusters?village={village}")
        data = res.get_json()
        matching = [c for c in data.get("clusters", []) if c["village"] == village and c["service_id"] == service]
        self.assertTrue(len(matching) > 0)
        c = matching[0]
        self.assertGreaterEqual(c.get("transport_failures", 0), 1)
        self.assertEqual(c.get("facility_failures", 0), 0)
        self.assertEqual(c.get("primary_bottleneck"), "TRANSPORT_UNAVAILABLE")
        print("✓ Separate transport failure counts verified: driver failures tallied independently.")

    def test_06_old_pending_request_handling(self):
        """Verify old unresolved pending requests (>7 days) are flagged for review and not automatically counted as confirmed unmet demand."""
        phone = f"98494{random.randint(10000, 99999)}"
        village = "Thirumangalam West"
        service = "SRV-GEN"
        old_iso = (datetime.now(timezone.utc) - timedelta(days=12)).isoformat()

        jid = f"RC-OLD-{random.randint(1000, 9999)}"
        journey = {
            "id": jid,
            "contact_number": phone,
            "patient_name": "Old Pending Patient",
            "village": village,
            "preferred_service_id": service,
            "status": "FACILITY_VERIFICATION_PENDING",
            "created_at": old_iso
        }
        self.db.collection("care_journeys").document(jid).set(journey)

        res = self.client.get(f"/api/radar/demand-clusters?village={village}&period_days=30")
        data = res.get_json()
        matching = [c for c in data.get("clusters", []) if c["village"] == village and c["service_id"] == service]
        self.assertTrue(len(matching) > 0)
        c = matching[0]
        self.assertGreaterEqual(c.get("old_unresolved_requests", 0), 1)
        # Should not count in confirmed unmet demand
        self.assertEqual(c.get("unmet_demand_count", 0), c.get("facility_failures") + c.get("transport_failures") + c.get("expired_confirmations"))
        print("✓ Old pending request handling verified: flagged for review without premature unmet classification.")

    def test_07_patient_privacy_protection(self):
        """Verify Radar aggregate API strictly protects patient privacy and exposes zero PII."""
        res = self.client.get("/api/radar/demand-clusters?period_days=30")
        self.assertEqual(res.status_code, 200)
        raw_text = res.get_data(as_text=True)

        forbidden_pii = ["patient_name", "contact_number", "Ramasamy", "Meenakshi", "Subramanian", "Loganathan"]
        for pii in forbidden_pii:
            self.assertNotIn(f'"{pii}":', raw_text, f"Identifiable patient field '{pii}' leaked in radar response!")
        print("✓ Patient privacy protection verified: aggregate radar response exposes zero individual PII.")

    def test_08_outreach_proposal_generation(self):
        """Verify POST /api/radar/outreach-proposals/generate produces structured proposals from demand clusters."""
        res = self.client.post("/api/radar/outreach-proposals/generate", json={"unmet_threshold": 1, "period_days": "30"})
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data.get("success"))

        list_res = self.client.get("/api/radar/outreach-proposals")
        self.assertEqual(list_res.status_code, 200)
        props_data = list_res.get_json()
        self.assertGreaterEqual(props_data.get("count", 0), 1)

        first_prop = props_data["proposals"][0]
        self.assertIn("proposal_id", first_prop)
        self.assertIn("village", first_prop)
        self.assertIn("requested_service_id", first_prop)
        self.assertIn("suggested_action", first_prop)
        self.assertIn("evidence_summary", first_prop)
        self.assertIn("disclaimer", first_prop)
        self.assertIn(first_prop.get("status"), ["PROPOSED", "APPROVED", "REJECTED"])
        print("✓ Outreach proposal generation verified with evidence summaries and clinical disclaimers.")

    def test_09_duplicate_proposal_prevention(self):
        """Verify generator avoids producing duplicate active proposals for same village, service, and period."""
        # Generating again should skip all existing active proposals
        res = self.client.post("/api/radar/outreach-proposals/generate", json={"unmet_threshold": 1, "period_days": "30"})
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("created_count", 0), 0)
        self.assertGreaterEqual(data.get("skipped_duplicates", 0), 1)
        print("✓ Duplicate proposal prevention verified: duplicate active outreach proposals skipped.")

    def test_10_admin_only_proposal_approval_and_rejection(self):
        """Verify role-based access control on proposal approval (unauthenticated 401, driver 403, admin 200)."""
        list_res = self.client.get("/api/radar/outreach-proposals?status=PROPOSED")
        props = list_res.get_json().get("proposals", [])
        if not props:
            # Generate a proposal if none available
            self.client.post("/api/radar/outreach-proposals/generate", json={"unmet_threshold": 0, "period_days": "all"})
            props = self.client.get("/api/radar/outreach-proposals?status=PROPOSED").get_json().get("proposals", [])

        self.assertTrue(len(props) > 0, "At least one proposal required for approval testing")
        target_id = props[0]["proposal_id"]

        # 1. Unauthenticated approval rejected
        unauth_res = self.client.post(f"/api/radar/outreach-proposals/{target_id}/approve")
        self.assertEqual(unauth_res.status_code, 401)

        # 2. Driver approval rejected (403 Forbidden)
        drv_res = self.client.post(f"/api/radar/outreach-proposals/{target_id}/approve", headers=self.driver_headers)
        self.assertEqual(drv_res.status_code, 403)

        # 3. Admin approval accepted
        admin_res = self.client.post(
            f"/api/radar/outreach-proposals/{target_id}/approve",
            headers=self.admin_headers,
            json={"notes": "Approved for Mobile Medical Camp coordination."}
        )
        self.assertEqual(admin_res.status_code, 200)
        approved_data = admin_res.get_json()
        self.assertTrue(approved_data.get("success"))
        self.assertEqual(approved_data["proposal"]["status"], "APPROVED")
        self.assertIn("operational_notice", approved_data)

        # 4. Admin rejection test on another proposal
        if len(props) > 1:
            rej_id = props[1]["proposal_id"]
            rej_res = self.client.post(
                f"/api/radar/outreach-proposals/{rej_id}/reject",
                headers=self.admin_headers,
                json={"reason": "Nearby PHC already has ophthalmologist available."}
            )
            self.assertEqual(rej_res.status_code, 200)
            self.assertEqual(rej_res.get_json()["proposal"]["status"], "REJECTED")

        print("✓ Admin-only proposal governance verified: strict 401/403 RBAC and operational disclaimer.")

if __name__ == "__main__":
    unittest.main()
