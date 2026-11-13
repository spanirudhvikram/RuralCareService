import os
import json
import uuid
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from config import Config

# Ensure data directory exists
Config.DATA_DIR.mkdir(parents=True, exist_ok=True)

class DocumentSnapshot:
    def __init__(self, doc_id, data, exists=True):
        self.id = doc_id
        self._data = data or {}
        self.exists = exists

    def to_dict(self):
        return dict(self._data) if self.exists else None

    def get(self, field, default=None):
        return self._data.get(field, default) if self.exists else default

class LocalDocumentRef:
    def __init__(self, store, collection_name, doc_id):
        self.store = store
        self.collection_name = collection_name
        self.id = doc_id

    def get(self):
        return self.store.get_document(self.collection_name, self.id)

    def set(self, data, merge=False):
        return self.store.set_document(self.collection_name, self.id, data, merge=merge)

    def update(self, updates):
        return self.store.update_document(self.collection_name, self.id, updates)

    def delete(self):
        return self.store.delete_document(self.collection_name, self.id)

class LocalCollectionRef:
    def __init__(self, store, collection_name):
        self.store = store
        self.name = collection_name

    def document(self, doc_id=None):
        if not doc_id:
            doc_id = str(uuid.uuid4())
        return LocalDocumentRef(self.store, self.name, doc_id)

    def add(self, data):
        doc_id = str(uuid.uuid4())
        ref = self.document(doc_id)
        ref.set(data)
        return None, ref

    def stream(self):
        return self.store.list_documents(self.name)

    def where(self, field, op, value):
        docs = self.store.list_documents(self.name)
        filtered = []
        for doc in docs:
            d = doc.to_dict()
            if d is None:
                continue
            val = d.get(field)
            if op == "==" and val == value:
                filtered.append(doc)
            elif op == "!=" and val != value:
                filtered.append(doc)
            elif op == "in" and isinstance(value, (list, tuple)) and val in value:
                filtered.append(doc)
            elif op == ">" and val is not None and val > value:
                filtered.append(doc)
            elif op == "<" and val is not None and val < value:
                filtered.append(doc)
        return filtered

