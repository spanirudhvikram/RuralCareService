#!/usr/bin/env python3
"""
Live Deployment End-to-End Test Suite
Tests the live deployed public HTTPS endpoint directly across all Phase 7 requirements.
"""

import sys
import random
import requests

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "https://rangers-composition-subsidiary-chubby.trycloudflare.com"
print(f"Executing Live Deployment E2E Tests on: {BASE_URL}")

s = requests.Session()

# 1. Homepage & Health
r_home = s.get(f"{BASE_URL}/")
assert r_home.status_code == 200, f"Homepage failed: {r_home.status_code}"
assert "RuralCare" in r_home.text or "கிராமப்புற" in r_home.text
print("1. Homepage Load: PASSED (HTTP 200)")

r_health = s.get(f"{BASE_URL}/api/health")
assert r_health.status_code == 200
h_data = r_health.json()
assert h_data.get("database") in ("firebase_firestore", "persistent_document_store"), f"Unexpected db: {h_data.get('database')}"
print(f"2. Health & Live Database Mode: PASSED ({h_data.get('database')})")

# 3. Patient Registration Online
rand_phone = f"98{random.randint(10000000, 99999999)}"
r_reg = s.post(f"{BASE_URL}/api/patients/register", json={
    "name": "Live Test Patient (முத்து)",
    "age": 58,
    "contact_number": rand_phone,
    "village": "Chettipalayam",
    "consent_given": True
})
assert r_reg.status_code in (200, 201), f"Patient reg failed: {r_reg.text}"
pat = r_reg.json().get("patient")
pid = pat["id"]
print(f"3. Online Patient Registration: PASSED ({pid})")

# 4. Patient Login & Retrieval
r_login = s.post(f"{BASE_URL}/api/patients/login", json={
    "patient_id": pid,
    "contact_number": rand_phone
})
assert r_login.status_code == 200
assert r_login.json().get("patient", {}).get("name") == "Live Test Patient (முத்து)"
print("4. Patient Login & Profile Retrieval: PASSED")

# 5. Patient Care Journey Creation
r_journey = s.post(f"{BASE_URL}/api/journeys", json={
    "patient_id": pid,
    "patient_name": "Live Test Patient (முத்து)",
    "age": 58,
    "contact_number": rand_phone,
    "village": "Chettipalayam",
    "preferred_service_id": "SRV-GEN",
    "preferred_date": "2026-11-28",
    "facility_id": "FAC-01",
    "transport_required": True,
    "pickup_landmark": "RuralCare Self-Service Centre (Chettipalayam)",
    "symptoms": "காய்ச்சல் மற்றும் உடல் வலி"
})
assert r_journey.status_code == 201, f"Journey create failed: {r_journey.text}"
jid = r_journey.json().get("journey", {}).get("id")
tr_id = r_journey.json().get("journey", {}).get("transport_request_id")
print(f"5. Care Journey Creation: PASSED ({jid})")

# 6. Doctor Workflow (Review & Offer Slot)
s.post(f"{BASE_URL}/api/auth/login", json={"username": "dr.revathi@pollachigh.tn.gov.in", "password": "staff"})
slot = f"10:00 AM Slot {random.randint(1000, 9999)}"
r_doc = s.post(f"{BASE_URL}/api/facilities/doctor/review", json={
    "journey_id": jid,
    "action": "accept",
    "appointment_slot": slot,
    "clinical_urgency": "ROUTINE",
    "doctor_notes": "Live test clinical checkup."
})
assert r_doc.status_code == 200, f"Doctor review failed: {r_doc.text}"
print(f"6. Doctor Workflow (Offer Slot): PASSED ({slot})")

# 7. Patient Confirm Slot
r_confirm = s.post(f"{BASE_URL}/api/journeys/{jid}/patient-confirm-slot")
assert r_confirm.status_code == 200
print("7. Patient Slot Confirmation: PASSED")

