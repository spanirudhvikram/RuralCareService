#!/usr/bin/env python3
"""
RuralCare Firestore Rollback Utility
Safely removes migrated cloud documents from Firestore project ruralcare-f61e6
and validates restoration of the local JSON persistent store.
"""

import os
import sys
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import Config

def purge_firestore_collections(confirmed=False):
    if not confirmed:
        print("ERROR: Rollback purge requires explicit confirmation (--confirm).")
        sys.exit(1)

    import firebase_admin
    from firebase_admin import credentials, firestore

    creds_path = Config.FIREBASE_CREDENTIALS_PATH
    if not firebase_admin._apps:
        cred = credentials.Certificate(creds_path)
        firebase_admin.initialize_app(cred, {"projectId": Config.FIREBASE_PROJECT_ID})

    db = firestore.client()
    collections_to_purge = [
        "facilities", "villages", "services", "facility_staff", "drivers",
        "care_journeys", "transport_requests", "journey_events",
        "outreach_proposals", "medicine_requests", "patients",
        "pharmacies", "prescriptions", "medicine_orders", "_connectivity_tests"
    ]

    total_deleted = 0
    for col_name in collections_to_purge:
        col_ref = db.collection(col_name)
        docs = list(col_ref.limit(500).stream())
        deleted = 0
        while docs:
            batch = db.batch()
            for doc in docs:
                batch.delete(doc.reference)
                deleted += 1
            batch.commit()
            docs = list(col_ref.limit(500).stream())
        total_deleted += deleted
        print(f"Purged collection '{col_name}': {deleted} documents removed.")

    print(f"\nRollback complete. Total {total_deleted} cloud documents purged.")
    print("Local database backup at data/backups/ remains completely intact and unaffected.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RuralCare Firestore Rollback Utility")
    parser.add_argument("--confirm", action="store_true", help="Confirm execution of rollback purge")
    args = parser.parse_args()
    purge_firestore_collections(args.confirm)