class LocalPersistentStore:
    """Thread-safe persistent JSON document store mirroring Firestore behavior."""
    def __init__(self, file_path):
        self.file_path = Path(file_path)
        self.lock = threading.RLock()
        self.data = {}
        self._last_mtime = 0
        self._load()

    def _check_reload(self):
        """Reload data if file has been modified on disk by another process."""
        try:
            if self.file_path.exists():
                mtime = self.file_path.stat().st_mtime
                if mtime > self._last_mtime:
                    with open(self.file_path, "r", encoding="utf-8") as f:
                        disk_data = json.load(f)
                    if isinstance(disk_data, dict):
                        self.data = disk_data
                    self._last_mtime = mtime
        except Exception:
            pass

    def _load(self):
        with self.lock:
            if self.file_path.exists():
                try:
                    with open(self.file_path, "r", encoding="utf-8") as f:
                        self.data = json.load(f)
                    self._last_mtime = self.file_path.stat().st_mtime
                except Exception:
                    self.data = {}
            else:
                self.data = {}
                self._save()

    def _save(self):
        with self.lock:
            temp_path = self.file_path.with_name(f"{self.file_path.stem}_{os.getpid()}_{uuid.uuid4().hex[:8]}.tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            try:
                temp_path.replace(self.file_path)
                if self.file_path.exists():
                    self._last_mtime = self.file_path.stat().st_mtime
            except Exception:
                # Fallback if replace fails
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass

    def collection(self, name):
        with self.lock:
            self._check_reload()
            if name not in self.data:
                self.data[name] = {}
        return LocalCollectionRef(self, name)

    def get_document(self, collection_name, doc_id):
        with self.lock:
            self._check_reload()
            coll = self.data.get(collection_name, {})
            if doc_id in coll:
                return DocumentSnapshot(doc_id, coll[doc_id], exists=True)
            return DocumentSnapshot(doc_id, None, exists=False)

    def set_document(self, collection_name, doc_id, data, merge=False):
        with self.lock:
            self._check_reload()
            if collection_name not in self.data:
                self.data[collection_name] = {}
            if merge and doc_id in self.data[collection_name]:
                self.data[collection_name][doc_id].update(data)
            else:
                self.data[collection_name][doc_id] = dict(data)
            self._save()
            return DocumentSnapshot(doc_id, self.data[collection_name][doc_id], exists=True)

    def update_document(self, collection_name, doc_id, updates):
        with self.lock:
            self._check_reload()
            coll = self.data.get(collection_name, {})
            if doc_id not in coll:
                raise KeyError(f"Document {doc_id} does not exist in {collection_name}")
            coll[doc_id].update(updates)
            self._save()
            return DocumentSnapshot(doc_id, coll[doc_id], exists=True)

    def delete_document(self, collection_name, doc_id):
        with self.lock:
            self._check_reload()
            coll = self.data.get(collection_name, {})
            if doc_id in coll:
                del coll[doc_id]
                self._save()
                return True
            return False

    def list_documents(self, collection_name):
        with self.lock:
            self._check_reload()
            coll = self.data.get(collection_name, {})
            return [DocumentSnapshot(k, v, exists=True) for k, v in coll.items()]


class FirestoreQueryWrapper:
    """Wraps google-cloud-firestore Query to seamlessly support len(), [idx], and streaming."""
    def __init__(self, raw_query):
        self._raw_query = raw_query
        self._cached = None

    def _get_list(self):
        if self._cached is None:
            self._cached = list(self._raw_query.stream())
        return self._cached

    def __iter__(self):
        return iter(self._get_list())

    def __len__(self):
        return len(self._get_list())

    def __getitem__(self, idx):
        return self._get_list()[idx]

    def __bool__(self):
        return len(self._get_list()) > 0

    def stream(self):
        return iter(self._get_list())

    def get(self):
        return self._get_list()

    def where(self, field=None, op=None, value=None, filter=None):
        from google.cloud.firestore_v1.base_query import FieldFilter
        if filter is not None:
            return FirestoreQueryWrapper(self._raw_query.where(filter=filter))
        return FirestoreQueryWrapper(self._raw_query.where(filter=FieldFilter(field, op, value)))

    def order_by(self, *args, **kwargs):
        return FirestoreQueryWrapper(self._raw_query.order_by(*args, **kwargs))

    def limit(self, count):
        return FirestoreQueryWrapper(self._raw_query.limit(count))

    def select(self, *args, **kwargs):
        return FirestoreQueryWrapper(self._raw_query.select(*args, **kwargs))


class FirestoreCollectionWrapper:
    """Wraps CollectionReference to return FirestoreQueryWrapper for queries."""
    def __init__(self, raw_coll):
        self._raw_coll = raw_coll

    @property
    def id(self):
        return self._raw_coll.id

    def document(self, doc_id=None):
        return self._raw_coll.document(doc_id)

    def stream(self):
        return self._raw_coll.stream()

    def get(self):
        return list(self._raw_coll.stream())

    def where(self, field=None, op=None, value=None, filter=None):
        from google.cloud.firestore_v1.base_query import FieldFilter
        if filter is not None:
            return FirestoreQueryWrapper(self._raw_coll.where(filter=filter))
        return FirestoreQueryWrapper(self._raw_coll.where(filter=FieldFilter(field, op, value)))

    def order_by(self, *args, **kwargs):
        return FirestoreQueryWrapper(self._raw_coll.order_by(*args, **kwargs))

    def limit(self, count):
        return FirestoreQueryWrapper(self._raw_coll.limit(count))

    def select(self, *args, **kwargs):
        return FirestoreQueryWrapper(self._raw_coll.select(*args, **kwargs))

    def add(self, data):
        return self._raw_coll.add(data)


class FirestoreClientWrapper:
    """Wraps Firestore client to return wrapped collections and queries."""
    def __init__(self, raw_client):
        self._raw_client = raw_client

    def collection(self, name):
        return FirestoreCollectionWrapper(self._raw_client.collection(name))

    def collections(self):
        return self._raw_client.collections()

    def batch(self):
        return self._raw_client.batch()

    def transaction(self, **kwargs):
        return self._raw_client.transaction(**kwargs)


# Global database instance initialized below
_db_instance = None
_using_firebase = False

def init_db(force_reconnect=False):
    global _db_instance, _using_firebase
    if _db_instance is not None and not force_reconnect:
        return _db_instance

    creds_path = Config.FIREBASE_CREDENTIALS_PATH
    creds_json = os.environ.get("FIREBASE_CREDENTIALS_JSON") or os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")

    if (creds_path and os.path.exists(creds_path)) or creds_json:
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore
            if not firebase_admin._apps:
                if creds_json:
                    try:
                        cred_dict = json.loads(creds_json)
                        cred = credentials.Certificate(cred_dict)
                    except Exception as je:
                        raise ValueError(f"Invalid FIREBASE_CREDENTIALS_JSON format: {je}")
                else:
                    cred = credentials.Certificate(creds_path)
                firebase_admin.initialize_app(cred, {"projectId": Config.FIREBASE_PROJECT_ID})
            raw_client = firestore.client()

            # Verify actual live server-side connection before reporting Firestore active
            test_id = f"conn_check_{uuid.uuid4().hex[:8]}"
            test_ref = raw_client.collection("_connectivity_tests").document(test_id)
            test_ref.set({"status": "CONNECTED", "timestamp": datetime.now(timezone.utc).isoformat()})
            snap = test_ref.get()
            if not snap.exists:
                raise RuntimeError("Live Firestore write succeeded but read check failed.")
            test_ref.delete()

            _db_instance = FirestoreClientWrapper(raw_client)
            _using_firebase = True
            print(f"Connected to live Firebase Firestore (project: {Config.FIREBASE_PROJECT_ID}).")
            return _db_instance
        except Exception as e:
            print(f"Firebase live connection failed, falling back to local persistent store: {e}")

    # Fallback to persistent local store with exact same API
    _db_instance = LocalPersistentStore(Config.DB_FILE)
    _using_firebase = False
    print(f"Using persistent local store at {Config.DB_FILE}")
    seed_initial_data(_db_instance)
    seed_radar_demo_data(_db_instance)
    seed_recommendation_demo_data(_db_instance)
    seed_workflow_demo_data(_db_instance)
    return _db_instance

def get_db():
    return init_db()

def is_using_firebase():
    return _using_firebase

def get_database_status():
    """
    Returns verified diagnostic information regarding the active database adapter.
    Explicitly distinguishes between live Firebase Firestore and local persistent store.
    """
    is_fb = is_using_firebase()
    creds_path = Config.FIREBASE_CREDENTIALS_PATH
    has_creds = bool((creds_path and os.path.exists(creds_path)) or os.environ.get("FIREBASE_CREDENTIALS_JSON"))
    creds_source = "environment_json" if os.environ.get("FIREBASE_CREDENTIALS_JSON") else (creds_path if (creds_path and os.path.exists(creds_path)) else None)
    
    status = {
        "database_mode": "firebase_firestore" if is_fb else "persistent_document_store",
        "cloud_firestore_active": is_fb,
        "credentials_configured": has_creds,
        "credentials_path": creds_path if has_creds else None,
        "project_id": Config.FIREBASE_PROJECT_ID if is_fb else None,
        "storage_path": str(Config.DB_FILE),
        "status": "ONLINE_CLOUD" if is_fb else "ONLINE_LOCAL_STORE",
        "description": "Operating on live Firebase Firestore in the cloud." if is_fb else "Operating on thread-safe persistent document store mirroring Firestore APIs."
    }
    return status

def verify_firestore_connection():
    """
    Attempts actual write, read, update, query, and delete operations on the database.
    If using Firebase, tests against cloud Firestore.
    If using local store, validates local document store read/write fidelity.
    """
    db = get_db()
    test_id = f"test_{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    test_payload = {
        "test_id": test_id,
        "purpose": "RuralCare Firestore Connectivity & Integrity Verification",
        "created_at": now_iso,
        "status": "HEALTHCHECK_OK"
    }

    try:
        # 1. Write / Set
        coll = db.collection("_connectivity_tests")
        doc_ref = coll.document(test_id)
        doc_ref.set(test_payload)

        # 2. Read / Get
        snapshot = doc_ref.get()
        if not snapshot.exists:
            return {"success": False, "error": "Document write succeeded but read snapshot reported not exists."}
        data = snapshot.to_dict()
        if data.get("test_id") != test_id:
            return {"success": False, "error": "Data mismatch on verification read."}

        # 3. Update
        doc_ref.update({"status": "UPDATED_OK", "updated_at": now_iso})
        updated_snap = doc_ref.get()
        if updated_snap.to_dict().get("status") != "UPDATED_OK":
            return {"success": False, "error": "Update failed during verification."}

        # 4. Query
        matches = coll.where("test_id", "==", test_id)
        if len(matches) == 0:
            return {"success": False, "error": "Query verification returned 0 matches."}

        # 5. Delete
        doc_ref.delete()
        del_snap = doc_ref.get()
        if del_snap.exists:
            return {"success": False, "error": "Document deletion verification failed."}

        return {
            "success": True,
            "database_mode": "firebase_firestore" if is_using_firebase() else "persistent_document_store",
            "cloud_firestore_active": is_using_firebase(),
            "operations_verified": ["write", "read", "update", "query", "delete"],
            "timestamp": now_iso
        }
    except Exception as e:
        return {
            "success": False,
            "database_mode": "firebase_firestore" if is_using_firebase() else "persistent_document_store",
            "cloud_firestore_active": is_using_firebase(),
            "error": str(e)
        }