# 8. Driver Workflow (Accept & Quote Fare)
s.post(f"{BASE_URL}/api/auth/login", json={"username": "9842177101", "password": "driver"})
r_drv = s.post(f"{BASE_URL}/api/transport/requests/{tr_id}/accept", json={
    "driver_name": "அன்பு",
    "fare_amount": 150.0
})
assert r_drv.status_code == 200
r_fare = s.post(f"{BASE_URL}/api/transport/requests/{tr_id}/record-fare-acceptance", json={
    "patient_fare_accepted": True
})
assert r_fare.status_code == 200
print("8. Driver Workflow & Fare Acceptance: PASSED")

# 9. Departure Readiness Check
s.post(f"{BASE_URL}/api/journeys/{jid}/patient-readiness", json={"patient_readiness_confirmed": True})
r_chk = s.get(f"{BASE_URL}/api/readiness/check/{jid}")
assert r_chk.status_code == 200
chk_res = r_chk.json()
assert chk_res.get("readiness", {}).get("is_ready_for_departure") is True
assert chk_res.get("status") == "COORDINATION_CONFIRMED"
print(f"9. 7-Checkpoint Departure Readiness: PASSED ({chk_res.get('status')})")

# 10. Prescription Upload & Verification
s.post(f"{BASE_URL}/api/auth/login", json={"username": "dr.revathi@pollachigh.tn.gov.in", "password": "staff"})
r_rx = s.post(f"{BASE_URL}/api/prescriptions/upload", data={
    "patient_id": pid,
    "journey_id": jid,
    "doctor_name": "Dr. K. Revathi",
    "medications": "Paracetamol 500mg, Amoxicillin 250mg",
    "dosage_instructions": "1 tablet twice daily after food"
}, files={"file": ("rx_test.png", b"\x89PNG\r\n\x1a\nfakeimagebytes", "image/png")})
assert r_rx.status_code == 201, f"Prescription upload failed: {r_rx.text}"
rx_id = r_rx.json().get("prescription", {}).get("id")
print(f"10. Pharmacy & Prescription Workflow: PASSED ({rx_id})")

# 11. Offline Sync Batch Endpoint
offline_uuid = f"offline_uuid_{random.randint(100000, 999999)}"
r_sync = s.post(f"{BASE_URL}/api/sync/batch", json={
    "items": [{
        "client_uuid": offline_uuid,
        "entity_type": "PATIENT_REGISTRATION",
        "name": "Offline Queued Citizen",
        "age": 50,
        "contact_number": f"97{random.randint(10000000, 99999999)}",
        "village": "Omalur",
        "consent_given": True,
        "created_at": "2026-11-13T05:14:00Z"
    }]
})
assert r_sync.status_code == 200
sync_results = r_sync.json().get("results", [])
assert len(sync_results) == 1 and sync_results[0].get("status") in ("SYNCED", "SYNC_SUCCESS")
print(f"11. Offline Batch Sync: PASSED ({sync_results[0].get('status')})")

# 12. Duplicate Sync Prevention
r_sync_dup = s.post(f"{BASE_URL}/api/sync/batch", json={
    "items": [{
        "client_uuid": offline_uuid,
        "entity_type": "PATIENT_REGISTRATION",
        "name": "Offline Queued Citizen",
        "age": 50,
        "contact_number": "9700000000",
        "village": "Omalur",
        "consent_given": True,
        "created_at": "2026-11-13T05:14:00Z"
    }]
})
assert r_sync_dup.status_code == 200
dup_res = r_sync_dup.json().get("results", [])[0]
assert dup_res.get("status") in ("DUPLICATE_ACKNOWLEDGED", "DUPLICATE_SKIPPED")
print(f"12. Duplicate Replay Prevention: PASSED ({dup_res.get('status')})")

print("\nALL 12 LIVE DEPLOYMENT E2E TESTS PASSED OVER PUBLIC CLOUD INTERNET!")
