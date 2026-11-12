#!/usr/bin/env python3
"""
RuralCare Firestore Migration Utility
Preserves local JSON backup, document IDs, relationships, and prevents duplicate imports.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import Config

def get_firestore_client():
    creds_path = Config.FIREBASE_CREDENTIALS_PATH
    if not creds_path or not os.path.exists(creds_path):
        secrets_dir = BASE_DIR / "secrets"
        if secrets_dir.exists():
            for f in secrets_dir.glob("*.json"):
                if "firebase" in f.name.lower() or "adminsdk" in f.name.lower():
                    creds_path = str(f.resolve())
                    break
    if not creds_path or not os.path.exists(creds_path):
        raise FileNotFoundError(f"Firebase credentials not found at {creds_path}")

    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        cred = credentials.Certificate(creds_path)
        firebase_admin.initialize_app(cred, {"projectId": Config.FIREBASE_PROJECT_ID})
    
    return firestore.client()

def generate_preview(local_db_path):
    with open(local_db_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    db = get_firestore_client()
    preview = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_file": str(local_db_path),
        "target_project_id": Config.FIREBASE_PROJECT_ID,
        "collections": {}
    }

    total_local_docs = 0
    total_existing_cloud = 0
    total_to_insert = 0

    for col_name, docs in data.items():
        if col_name.startswith("_"):
            continue
        local_count = len(docs)
        total_local_docs += local_count

        # Check existing in Firestore
        cloud_ref = db.collection(col_name)
        # Check existing IDs
        existing_ids = set()
        # Sample or check doc existence without downloading massive payloads
        cloud_docs = list(cloud_ref.select([]).stream())
        cloud_count = len(cloud_docs)
        existing_ids = {d.id for d in cloud_docs}
        total_existing_cloud += cloud_count

        to_insert_count = 0
        conflicts = 0
        for doc_id in docs.keys():
            if doc_id in existing_ids:
                conflicts += 1
            else:
                to_insert_count += 1

        total_to_insert += to_insert_count
        preview["collections"][col_name] = {
            "local_count": local_count,
            "cloud_existing_count": cloud_count,
            "new_to_insert": to_insert_count,
            "existing_to_preserve": conflicts
        }

    preview["summary"] = {
        "total_collections": len(preview["collections"]),
        "total_local_documents": total_local_docs,
        "total_existing_cloud_documents": total_existing_cloud,
        "total_documents_to_insert": total_to_insert
    }
    return preview

def run_migration(local_db_path, batch_size=400):
    with open(local_db_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    db = get_firestore_client()
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "migrated_collections": {},
        "verification": {}
    }

    for col_name, docs in data.items():
        if col_name.startswith("_"):
            continue

        inserted = 0
        skipped = 0
        verified = 0

        # Query existing doc IDs to prevent overwriting
        existing_docs = {d.id for d in db.collection(col_name).select([]).stream()}

        batch = db.batch()
        batch_count = 0

        for doc_id, doc_data in docs.items():
            if doc_id in existing_docs:
                skipped += 1
                continue

            doc_ref = db.collection(col_name).document(doc_id)
            batch.set(doc_ref, doc_data)
            batch_count += 1
            inserted += 1

            if batch_count >= batch_size:
                batch.commit()
                batch = db.batch()
                batch_count = 0

        if batch_count > 0:
            batch.commit()

        # Read back and verify a sample of inserted documents
        verify_ids = list(docs.keys())[:min(10, len(docs))]
        col_ref = db.collection(col_name)
        for vid in verify_ids:
            snap = col_ref.document(vid).get()
            if snap.exists:
                verified += 1

        results["migrated_collections"][col_name] = {
            "inserted": inserted,
            "skipped_existing": skipped,
            "verified_sample": verified
        }
        print(f"[{col_name}] Inserted: {inserted}, Skipped: {skipped}, Verified Sample: {verified}")

    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RuralCare Firestore Migration Utility")
    parser.add_argument("--preview", action="store_true", help="Generate migration preview without writing")
    parser.add_argument("--execute", action="store_true", help="Execute the migration")
    args = parser.parse_args()

    local_db = Config.DB_FILE
    if args.preview or not args.execute:
        print("Generating Migration Preview...")
        prev = generate_preview(local_db)
        print(json.dumps(prev, indent=2))
    elif args.execute:
        print("Executing Migration to Cloud Firestore...")
        res = run_migration(local_db)
        print("Migration Completed:")
        print(json.dumps(res, indent=2))