def seed_initial_data(db):
    """Seed synthetic demo records for villages, healthcare facilities, services, staff, and drivers."""
    facilities_ref = db.collection("facilities")
    existing_facilities = list(facilities_ref.stream())
    if len(existing_facilities) > 0:
        return  # Already seeded

    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Villages
    villages = [
        {"id": "VIL-01", "name": "Chettipalayam", "name_ta": "செட்டிபாளையம்", "district": "Coimbatore", "sub_district": "Madukkarai", "pin": "641201"},
        {"id": "VIL-02", "name": "Omalur", "name_ta": "ஓமலூர்", "district": "Salem", "sub_district": "Omalur", "pin": "636455"},
        {"id": "VIL-03", "name": "Valparai Hills", "name_ta": "வால்பாறை", "district": "Coimbatore", "sub_district": "Valparai", "pin": "642127"},
        {"id": "VIL-04", "name": "Melur Rural", "name_ta": "மேலூர்", "district": "Madurai", "sub_district": "Melur", "pin": "625106"},
        {"id": "VIL-05", "name": "Negamam", "name_ta": "நெகமம்", "district": "Tiruppur", "sub_district": "Pollachi North", "pin": "642120"},
        {"id": "VIL-06", "name": "Thirumangalam West", "name_ta": "திருமங்கலம்", "district": "Madurai", "sub_district": "Thirumangalam", "pin": "625706"},
    ]
    for v in villages:
        db.collection("villages").document(v["id"]).set(v)

    # 2. Services
    services = [
        {"id": "SRV-GEN", "name": "General Medicine", "name_ta": "பொது மருத்துவம்", "description": "Consultation for fevers, respiratory issues, diabetes, hypertension", "category": "primary"},
        {"id": "SRV-EYE", "name": "Ophthalmology / Eye Care", "name_ta": "கண் சிகிச்சை", "description": "Cataract screening, vision assessment, glaucoma follow-up", "category": "specialist"},
        {"id": "SRV-DIA", "name": "Dialysis & Renal Care", "name_ta": "டையாலிசிஸ் / சிறுநீரக சிகிச்சை", "description": "Scheduled maintenance hemodialysis and nephrology follow-up", "category": "critical_scheduled"},
        {"id": "SRV-ANC", "name": "Antenatal & Maternity Care", "name_ta": "தாய் சேய் நலம் / மகப்பேறு", "description": "High-risk pregnancy ultrasound, trimester checks, immunisation", "category": "maternity"},
        {"id": "SRV-ORT", "name": "Orthopaedics & Joint Care", "name_ta": "எலும்பு மூட்டு சிகிச்சை", "description": "Fracture assessment, arthritis management, physical therapy guidance", "category": "specialist"},
        {"id": "SRV-PED", "name": "Pediatrics & Child Health", "name_ta": "குழந்தை மருத்துவம்", "description": "Childhood fever, nutritional assessment, growth checks", "category": "primary"},
    ]
    for s in services:
        db.collection("services").document(s["id"]).set(s)

    # 3. Facilities (clearly labelled DEMO records with VERIFIED / UNVERIFIED / EXPIRED statuses)
    facilities = [
        {
            "id": "FAC-01",
            "name": "Pollachi Government Taluk Hospital",
            "name_ta": "பொள்ளாச்சி அரசு தாலுகா தலைமை மருத்துவமனை",
            "facility_type": "Government Taluk Hospital",
            "location": "Pollachi Main Road, Coimbatore District",
            "district": "Coimbatore",
            "contact_phone": "+91 4259 223108",
            "verification_status": "VERIFIED",
            "last_verified_at": now_iso,
            "verification_valid_until": "2026-11-14T18:00:00Z",
            "verified_by_staff": "Dr. K. Revathi (Chief Medical Officer)",
            "available_services": ["SRV-GEN", "SRV-DIA", "SRV-EYE", "SRV-ANC"],
            "operating_hours": "08:00 AM - 04:00 PM (OPD)",
            "total_capacity_today": 35,
            "allocated_appointments_today": 12,
            "is_demo_record": True
        },
        {
            "id": "FAC-02",
            "name": "Omalur Community Health Centre (CHC)",
            "name_ta": "ஓமலூர் சமுதாய சுகாதார நிலையம்",
            "facility_type": "Community Health Centre",
            "location": "Bazaar Street, Omalur, Salem District",
            "district": "Salem",
            "contact_phone": "+91 4290 220108",
            "verification_status": "VERIFIED",
            "last_verified_at": now_iso,
            "verification_valid_until": "2026-11-14T18:00:00Z",
            "verified_by_staff": "Dr. S. Karthikeyan (Medical Officer)",
            "available_services": ["SRV-GEN", "SRV-ANC", "SRV-PED"],
            "operating_hours": "08:30 AM - 03:00 PM (OPD)",
            "total_capacity_today": 25,
            "allocated_appointments_today": 7,
            "is_demo_record": True
        },
        {
            "id": "FAC-03",
            "name": "Madurai District Referral Medical College Hospital",
            "name_ta": "மதுரை அரசு இராசாசி மருத்துவக் கல்லூரி மருத்துவமனை",
            "facility_type": "District Referral Tertiary Hospital",
            "location": "Panagal Road, Alwarpuram, Madurai",
            "district": "Madurai",
            "contact_phone": "+91 452 253 2535",
            "verification_status": "VERIFIED",
            "last_verified_at": now_iso,
            "verification_valid_until": "2026-11-14T20:00:00Z",
            "verified_by_staff": "Dr. V. Sundaram (Director of Coordination)",
            "available_services": ["SRV-GEN", "SRV-DIA", "SRV-EYE", "SRV-ANC", "SRV-ORT", "SRV-PED"],
            "operating_hours": "07:30 AM - 05:00 PM",
            "total_capacity_today": 60,
            "allocated_appointments_today": 24,
            "is_demo_record": True
        },
        {
            "id": "FAC-04",
            "name": "Valparai Tribal Upgraded Primary Health Centre",
            "name_ta": "வால்பாறை மேம்படுத்தப்பட்ட ஆரம்ப சுகாதார நிலையம்",
            "facility_type": "Upgraded Primary Health Centre",
            "location": "High Forest Road, Valparai",
            "district": "Coimbatore",
            "contact_phone": "+91 4253 222108",
            "verification_status": "EXPIRED",
            "last_verified_at": "2026-11-05T09:30:00Z",
            "verification_valid_until": "2026-11-07T09:30:00Z",
            "verified_by_staff": "Sister Mary (Staff Nurse In-Charge)",
            "available_services": ["SRV-GEN", "SRV-ANC"],
            "operating_hours": "09:00 AM - 02:00 PM",
            "total_capacity_today": 15,
            "allocated_appointments_today": 2,
            "verification_notes": "Verification expired. Requires fresh morning headcount from facility staff.",
            "is_demo_record": True
        },
        {
            "id": "FAC-05",
            "name": "Chettipalayam Rural Wellness Centre",
            "name_ta": "செட்டிபாளையம் நலவாழ்வு மையம்",
            "facility_type": "Health & Wellness Sub-Centre",
            "location": "Panchayat Office Road, Chettipalayam",
            "district": "Coimbatore",
            "contact_phone": "+91 422 265 1108",
            "verification_status": "UNVERIFIED",
            "last_verified_at": None,
            "verification_valid_until": None,
            "verified_by_staff": None,
            "available_services": ["SRV-GEN"],
            "operating_hours": "09:00 AM - 01:00 PM",
            "total_capacity_today": 10,
            "allocated_appointments_today": 0,
            "verification_notes": "Awaiting local staff registration and facility inspection.",
            "is_demo_record": True
        }
    ]
    for fac in facilities:
        facilities_ref.document(fac["id"]).set(fac)

    # 4. Facility Staff
    staff_members = [
        {
            "id": "STAFF-01",
            "name": "Dr. K. Revathi",
            "role": "facility_staff",
            "facility_id": "FAC-01",
            "facility_name": "Pollachi Government Taluk Hospital",
            "email": "dr.revathi@pollachigh.tn.gov.in",
            "phone": "+91 94431 22001",
            "is_demo_record": True
        },
        {
            "id": "STAFF-02",
            "name": "Dr. S. Karthikeyan",
            "role": "facility_staff",
            "facility_id": "FAC-02",
            "facility_name": "Omalur Community Health Centre",
            "email": "dr.karthik@omalurchc.tn.gov.in",
            "phone": "+91 94432 33002",
            "is_demo_record": True
        },
        {
            "id": "STAFF-03",
            "name": "Dr. V. Sundaram",
            "role": "facility_staff",
            "facility_id": "FAC-03",
            "facility_name": "Madurai District Referral Medical College Hospital",
            "email": "dr.sundaram@maduraigh.tn.gov.in",
            "phone": "+91 94433 44003",
            "is_demo_record": True
        }
    ]
    for s in staff_members:
        db.collection("facility_staff").document(s["id"]).set(s)

    # 5. Drivers
    drivers = [
        {
            "id": "DRV-01",
            "name": "Murugan S.",
            "name_ta": "முருகன் எஸ்.",
            "role": "driver",
            "phone": "+91 98421 77101",
            "vehicle_type": "Auto-Rickshaw (3-Seater)",
            "vehicle_reg": "TN-38-AX-4412",
            "primary_village": "Chettipalayam",
            "coverage_villages": ["Chettipalayam", "Negamam", "Valparai Hills"],
            "is_active": True,
            "verification_status": "VERIFIED",
            "standard_per_km_rate": 14,
            "is_demo_record": True
        },
        {
            "id": "DRV-02",
            "name": "Chinnasamy P.",
            "name_ta": "சின்னசாமி பி.",
            "role": "driver",
            "phone": "+91 98422 88202",
            "vehicle_type": "Rural Transit Bolero (6-Seater)",
            "vehicle_reg": "TN-30-CZ-9011",
            "primary_village": "Omalur",
            "coverage_villages": ["Omalur", "Salem Rural", "Mecheri"],
            "is_active": True,
            "verification_status": "VERIFIED",
            "standard_per_km_rate": 18,
            "is_demo_record": True
        },
        {
            "id": "DRV-03",
            "name": "Alagappan M.",
            "name_ta": "அழகப்பன் எம்.",
            "role": "driver",
            "phone": "+91 98423 99303",
            "vehicle_type": "Maruti Omni Rural Ambulance/Cab",
            "vehicle_reg": "TN-59-BR-7819",
            "primary_village": "Melur Rural",
            "coverage_villages": ["Melur Rural", "Thirumangalam West"],
            "is_active": True,
            "verification_status": "VERIFIED",
            "standard_per_km_rate": 16,
            "is_demo_record": True
        }
    ]
    for d in drivers:
        db.collection("drivers").document(d["id"]).set(d)

    # 6. Pre-seeded demo Care Journeys for immediate testing
    demo_journeys = [
        {
            "id": "RC-2026-1001",
            "patient_name": "Ramasamy Gounder",
            "age": 68,
            "contact_number": "9842100001",
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-DIA",
            "preferred_service_name": "Dialysis & Renal Care",
            "preferred_date": "2026-11-13",
            "facility_id": "FAC-01",
            "facility_name": "Pollachi Government Taluk Hospital",
            "registration_method": "ASSISTED_SERVICE",
            "operator_id": "KIOSK-OPERATOR-CP01",
            "operator_name": "Selvam (Panchayat Kiosk Lead)",
            "transport_required": True,
            "status": "COORDINATION_CONFIRMED",
            "created_at": "2026-11-12T08:30:00Z",
            "updated_at": "2026-11-12T10:15:00Z",
            "service_verified": True,
            "service_verification_timestamp": now_iso,
            "appointment_confirmed": True,
            "appointment_slot": "10:30 AM, Dialysis Ward 2",
            "transport_request_id": "TR-1001",
            "transport_status": "ACCEPTED",
            "driver_id": "DRV-01",
            "driver_name": "Murugan S.",
            "driver_phone": "+91 98421 77101",
            "vehicle_reg": "TN-38-AX-4412",
            "pickup_landmark": "Chettipalayam Bus Stop Shelter",
            "pickup_confirmed": True,
            "destination_confirmed": True,
            "disclosed_fare": 180,
            "fare_currency": "INR",
            "patient_fare_accepted": True,
            "patient_readiness_confirmed": True,
            "departure_readiness_check": {
                "is_ready_for_departure": True,
                "evaluated_at": now_iso,
                "checks": {
                    "service_verified": True,
                    "verification_unexpired": True,
                    "appointment_accepted": True,
                    "transport_accepted": True,
                    "pickup_destination_confirmed": True,
                    "fare_disclosed_and_accepted": True,
                    "patient_readiness_confirmed": True
                },
                "explanation": "All 7 verification checkpoints validated by RuralCare Engine. Journey confirmed for departure."
            },
            "is_demo_record": True
        },
        {
            "id": "RC-2026-1002",
            "patient_name": "Kannagi M.",
            "age": 29,
            "contact_number": "9842200002",
            "village": "Omalur",
            "preferred_service_id": "SRV-ANC",
            "preferred_service_name": "Antenatal & Maternity Care",
            "preferred_date": "2026-11-13",
            "facility_id": "FAC-02",
            "facility_name": "Omalur Community Health Centre (CHC)",
            "registration_method": "SELF_SERVICE",
            "registration_channel": "VOICE_ASSISTED",
            "operator_id": None,
            "transport_required": False,
            "status": "APPOINTMENT_PENDING",
            "created_at": "2026-11-12T09:15:00Z",
            "updated_at": "2026-11-12T09:15:00Z",
            "service_verified": True,
            "service_verification_timestamp": now_iso,
            "appointment_confirmed": False,
            "appointment_slot": None,
            "transport_request_id": None,
            "departure_readiness_check": {
                "is_ready_for_departure": False,
                "evaluated_at": now_iso,
                "checks": {
                    "service_verified": True,
                    "verification_unexpired": True,
                    "appointment_accepted": False,
                    "transport_accepted": True,  # Not required
                    "pickup_destination_confirmed": True,
                    "fare_disclosed_and_accepted": True,
                    "patient_readiness_confirmed": True
                },
                "explanation": "Awaiting appointment approval from Omalur CHC staff."
            },
            "is_demo_record": True
        }
    ]
    for j in demo_journeys:
        db.collection("care_journeys").document(j["id"]).set(j)

    # 7. Pre-seed demo transport request
    demo_transport = {
        "id": "TR-1001",
        "journey_id": "RC-2026-1001",
        "patient_name": "Ramasamy Gounder",
        "patient_phone": "9842100001",
        "pickup_village": "Chettipalayam",
        "pickup_landmark": "Chettipalayam Bus Stop Shelter",
        "destination_facility_id": "FAC-01",
        "destination_facility_name": "Pollachi Government Taluk Hospital",
        "scheduled_date": "2026-11-13",
        "driver_id": "DRV-01",
        "driver_name": "Murugan S.",
        "driver_phone": "+91 98421 77101",
        "vehicle_reg": "TN-38-AX-4412",
        "status": "ACCEPTED",
        "pickup_confirmed": True,
        "destination_confirmed": True,
        "fare_amount": 180,
        "patient_fare_accepted": True,
        "created_at": "2026-11-12T08:35:00Z",
        "updated_at": "2026-11-12T09:00:00Z",
        "is_demo_record": True
    }
    db.collection("transport_requests").document(demo_transport["id"]).set(demo_transport)

    # 8. Journey events
    events = [
        {
            "id": "EV-01",
            "journey_id": "RC-2026-1001",
            "event_type": "JOURNEY_CREATED",
            "actor": "Selvam (Operator KIOSK-CP01)",
            "description": "Patient registered at Chettipalayam Panchayat Kiosk (Assisted Service)",
            "timestamp": "2026-11-12T08:30:00Z"
        },
        {
            "id": "EV-02",
            "journey_id": "RC-2026-1001",
            "event_type": "SERVICE_VERIFIED",
            "actor": "Dr. K. Revathi (Pollachi GH)",
            "description": "Dialysis Ward availability verified for 48 hours",
            "timestamp": "2026-11-12T08:45:00Z"
        },
        {
            "id": "EV-03",
            "journey_id": "RC-2026-1001",
            "event_type": "APPOINTMENT_ACCEPTED",
            "actor": "Pollachi GH Outpatient Desk",
            "description": "Dialysis Slot confirmed: 10:30 AM Ward 2",
            "timestamp": "2026-11-12T08:50:00Z"
        },
        {
            "id": "EV-04",
            "journey_id": "RC-2026-1001",
            "event_type": "DRIVER_ACCEPTED",
            "actor": "Murugan S. (Driver DRV-01)",
            "description": "Transport accepted. Fare quoted: ₹180 from Chettipalayam Bus Stop",
            "timestamp": "2026-11-12T09:00:00Z"
        },
        {
            "id": "EV-05",
            "journey_id": "RC-2026-1001",
            "event_type": "DEPARTURE_READINESS_CONFIRMED",
            "actor": "RuralCare Coordination Engine",
            "description": "Departure Readiness Check PASSED. Status elevated to COORDINATION_CONFIRMED.",
            "timestamp": "2026-11-12T10:15:00Z"
        }
    ]
    for ev in events:
        db.collection("journey_events").document(ev["id"]).set(ev)

    print("Synthetic demo data successfully seeded.")

def seed_radar_demo_data(db):
    """Seed synthetic demo care journeys and failure events to populate Demand Radar."""
    journeys_ref = db.collection("care_journeys")
    if journeys_ref.document("RC-2026-2001").get().exists:
        return

    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()
    old_iso = (now_utc - timedelta(days=10)).isoformat()
    recent_iso = (now_utc - timedelta(days=3)).isoformat()

    radar_journeys = [
        # Melur Rural: Antenatal Care (SRV-ANC) -> 2 Unmet (1 facility failure, 1 transport failure) + 1 Confirmed
        {
            "id": "RC-2026-2001",
            "patient_name": "Meenakshi S.",
            "age": 27,
            "contact_number": "9842102001",
            "village": "Melur Rural",
            "preferred_service_id": "SRV-ANC",
            "preferred_service_name": "Antenatal & Maternity Care",
            "preferred_date": "2026-11-10",
            "facility_id": "FAC-03",
            "facility_name": "Madurai District Referral Medical College Hospital",
            "registration_method": "SELF_SERVICE",
            "transport_required": True,
            "status": "CANCELLED",
            "cancellation_reason": "Facility doctor unavailable: Obstetric surgeon called for emergency duty.",
            "created_at": recent_iso,
            "updated_at": recent_iso,
            "is_demo_record": True
        },
        {
            "id": "RC-2026-2002",
            "patient_name": "Kavitha R.",
            "age": 24,
            "contact_number": "9842102002",
            "village": "Melur Rural",
            "preferred_service_id": "SRV-ANC",
            "preferred_service_name": "Antenatal & Maternity Care",
            "preferred_date": "2026-11-11",
            "facility_id": "FAC-03",
            "facility_name": "Madurai District Referral Medical College Hospital",
            "registration_method": "ASSISTED_SERVICE",
            "transport_required": True,
            "transport_status": "FAILED",
            "status": "TRANSPORT_FAILED",
            "cancellation_reason": "No rural auto available in Melur North cluster; driver mechanical breakdown.",
            "created_at": recent_iso,
            "updated_at": recent_iso,
            "is_demo_record": True
        },
        {
            "id": "RC-2026-2003",
            "patient_name": "Revathi P.",
            "age": 29,
            "contact_number": "9842102003",
            "village": "Melur Rural",
            "preferred_service_id": "SRV-ANC",
            "preferred_service_name": "Antenatal & Maternity Care",
            "preferred_date": "2026-11-13",
            "facility_id": "FAC-03",
            "facility_name": "Madurai District Referral Medical College Hospital",
            "registration_method": "SELF_SERVICE",
            "transport_required": False,
            "status": "COORDINATION_CONFIRMED",
            "created_at": now_iso,
            "updated_at": now_iso,
            "is_demo_record": True
        },
        # Chettipalayam: Eye Care (SRV-EYE) -> 2 Unmet (facility appointment rejections) + 1 Completed
        {
            "id": "RC-2026-2004",
            "patient_name": "Subramanian K.",
            "age": 72,
            "contact_number": "9842102004",
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-EYE",
            "preferred_service_name": "Ophthalmology / Eye Care",
            "preferred_date": "2026-11-09",
            "facility_id": "FAC-01",
            "facility_name": "Pollachi Government Taluk Hospital",
            "registration_method": "ASSISTED_SERVICE",
            "transport_required": True,
            "status": "CANCELLED",
            "cancellation_reason": "Eye clinic slit-lamp diagnostic equipment out of service; appointment rejected.",
            "created_at": recent_iso,
            "updated_at": recent_iso,
            "is_demo_record": True
        },
        {
            "id": "RC-2026-2005",
            "patient_name": "Marimuthu A.",
            "age": 65,
            "contact_number": "9842102005",
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-EYE",
            "preferred_service_name": "Ophthalmology / Eye Care",
            "preferred_date": "2026-11-10",
            "facility_id": "FAC-01",
            "facility_name": "Pollachi Government Taluk Hospital",
            "registration_method": "SELF_SERVICE",
            "transport_required": True,
            "status": "CANCELLED",
            "cancellation_reason": "Visiting ophthalmologist on clinical leave; appointment rejected by staff.",
            "created_at": recent_iso,
            "updated_at": recent_iso,
            "is_demo_record": True
        },
        {
            "id": "RC-2026-2006",
            "patient_name": "Palanisamy V.",
            "age": 61,
            "contact_number": "9842102006",
            "village": "Chettipalayam",
            "preferred_service_id": "SRV-EYE",
            "preferred_service_name": "Ophthalmology / Eye Care",
            "preferred_date": "2026-11-08",
            "facility_id": "FAC-01",
            "facility_name": "Pollachi Government Taluk Hospital",
            "registration_method": "SELF_SERVICE",
            "transport_required": False,
            "status": "COMPLETED",
            "created_at": recent_iso,
            "updated_at": recent_iso,
            "is_demo_record": True
        },
        # Valparai Hills: Orthopaedics (SRV-ORT) & General -> 1 Transport Failure + 1 Old Unresolved
        {
            "id": "RC-2026-2007",
            "patient_name": "Muthusamy C.",
            "age": 58,
            "contact_number": "9842102007",
            "village": "Valparai Hills",
            "preferred_service_id": "SRV-ORT",
            "preferred_service_name": "Orthopaedics & Joint Care",
            "preferred_date": "2026-11-09",
            "facility_id": "FAC-01",
            "facility_name": "Pollachi Government Taluk Hospital",
            "registration_method": "ASSISTED_SERVICE",
            "transport_required": True,
            "status": "TRANSPORT_FAILED",
            "cancellation_reason": "Valparai Ghat road monsoon obstruction; vehicle cancelled.",
            "created_at": recent_iso,
            "updated_at": recent_iso,
            "is_demo_record": True
        },
        {
            "id": "RC-2026-2008",
            "patient_name": "Kalyani T.",
            "age": 49,
            "contact_number": "9842102008",
            "village": "Valparai Hills",
            "preferred_service_id": "SRV-GEN",
            "preferred_service_name": "General Medicine",
            "preferred_date": "2026-11-02",
            "facility_id": "FAC-04",
            "facility_name": "Valparai Tribal Upgraded Primary Health Centre",
            "registration_method": "SELF_SERVICE",
            "transport_required": False,
            "status": "FACILITY_VERIFICATION_PENDING",
            "created_at": old_iso,
            "updated_at": old_iso,
            "is_demo_record": True
        },
        # Omalur: Voluntary patient cancellation -> MUST BE EXCLUDED from unmet demand
        {
            "id": "RC-2026-2009",
            "patient_name": "Loganathan M.",
            "age": 42,
            "contact_number": "9842102009",
            "village": "Omalur",
            "preferred_service_id": "SRV-GEN",
            "preferred_service_name": "General Medicine",
            "preferred_date": "2026-11-10",
            "facility_id": "FAC-02",
            "facility_name": "Omalur Community Health Centre (CHC)",
            "registration_method": "SELF_SERVICE",
            "transport_required": False,
            "status": "CANCELLED",
            "cancellation_reason": "Patient attended family function; cancelled by user voluntarily.",
            "created_at": recent_iso,
            "updated_at": recent_iso,
            "is_demo_record": True
        }
    ]

    for j in radar_journeys:
        journeys_ref.document(j["id"]).set(j)

    radar_events = [
        {"id": "EV-RADAR-01", "journey_id": "RC-2026-2001", "event_type": "APPOINTMENT_REJECTED", "actor": "Madurai GH Staff", "description": "Appointment rejected: Obstetric specialist unavailable.", "timestamp": recent_iso},
        {"id": "EV-RADAR-02", "journey_id": "RC-2026-2002", "event_type": "TRANSPORT_CANCELLED", "actor": "Driver Alagappan", "description": "Transport cancelled: Vehicle broken down.", "timestamp": recent_iso},
        {"id": "EV-RADAR-04", "journey_id": "RC-2026-2004", "event_type": "APPOINTMENT_REJECTED", "actor": "Pollachi GH Staff", "description": "Appointment rejected: Slit-lamp under maintenance.", "timestamp": recent_iso},
        {"id": "EV-RADAR-05", "journey_id": "RC-2026-2005", "event_type": "APPOINTMENT_REJECTED", "actor": "Pollachi GH Staff", "description": "Appointment rejected: Eye specialist on leave.", "timestamp": recent_iso},
        {"id": "EV-RADAR-07", "journey_id": "RC-2026-2007", "event_type": "TRANSPORT_CANCELLED", "actor": "Driver Murugan S.", "description": "Ghat road obstruction, transport failed.", "timestamp": recent_iso},
        {"id": "EV-RADAR-09", "journey_id": "RC-2026-2009", "event_type": "JOURNEY_CANCELLED", "actor": "Patient", "description": "Cancelled by user voluntarily due to family event.", "timestamp": recent_iso}
    ]

    for ev in radar_events:
        db.collection("journey_events").document(ev["id"]).set(ev)
    print("Radar demo data successfully seeded.")

def seed_recommendation_demo_data(db):
    """Seed coordinates and verified alternative referral facility (FAC-06)."""
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    # Update facility coordinates if not set
    coords_map = {
        "FAC-01": (10.6580, 77.0090),
        "FAC-02": (11.7430, 78.0410),
        "FAC-03": (9.9252, 78.1198),
        "FAC-04": (10.3260, 76.9550),
        "FAC-05": (10.9230, 77.0390),
    }
    for fid, (lat, lng) in coords_map.items():
        doc = db.collection("facilities").document(fid).get()
        if doc.exists:
            d = doc.to_dict()
            if "latitude" not in d or "longitude" not in d:
                db.collection("facilities").document(fid).update({
                    "latitude": lat,
                    "longitude": lng,
                    "is_active": True
                })

    # Update village coordinates if not set
    vil_coords = {
        "VIL-01": (10.9230, 77.0390),
        "VIL-02": (11.7430, 78.0410),
        "VIL-03": (10.3260, 76.9550),
        "VIL-04": (10.0270, 78.3360),
        "VIL-05": (10.7410, 77.0790),
        "VIL-06": (9.8240, 78.0170),
    }
    for vid, (lat, lng) in vil_coords.items():
        doc = db.collection("villages").document(vid).get()
        if doc.exists:
            d = doc.to_dict()
            if "latitude" not in d or "longitude" not in d:
                db.collection("villages").document(vid).update({
                    "latitude": lat,
                    "longitude": lng
                })

    # Seed FAC-06 if not present
    if not db.collection("facilities").document("FAC-06").get().exists:
        fac_06 = {
            "id": "FAC-06",
            "name": "Coimbatore Government Medical College Hospital",
            "name_ta": "கோயம்புத்தூர் அரசு மருத்துவக் கல்லூரி மருத்துவமனை",
            "facility_type": "District Tertiary Referral Hospital",
            "location": "Trichy Road, Sungam, Coimbatore",
            "district": "Coimbatore",
            "contact_phone": "+91 422 230 1393",
            "verification_status": "VERIFIED",
            "last_verified_at": now_iso,
            "verification_valid_until": (now_utc + timedelta(days=7)).isoformat(),
            "verified_by_staff": "Dr. P. Nirmala (Dean & Chief Medical Officer)",
            "available_services": ["SRV-GEN", "SRV-EYE", "SRV-DIA", "SRV-ANC", "SRV-ORT", "SRV-PED"],
            "operating_hours": "08:00 AM - 04:30 PM (OPD)",
            "total_capacity_today": 50,
            "allocated_appointments_today": 14,
            "verification_notes": "All departments active with on-duty specialist consultants.",
            "latitude": 11.0016,
            "longitude": 76.9664,
            "is_active": True,
            "is_demo_record": True
        }
        db.collection("facilities").document("FAC-06").set(fac_06)

def generate_patient_id(db):
    """Generate a clean, memorable Patient ID: PID-2026-XXXX."""
    import random
    for _ in range(10):
        num = random.randint(1000, 9999)
        pid = f"PID-2026-{num}"
        if not db.collection("patients").document(pid).get().exists:
            return pid
    return f"PID-2026-{uuid.uuid4().hex[:4].upper()}"

def seed_workflow_demo_data(db):
    """Seed synthetic demo patients, pharmacies, and demo prescriptions for complete workflow integration."""
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    # 1. Patients
    patients = [
        {
            "id": "PID-2026-1001",
            "name": "Ramasamy Gounder",
            "name_ta": "ராமசாமி கவுண்டர்",
            "age": 68,
            "contact_number": "9842100001",
            "village": "Chettipalayam",
            "consent_given": True,
            "registered_at": "2026-11-12T08:00:00Z",
            "is_demo_record": True
        },
        {
            "id": "PID-2026-1002",
            "name": "Kannagi M.",
            "name_ta": "கண்ணகி எம்.",
            "age": 29,
            "contact_number": "9842200002",
            "village": "Omalur",
            "consent_given": True,
            "registered_at": "2026-11-12T09:00:00Z",
            "is_demo_record": True
        },
        {
            "id": "PID-2026-2001",
            "name": "Meenakshi S.",
            "name_ta": "மீனாட்சி எஸ்.",
            "age": 27,
            "contact_number": "9842102001",
            "village": "Melur Rural",
            "consent_given": True,
            "registered_at": "2026-11-10T08:00:00Z",
            "is_demo_record": True
        }
    ]
    for p in patients:
        if not db.collection("patients").document(p["id"]).get().exists:
            db.collection("patients").document(p["id"]).set(p)

    # Link patient_id to existing demo care journeys if missing
    journey_patient_map = {
        "RC-2026-1001": "PID-2026-1001",
        "RC-2026-1002": "PID-2026-1002",
        "RC-2026-2001": "PID-2026-2001"
    }
    for jid, pid in journey_patient_map.items():
        doc = db.collection("care_journeys").document(jid).get()
        if doc.exists and not doc.to_dict().get("patient_id"):
            db.collection("care_journeys").document(jid).update({"patient_id": pid})

    # 2. Verified Rural Pharmacies
    pharmacies = [
        {
            "id": "PHARM-01",
            "name": "Pollachi Jan Aushadhi Rural Medicals",
            "name_ta": "பொள்ளாச்சி மக்கள் மருந்தகம்",
            "district": "Coimbatore",
            "village": "Chettipalayam",
            "location": "Near Taluk Hospital Gate, Pollachi",
            "contact_phone": "+91 4259 224488",
            "is_verified": True,
            "verification_status": "VERIFIED",
            "verified_by": "State Drug Controller Inspection Desk",
            "operating_hours": "08:30 AM - 08:30 PM",
            "home_delivery_available": True,
            "delivery_radius_km": 15,
            "delivery_charge_inr": 20,
            "stock": [
                {"name": "Metformin 500mg", "brand": "Jan Aushadhi Metformin", "category": "Diabetes", "status": "IN_STOCK", "quantity": 120, "price_inr": 18.0},
                {"name": "Amlodipine 5mg", "brand": "Jan Aushadhi Amlodipine", "category": "Hypertension", "status": "IN_STOCK", "quantity": 90, "price_inr": 14.5},
                {"name": "Paracetamol 650mg", "brand": "Jan Aushadhi Paracetamol", "category": "Analgesic / Fever", "status": "IN_STOCK", "quantity": 300, "price_inr": 12.0},
                {"name": "Amoxicillin 500mg", "brand": "Amox-Rural", "category": "Antibiotic", "status": "PARTIALLY_AVAILABLE", "quantity": 12, "price_inr": 45.0},
                {"name": "Insulin Regular 40IU", "brand": "Human Insulin R", "category": "Critical Diabetes", "status": "OUT_OF_STOCK", "quantity": 0, "price_inr": 140.0}
            ],
            "is_demo_record": True
        },
        {
            "id": "PHARM-02",
            "name": "Omalur Community Health Pharmacy",
            "name_ta": "ஓமலூர் சமுதாய சுகாதார மருந்தகம்",
            "district": "Salem",
            "village": "Omalur",
            "location": "Omalur Bus Stand Complex, Salem",
            "contact_phone": "+91 4290 221199",
            "is_verified": True,
            "verification_status": "VERIFIED",
            "verified_by": "Salem District Health Inspector",
            "operating_hours": "09:00 AM - 08:00 PM",
            "home_delivery_available": True,
            "delivery_radius_km": 12,
            "delivery_charge_inr": 25,
            "stock": [
                {"name": "Iron & Folic Acid Tablets", "brand": "Maternal Care Iron", "category": "Antenatal", "status": "IN_STOCK", "quantity": 400, "price_inr": 10.0},
                {"name": "Calcium & Vitamin D3", "brand": "Rural Cal-D", "category": "Antenatal", "status": "IN_STOCK", "quantity": 250, "price_inr": 22.0},
                {"name": "Paracetamol 500mg", "brand": "Jan Aushadhi Paracetamol", "category": "Analgesic", "status": "IN_STOCK", "quantity": 200, "price_inr": 10.0}
            ],
            "is_demo_record": True
        },
        {
            "id": "PHARM-03",
            "name": "Melur Rural Cooperative Pharmacy",
            "name_ta": "மேலூர் கூட்டுறவு மருந்தகம்",
            "district": "Madurai",
            "village": "Melur Rural",
            "location": "Main Road, Melur, Madurai",
            "contact_phone": "+91 452 241 1234",
            "is_verified": True,
            "verification_status": "VERIFIED",
            "verified_by": "Madurai Regional Drug Inspector",
            "operating_hours": "09:00 AM - 07:00 PM",
            "home_delivery_available": False,
            "delivery_radius_km": 0,
            "delivery_charge_inr": 0,
            "stock": [
                {"name": "Iron & Folic Acid Tablets", "brand": "Maternal Care Iron", "category": "Antenatal", "status": "OUT_OF_STOCK", "quantity": 0, "price_inr": 10.0},
                {"name": "Paracetamol 500mg", "brand": "Paracetamol-TN", "category": "Analgesic", "status": "IN_STOCK", "quantity": 50, "price_inr": 10.0}
            ],
            "is_demo_record": True
        }
    ]
    for ph in pharmacies:
        if not db.collection("pharmacies").document(ph["id"]).get().exists:
            db.collection("pharmacies").document(ph["id"]).set(ph)

    # 3. Prescriptions
    demo_rx = {
        "id": "RX-2026-1001",
        "journey_id": "RC-2026-1001",
        "patient_id": "PID-2026-1001",
        "patient_name": "Ramasamy Gounder",
        "patient_phone": "9842100001",
        "village": "Chettipalayam",
        "doctor_name": "Dr. K. Revathi (Chief Medical Officer)",
        "facility_id": "FAC-01",
        "facility_name": "Pollachi Government Taluk Hospital",
        "consultation_date": "2026-11-12",
        "clinical_urgency": "ROUTINE",
        "doctor_notes": "Patient undergoing routine dialysis maintenance. Blood pressure stable. Continue regular morning hypertension tablet.",
        "medicines": [
            {"name": "Metformin 500mg", "dosage": "1 tablet twice daily after meals", "days": 30, "quantity": 60, "category": "Diabetes"},
            {"name": "Amlodipine 5mg", "dosage": "1 tablet at bedtime", "days": 30, "quantity": 30, "category": "Hypertension"}
        ],
        "verification_type": "DOCTOR_AUTHENTICATED",
        "is_verified": True,
        "valid_until": (now_utc + timedelta(days=30)).strftime("%Y-%m-%d"),
        "created_at": "2026-11-12T10:30:00Z",
        "refill_reminder_days": 30,
        "refill_reminder_scheduled": True,
        "is_demo_record": True
    }
    if not db.collection("prescriptions").document(demo_rx["id"]).get().exists:
        db.collection("prescriptions").document(demo_rx["id"]).set(demo_rx)


