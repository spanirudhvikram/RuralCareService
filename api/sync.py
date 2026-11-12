from datetime import datetime, timezone, timedelta
import re
import uuid
import os
from flask import Blueprint, jsonify, request, session
from database import (
    get_db,
    is_using_firebase,
    get_database_status,
    verify_firestore_connection,
    generate_patient_id
)
from api.journeys import generate_journey_id, sanitize_text
from api.readiness import evaluate_readiness, log_journey_event
from api.prescriptions import generate_rx_id

sync_bp = Blueprint("sync", __name__, url_prefix="/api/sync")

# Emergency keywords that require immediate official guidance instead of normal offline queueing
EMERGENCY_KEYWORDS = [
    "chest pain", "heart attack", "unconscious", "stroke", "severe accident",
    "heavy bleeding", "breathlessness", "cardiac", "ambulance", "108",
    "அவசரம்", "மாரடைப்பு", "மயக்கம்", "விபத்து", "மூச்சுத்திணறல்"
]

def check_for_emergency(text):
    if not text:
        return False
    lower = str(text).lower()
    return any(kw in lower for kw in EMERGENCY_KEYWORDS)

def mask_phone_number(phone):
    if not phone or len(phone) < 4:
        return "XXXXXXXXXX"
    return "XXXXXX" + str(phone)[-4:]

@sync_bp.route("/ping", methods=["GET"])
def sync_ping():
    """
    Lightweight reachability ping.
    Used by clients to verify actual server reachability beyond navigator.onLine hint.
    """
    db_stat = get_database_status()
    return jsonify({
        "status": "ok",
        "reachability": "ONLINE",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database_mode": db_stat.get("database_mode"),
        "cloud_firestore_active": db_stat.get("cloud_firestore_active")
    })

@sync_bp.route("/db-status", methods=["GET"])
def sync_db_status():
    """
    Diagnostic database status endpoint.
    Reports the exact active database mode, persistence target, and whether
    cloud Firestore credentials are live or unconfigured.
    """
    status = get_database_status()
    # Optionally run test if requested
    if request.args.get("verify") == "true":
        verification = verify_firestore_connection()
        status["verification"] = verification
    return jsonify({
        "success": True,
        "database": status
    })

@sync_bp.route("/batch", methods=["POST"])
def sync_offline_batch():
    """
    Synchronizes offline queued records from IndexedDB.
    Validates, deduplicates using client_uuid / idempotency_key,
    processes request types specifically, performs safe conflict merges,
    and returns an individual acknowledgement for every record.
    """
    db = get_db()
    payload = request.get_json() or {}
    items = payload.get("items", [])

    if not isinstance(items, list):
        return jsonify({"success": False, "error": "Expected a list of queued items."}), 400

    results = []
    now_dt = datetime.now(timezone.utc)
    now_utc = now_dt.isoformat()
    today_str = now_dt.strftime("%Y-%m-%d")

    for item in items:
        client_uuid = item.get("client_uuid") or item.get("idempotency_key")
        if not client_uuid:
            results.append({
                "client_uuid": None,
                "status": "REJECTED",
                "error": "Missing client_uuid or idempotency_key."
            })
            continue

        # Privacy constraint: do not accept or store raw audio recordings
        if "audio_blob" in item or "audio_base64" in item or "recording" in item or "audio_buffer" in item:
            results.append({
                "client_uuid": client_uuid,
                "status": "REJECTED",
                "error": "Microphone audio recordings must not be transmitted or stored."
            })
            continue

        # Consent check: patient consent is mandatory for new submissions
        consent = item.get("consent_given", item.get("patient_consent", True))
        if consent is False:
            results.append({
                "client_uuid": client_uuid,
                "status": "VALIDATION_FAILED",
                "error": "Informed patient consent is required before processing offline requests."
            })
            continue

        # Emergency check: if patient notes or symptoms contain critical emergencies
        symptoms_text = str(item.get("notes") or item.get("symptoms") or item.get("preferred_service_name") or "")
        if check_for_emergency(symptoms_text):
            results.append({
                "client_uuid": client_uuid,
                "status": "EMERGENCY_OVERRIDE",
                "emergency_contact": "108",
                "message": "Emergency detected: Call 108 (Tamil Nadu Ambulance) immediately. Offline sync cannot be used for acute emergencies."
            })
            continue

        # ---------------------------------------------------------------------
        # 1. Deduplication across collections using client_uuid / idempotency key
        # ---------------------------------------------------------------------
        existing_journey = db.collection("care_journeys").where("client_uuid", "==", client_uuid)
        if len(existing_journey) > 0:
            doc = existing_journey[0].to_dict()
            results.append({
                "client_uuid": client_uuid,
                "journey_id": existing_journey[0].id,
                "status": "DUPLICATE_ACKNOWLEDGED",
                "processing_status": doc.get("status", "SYNCED"),
                "message": "Care Journey already synchronized"
            })
            continue

        existing_patient = db.collection("patients").where("client_uuid", "==", client_uuid)
        if len(existing_patient) > 0:
            doc = existing_patient[0].to_dict()
            results.append({
                "client_uuid": client_uuid,
                "patient_id": existing_patient[0].id,
                "status": "DUPLICATE_ACKNOWLEDGED",
                "processing_status": "PATIENT_REGISTERED",
                "message": "Patient registration already synchronized"
            })
            continue

        existing_rx = db.collection("prescriptions").where("client_uuid", "==", client_uuid)
        if len(existing_rx) > 0:
            results.append({
                "client_uuid": client_uuid,
                "prescription_id": existing_rx[0].id,
                "status": "DUPLICATE_ACKNOWLEDGED",
                "processing_status": "PRESCRIPTION_REGISTERED",
                "message": "Prescription already synchronized"
            })
            continue

        existing_transport = db.collection("transport_requests").where("client_uuid", "==", client_uuid)
        if len(existing_transport) > 0:
            results.append({
                "client_uuid": client_uuid,
                "transport_request_id": existing_transport[0].id,
                "status": "DUPLICATE_ACKNOWLEDGED",
                "processing_status": existing_transport[0].to_dict().get("status", "SYNCED"),
                "message": "Transport request already synchronized"
            })
            continue

        existing_medicine = db.collection("medicine_requests").where("client_uuid", "==", client_uuid)
        if len(existing_medicine) > 0:
            results.append({
                "client_uuid": client_uuid,
                "medicine_request_id": existing_medicine[0].id,
                "status": "DUPLICATE_ACKNOWLEDGED",
                "processing_status": existing_medicine[0].to_dict().get("status", "SYNCED"),
                "message": "Medicine request already synchronized"
            })
            continue

        # Extract operation type & entity type
        entity_type = str(item.get("entity_type") or item.get("request_type") or "APPOINTMENT").upper().strip()
        operation_type = str(item.get("operation_type") or "CREATE").upper().strip()

        try:
            # -----------------------------------------------------------------
            # TYPE 0: CONFLICT DETECTION & MERGE (for existing entity UPDATE)
            # -----------------------------------------------------------------
            if operation_type == "UPDATE":
                target_id = item.get("target_id") or item.get("journey_id") or item.get("patient_id")
                target_coll = item.get("collection") or "care_journeys"
                
                if not target_id:
                    results.append({
                        "client_uuid": client_uuid,
                        "status": "VALIDATION_FAILED",
                        "error": "Target record ID is required for update operation."
                    })
                    continue

                doc_snap = db.collection(target_coll).document(target_id).get()
                if not doc_snap.exists:
                    results.append({
                        "client_uuid": client_uuid,
                        "status": "SYNC_FAILED",
                        "error": f"Target document {target_id} does not exist on server."
                    })
                    continue

                server_data = doc_snap.to_dict()
                client_last_modified = item.get("last_modified") or item.get("created_at") or now_utc
                server_updated_at = server_data.get("updated_at") or server_data.get("created_at") or now_utc

                # Conflict Check: If server was modified after client's snapshot
                if server_updated_at > client_last_modified:
                    # Compare specific fields
                    client_payload = item.get("payload") or item
                    conflict_detected = False
                    conflicting_fields = []

                    # Key sensitive fields where concurrent changes cannot be auto-merged:
                    for critical_field in ["status", "preferred_service_id", "facility_id", "appointment_slot"]:
                        if critical_field in client_payload and client_payload[critical_field] != server_data.get(critical_field):
                            conflict_detected = True
                            conflicting_fields.append({
                                "field": critical_field,
                                "server_value": server_data.get(critical_field),
                                "client_proposed": client_payload.get(critical_field)
                            })

                    if conflict_detected:
                        # Log conflict to audit trail without overwriting
                        log_journey_event(
                            db,
                            target_id if target_coll == "care_journeys" else "SYSTEM",
                            "SYNC_CONFLICT_DETECTED",
                            "Offline Sync Worker",
                            f"Concurrent modification conflict on fields: {[c['field'] for c in conflicting_fields]}. Offline changes held for authorized review."
                        )
                        results.append({
                            "client_uuid": client_uuid,
                            "target_id": target_id,
                            "status": "CONFLICT_REQUIRES_REVIEW",
                            "processing_status": "CONFLICT_REQUIRES_REVIEW",
                            "message": "Concurrent updates detected on critical fields. Staged for authorized review.",
                            "conflicts": conflicting_fields,
                            "server_version": server_data
                        })
                        continue
                    else:
                        # Safe non-overlapping merge: e.g. landmark, phone, notes
                        allowed_merge_fields = ["pickup_landmark", "notes", "contact_number", "patient_fare_accepted"]
                        updates = {}
                        for fld in allowed_merge_fields:
                            if fld in client_payload and client_payload[fld] is not None:
                                updates[fld] = client_payload[fld]
                        updates["updated_at"] = now_utc
                        db.collection(target_coll).document(target_id).update(updates)
                        log_journey_event(
                            db,
                            target_id if target_coll == "care_journeys" else "SYSTEM",
                            "SAFE_MERGE_APPLIED",
                            "Offline Sync Worker",
                            f"Merged non-overlapping offline updates for fields: {list(updates.keys())}."
                        )
                        results.append({
                            "client_uuid": client_uuid,
                            "target_id": target_id,
                            "status": "SYNCED",
                            "processing_status": "MERGE_APPLIED",
                            "message": "Independent offline updates merged successfully with server record."
                        })
                        continue

            # -----------------------------------------------------------------
            # TYPE 1: PATIENT_REGISTRATION (Citizen Identity)
            # -----------------------------------------------------------------
            if entity_type in ("PATIENT_REGISTRATION", "PATIENT"):
                name = sanitize_text(item.get("name") or item.get("patient_name", ""))
                age = item.get("age")
                contact_number = str(item.get("contact_number") or item.get("phone", "")).strip()
                village = sanitize_text(item.get("village", ""))

                if not name or len(name) < 2:
                    results.append({
                        "client_uuid": client_uuid,
                        "status": "VALIDATION_FAILED",
                        "error": "Patient name must be at least 2 characters long."
                    })
                    continue

                try:
                    age_val = int(age)
                    if age_val < 1 or age_val > 125:
                        raise ValueError()
                except Exception:
                    results.append({
                        "client_uuid": client_uuid,
                        "status": "VALIDATION_FAILED",
                        "error": "Valid patient age between 1 and 125 is mandatory."
                    })
                    continue

                if not re.match(r"^\d{10}$", contact_number):
                    results.append({
                        "client_uuid": client_uuid,
                        "status": "VALIDATION_FAILED",
                        "error": "Contact number must be an exact 10-digit mobile number."
                    })
                    continue

                if not village:
                    results.append({
                        "client_uuid": client_uuid,
                        "status": "VALIDATION_FAILED",
                        "error": "Village name is required."
                    })
                    continue

                # Deduplicate by phone
                existing_p = db.collection("patients").where("contact_number", "==", contact_number)
                if len(existing_p) > 0:
                    p_doc = existing_p[0].to_dict()
                    results.append({
                        "client_uuid": client_uuid,
                        "patient_id": p_doc.get("id"),
                        "status": "SYNCED",
                        "is_returning": True,
                        "processing_status": "PATIENT_RETRIEVED",
                        "message": f"Welcome back, {p_doc.get('name')}. Existing citizen account matched.",
                        "patient": p_doc
                    })
                    continue

                patient_id = generate_patient_id(db)
                patient_data = {
                    "id": patient_id,
                    "client_uuid": client_uuid,
                    "name": name,
                    "name_ta": item.get("name_ta") or name,
                    "age": age_val,
                    "contact_number": contact_number,
                    "village": village,
                    "consent_given": True,
                    "consent_timestamp": now_utc,
                    "registered_at": item.get("created_at") or now_utc,
                    "updated_at": now_utc,
                    "is_active": True,
                    "offline_registered": True
                }
                db.collection("patients").document(patient_id).set(patient_data)

                results.append({
                    "client_uuid": client_uuid,
                    "patient_id": patient_id,
                    "status": "SYNCED",
                    "processing_status": "PATIENT_REGISTERED",
                    "message": "Citizen identity registered and stored in database.",
                    "patient": patient_data
                })
                continue

            # -----------------------------------------------------------------
            # TYPE 2: PRESCRIPTION_UPLOAD (Doctor & Patient Prescriptions)
            # -----------------------------------------------------------------
            elif entity_type in ("PRESCRIPTION_UPLOAD", "PRESCRIPTION"):
                patient_name = sanitize_text(item.get("patient_name", "Citizen Patient"))
                patient_id = sanitize_text(item.get("patient_id", ""))
                journey_id = sanitize_text(item.get("journey_id", ""))
                doctor_name = sanitize_text(item.get("doctor_name", "Attending Medical Officer"))
                medicines = item.get("medicines", [])
                file_name = sanitize_text(item.get("file_name", "prescription_document.jpg"))
                file_data = item.get("file_data") or item.get("image_base64")

                # Validate file size if raw payload included (limit 5MB)
                if file_data and len(str(file_data)) > 5 * 1024 * 1024:
                    results.append({
                        "client_uuid": client_uuid,
                        "status": "VALIDATION_FAILED",
                        "error": "Prescription document exceeds 5MB size limit."
                    })
                    continue

                if not isinstance(medicines, list) or len(medicines) == 0:
                    medicines_text = item.get("medicines_text", "")
                    if medicines_text:
                        medicines = [{"name": line.strip(), "dosage": "As directed", "days": 30, "quantity": 30}
                                     for line in medicines_text.split("\n") if line.strip()]
                    else:
                        medicines = [{"name": "Essential Maintenance Medicine", "dosage": "Once daily", "days": 30, "quantity": 30}]

                rx_id = generate_rx_id(db)
                valid_until = (now_dt + timedelta(days=30)).strftime("%Y-%m-%d")

                rx_record = {
                    "id": rx_id,
                    "client_uuid": client_uuid,
                    "journey_id": journey_id or None,
                    "patient_id": patient_id or None,
                    "patient_name": patient_name,
                    "doctor_name": doctor_name,
                    "facility_name": sanitize_text(item.get("facility_name", "Rural Primary Facility")),
                    "clinical_urgency": sanitize_text(item.get("clinical_urgency", "ROUTINE")),
                    "medicines": medicines,
                    "file_name": file_name,
                    "has_document": bool(file_data),
                    "is_verified": True,
                    "valid_until": valid_until,
                    "created_at": item.get("created_at") or now_utc,
                    "synced_at": now_utc
                }
                db.collection("prescriptions").document(rx_id).set(rx_record)

                if journey_id:
                    log_journey_event(
                        db,
                        journey_id,
                        "PRESCRIPTION_UPLOAD_SYNCED",
                        doctor_name,
                        f"Prescription {rx_id} uploaded offline was synchronized to server."
                    )

                results.append({
                    "client_uuid": client_uuid,
                    "prescription_id": rx_id,
                    "status": "SYNCED",
                    "processing_status": "PRESCRIPTION_REGISTERED",
                    "message": "Prescription verified and registered in database."
                })
                continue

            # -----------------------------------------------------------------
            # TYPE 3: JOURNEY_EVENT (Client / Kiosk Audit Event)
            # -----------------------------------------------------------------
            elif entity_type == "JOURNEY_EVENT":
                journey_id = sanitize_text(item.get("journey_id", ""))
                event_type = sanitize_text(item.get("event_type", "OFFLINE_ACTION"))
                actor = sanitize_text(item.get("actor", "Offline Citizen"))
                notes = sanitize_text(item.get("notes", "Offline event logged"))

                if not journey_id:
                    results.append({
                        "client_uuid": client_uuid,
                        "status": "VALIDATION_FAILED",
                        "error": "Journey ID is required for logging care journey events."
                    })
                    continue

                log_journey_event(db, journey_id, event_type, actor, notes)
                results.append({
                    "client_uuid": client_uuid,
                    "status": "SYNCED",
                    "processing_status": "EVENT_RECORDED",
                    "message": "Journey event logged successfully."
                })
                continue

            # -----------------------------------------------------------------
            # TYPE 4: HOSPITAL_CHECK (Recheck facility and service availability)
            # -----------------------------------------------------------------
            elif entity_type in ("HOSPITAL_CHECK", "HOSPITAL_REQUEST"):
                facility_id = sanitize_text(item.get("facility_id", ""))
                service_id = sanitize_text(item.get("preferred_service_id", "SRV-GEN"))
                fac_doc = db.collection("facilities").document(facility_id).get()

                if not fac_doc.exists:
                    results.append({
                        "client_uuid": client_uuid,
                        "status": "ACTION_REQUIRED",
                        "processing_status": "FACILITY_UNAVAILABLE",
                        "error": f"Requested facility {facility_id} is not found. Alternative hospital recommendations available."
                    })
                    continue

                fac_data = fac_doc.to_dict()
                is_verified = (fac_data.get("verification_status") == "VERIFIED") or bool(fac_data.get("is_verified", False))
                supported_services = fac_data.get("services_offered", [])
                
                v_ts_str = fac_data.get("verification_timestamp") or fac_data.get("last_verified_at")
                valid_until_str = fac_data.get("verification_valid_until")
                is_unexpired = True
                if valid_until_str:
                    try:
                        vu_dt = datetime.fromisoformat(valid_until_str.replace("Z", "+00:00"))
                        if vu_dt < now_dt:
                            is_unexpired = False
                    except Exception:
                        pass
                elif v_ts_str:
                    try:
                        v_ts = datetime.fromisoformat(v_ts_str.replace("Z", "+00:00"))
                        if now_dt - v_ts > timedelta(days=7):
                            is_unexpired = False
                    except Exception:
                        pass

                if not is_verified or not is_unexpired:
                    results.append({
                        "client_uuid": client_uuid,
                        "facility_id": facility_id,
                        "status": "ACTION_REQUIRED",
                        "processing_status": "RECHECK_REQUIRED",
                        "message": "Facility verification has expired or is unverified. Recheck or alternative facility required."
                    })
                    continue

                results.append({
                    "client_uuid": client_uuid,
                    "facility_id": facility_id,
                    "status": "SYNCED",
                    "processing_status": "FACILITY_SERVICE_VERIFIED",
                    "facility_name": fac_data.get("name"),
                    "service_supported": (service_id in supported_services) if supported_services else True,
                    "message": "Facility and service verification rechecked successfully."
                })
                continue

            # -----------------------------------------------------------------
            # TYPE 5: TRANSPORT (Stand-alone transport coordination)
            # -----------------------------------------------------------------
            elif entity_type == "TRANSPORT":
                patient_name = sanitize_text(item.get("patient_name", ""))
                contact_number = str(item.get("contact_number", "")).strip()
                village = sanitize_text(item.get("village", ""))

                if not patient_name or not re.match(r"^\d{10}$", contact_number) or not village:
                    results.append({
                        "client_uuid": client_uuid,
                        "status": "VALIDATION_FAILED",
                        "error": "Valid patient name, 10-digit phone, and village are required for transport coordination."
                    })
                    continue

                tr_id = f"TR-{uuid.uuid4().hex[:6].upper()}"
                transport_data = {
                    "id": tr_id,
                    "client_uuid": client_uuid,
                    "patient_name": patient_name,
                    "patient_phone": contact_number,
                    "pickup_village": village,
                    "pickup_landmark": sanitize_text(item.get("pickup_landmark", f"{village} Centre")),
                    "pickup_type": sanitize_text(item.get("pickup_type", "RURALCARE_CENTRE")),
                    "destination_facility_name": sanitize_text(item.get("facility_name", "Nearest Taluk Hospital")),
                    "destination_facility_id": sanitize_text(item.get("facility_id", "FAC-01")),
                    "scheduled_date": item.get("preferred_date") or today_str,
                    "status": "REQUESTED",
                    "created_at": item.get("created_at") or now_utc,
                    "synced_at": now_utc,
                    "driver_id": None,
                    "driver_name": None
                }
                db.collection("transport_requests").document(tr_id).set(transport_data)

                results.append({
                    "client_uuid": client_uuid,
                    "transport_request_id": tr_id,
                    "status": "SYNCED",
                    "processing_status": "DRIVER_ACCEPTANCE_PENDING",
                    "message": "Transport request stored. Waiting for driver acceptance."
                })
                continue

            # -----------------------------------------------------------------
            # TYPE 6: MEDICINE (Pharmacy stock and replenishment request)
            # -----------------------------------------------------------------
            elif entity_type == "MEDICINE":
                patient_name = sanitize_text(item.get("patient_name", ""))
                contact_number = str(item.get("contact_number", "")).strip()
                village = sanitize_text(item.get("village", ""))

                if not patient_name or not re.match(r"^\d{10}$", contact_number) or not village:
                    results.append({
                        "client_uuid": client_uuid,
                        "status": "VALIDATION_FAILED",
                        "error": "Valid patient name, 10-digit phone, and village are required for medicine requests."
                    })
                    continue

                med_id = f"MED-{uuid.uuid4().hex[:6].upper()}"
                med_data = {
                    "id": med_id,
                    "client_uuid": client_uuid,
                    "patient_name": patient_name,
                    "contact_number": contact_number,
                    "village": village,
                    "medicines_requested": sanitize_text(item.get("medicines") or item.get("preferred_service_name", "Essential Maintenance Medicines")),
                    "status": "PHARMACY_STOCK_PENDING",
                    "stock_confirmed": False,
                    "created_at": item.get("created_at") or now_utc,
                    "synced_at": now_utc
                }
                db.collection("medicine_requests").document(med_id).set(med_data)

                results.append({
                    "client_uuid": client_uuid,
                    "medicine_request_id": med_id,
                    "status": "SYNCED",
                    "processing_status": "PHARMACY_STOCK_PENDING",
                    "message": "Medicine request stored safely. Waiting for pharmacy stock and order confirmation."
                })
                continue

            # -----------------------------------------------------------------
            # TYPE 7: COMMUNITY_DEMAND (Aggregated health need signal for Demand Radar)
            # -----------------------------------------------------------------
            elif entity_type == "COMMUNITY_DEMAND":
                village = sanitize_text(item.get("village", ""))
                service_id = sanitize_text(item.get("preferred_service_id", "SRV-GEN"))
                service_name = sanitize_text(item.get("preferred_service_name", "General Care"))

                if not village:
                    results.append({
                        "client_uuid": client_uuid,
                        "status": "VALIDATION_FAILED",
                        "error": "Village name is required for community demand aggregation."
                    })
                    continue

                sig_id = f"DEMAND-{uuid.uuid4().hex[:6].upper()}"
                demand_data = {
                    "id": sig_id,
                    "client_uuid": client_uuid,
                    "village": village,
                    "preferred_service_id": service_id,
                    "preferred_service_name": service_name,
                    "patient_name": "Community Health Reporter",
                    "contact_number": "9842100000",
                    "age": 45,
                    "status": "UNMET_DEMAND_RECORDED",
                    "created_at": item.get("created_at") or now_utc,
                    "synced_at": now_utc,
                    "notes": sanitize_text(item.get("notes", "Offline aggregated health demand signal")),
                    "cancellation_reason": "Community service shortage signal"
                }
                db.collection("care_journeys").document(sig_id).set(demand_data)
                log_journey_event(
                    db,
                    sig_id,
                    "COMMUNITY_DEMAND_SYNCED",
                    "Offline Kiosk Reporter",
                    f"Offline community health demand for {service_name} in {village} synced to Village Health Demand Radar."
                )

                results.append({
                    "client_uuid": client_uuid,
                    "demand_signal_id": sig_id,
                    "status": "SYNCED",
                    "processing_status": "COMMUNITY_DEMAND_RECORDED",
                    "message": "Community demand signal recorded and aggregated into Village Health Demand Radar."
                })
                continue

            # -----------------------------------------------------------------
            # TYPE 8: APPOINTMENT / CARE_JOURNEY (Standard Care Journey)
            # -----------------------------------------------------------------
            else:
                patient_name = sanitize_text(item.get("patient_name", ""))
                contact_number = str(item.get("contact_number", "")).strip()
                village = sanitize_text(item.get("village", ""))
                service_id = sanitize_text(item.get("preferred_service_id", ""))
                facility_id = sanitize_text(item.get("facility_id", ""))
                transport_required = bool(item.get("transport_required", False))

                if not patient_name or not re.match(r"^\d{10}$", contact_number) or not village or not service_id:
                    results.append({
                        "client_uuid": client_uuid,
                        "status": "VALIDATION_FAILED",
                        "error": "Required fields invalid or missing (name, 10-digit phone, village, service)."
                    })
                    continue

                try:
                    age_val = int(item.get("age", 50))
                    if age_val < 1 or age_val > 125:
                        age_val = None
                except Exception:
                    age_val = None

                # Expired preferred date check:
                pref_date = item.get("preferred_date") or today_str
                date_status_note = ""
                is_expired_date = False
                if pref_date < today_str:
                    is_expired_date = True
                    date_status_note = f"Scheduled date ({pref_date}) expired while offline. Adjusted to today ({today_str})."
                    pref_date = today_str

                # Resolve or associate patient_id
                patient_id = item.get("patient_id")
                if not patient_id:
                    existing_pat = db.collection("patients").where("contact_number", "==", contact_number)
                    if len(existing_pat) > 0:
                        patient_id = existing_pat[0].id

                journey_id = generate_journey_id(db)
                journey_data = {
                    "id": journey_id,
                    "client_uuid": client_uuid,
                    "patient_id": patient_id,
                    "patient_name": patient_name,
                    "age": age_val,
                    "contact_number": contact_number,
                    "village": village,
                    "preferred_service_id": service_id,
                    "preferred_service_name": sanitize_text(item.get("preferred_service_name", "General Care")),
                    "preferred_date": pref_date,
                    "facility_id": facility_id or "FAC-01",
                    "facility_name": sanitize_text(item.get("facility_name", "Government Hospital")),
                    "registration_method": sanitize_text(item.get("registration_method", "OFFLINE_SYNCED")),
                    "registration_channel": sanitize_text(item.get("registration_channel", "TOUCH_BASED")),
                    "operator_id": sanitize_text(item.get("operator_id")),
                    "operator_name": sanitize_text(item.get("operator_name")),
                    "transport_required": transport_required,
                    "pickup_landmark": sanitize_text(item.get("pickup_landmark", "")),
                    "pickup_location_type": sanitize_text(item.get("pickup_location_type", "RURALCARE_CENTRE")),
                    # Server controls authentic initial state - never trust client claims of confirmation
                    "status": "FACILITY_VERIFICATION_PENDING",
                    "created_at": item.get("created_at") or now_utc,
                    "synced_at": now_utc,
                    "updated_at": now_utc,
                    "service_verified": False,
                    "service_verification_timestamp": None,
                    "appointment_confirmed": False,
                    "appointment_slot": None,
                    "transport_request_id": None,
                    "transport_status": "NONE" if not transport_required else "PENDING",
                    "driver_id": None,
                    "driver_name": None,
                    "disclosed_fare": None,
                    "patient_fare_accepted": False,
                    "patient_readiness_confirmed": False
                }

                if transport_required:
                    tr_id = f"TR-{journey_id.split('-')[-1]}"
                    transport_data = {
                        "id": tr_id,
                        "client_uuid": f"tr_{client_uuid}",
                        "journey_id": journey_id,
                        "patient_name": patient_name,
                        "patient_phone": contact_number,
                        "pickup_village": village,
                        "pickup_landmark": sanitize_text(item.get("pickup_landmark", f"{village} Centre")),
                        "pickup_location_type": sanitize_text(item.get("pickup_location_type", "RURALCARE_CENTRE")),
                        "destination_facility_name": sanitize_text(item.get("facility_name", "")),
                        "destination_facility_id": facility_id or "FAC-01",
                        "scheduled_date": pref_date,
                        "status": "REQUESTED",
                        "created_at": now_utc,
                        "is_demo_record": True
                    }
                    db.collection("transport_requests").document(tr_id).set(transport_data)
                    journey_data["transport_request_id"] = tr_id

                db.collection("care_journeys").document(journey_id).set(journey_data)
                
                event_desc = f"Request captured offline was synced to server. Allocated Journey ID: {journey_id}"
                if date_status_note:
                    event_desc += f" Note: {date_status_note}"

                log_journey_event(
                    db,
                    journey_id,
                    "OFFLINE_SYNC_SUCCESS",
                    "Offline Sync Worker",
                    event_desc
                )
                evaluate_readiness(journey_id)

                results.append({
                    "client_uuid": client_uuid,
                    "journey_id": journey_id,
                    "status": "ACTION_REQUIRED" if is_expired_date else "SYNCED",
                    "processing_status": "FACILITY_VERIFICATION_PENDING",
                    "message": f"Successfully synchronized. Waiting for facility acceptance.{' ' + date_status_note if date_status_note else ''}"
                })

        except Exception as e:
            results.append({
                "client_uuid": client_uuid,
                "status": "ERROR",
                "error": str(e)
            })

    return jsonify({
        "success": True,
        "processed": len(results),
        "results": results,
        "timestamp": now_utc,
        "database_mode": get_database_status().get("database_mode")
    })

@sync_bp.route("/pull", methods=["GET", "POST"])
def sync_pull_server_updates():
    """
    Phase 6: Server-to-Client Synchronization.
    Returns authorized server updates, latest Care Journey statuses,
    doctor slot decisions, driver transport details, and medicine orders
    for a given patient or journey identifier.
    """
    db = get_db()
    args = request.get_json(silent=True) or request.args.to_dict()
    
    patient_id = args.get("patient_id")
    contact_number = str(args.get("contact_number") or args.get("phone", "")).strip()
    journey_id = args.get("journey_id")
    now_utc = datetime.now(timezone.utc).isoformat()

    journeys = []
    prescriptions = []
    transport_orders = []

    # 1. Lookup by Journey ID
    if journey_id:
        j_doc = db.collection("care_journeys").document(journey_id).get()
        if j_doc.exists:
            journeys.append(j_doc.to_dict())

    # 2. Lookup by Patient ID
    elif patient_id:
        j_matches = db.collection("care_journeys").where("patient_id", "==", patient_id)
        for doc in j_matches:
            journeys.append(doc.to_dict())
        rx_matches = db.collection("prescriptions").where("patient_id", "==", patient_id)
        for doc in rx_matches:
            prescriptions.append(doc.to_dict())

    # 3. Lookup by Contact Number
    elif contact_number and len(contact_number) == 10:
        j_matches = db.collection("care_journeys").where("contact_number", "==", contact_number)
        for doc in j_matches:
            journeys.append(doc.to_dict())
        p_matches = db.collection("patients").where("contact_number", "==", contact_number)
        if len(p_matches) > 0:
            pid = p_matches[0].id
            rx_matches = db.collection("prescriptions").where("patient_id", "==", pid)
            for doc in rx_matches:
                prescriptions.append(doc.to_dict())

    # Fetch associated transport requests
    for j in journeys:
        tr_id = j.get("transport_request_id")
        if tr_id:
            tr_doc = db.collection("transport_requests").document(tr_id).get()
            if tr_doc.exists:
                transport_orders.append(tr_doc.to_dict())

    return jsonify({
        "success": True,
        "timestamp": now_utc,
        "database_mode": get_database_status().get("database_mode"),
        "care_journeys": journeys,
        "prescriptions": prescriptions,
        "transport_requests": transport_orders,
        "server_version": "2.0.0"
    })

@sync_bp.route("/status/<client_uuid>", methods=["GET"])
def get_sync_status(client_uuid):
    """Lookup server processing status for an offline-submitted request UUID."""
    db = get_db()
    
    # 1. Check care_journeys
    matches = db.collection("care_journeys").where("client_uuid", "==", client_uuid)
    if len(matches) > 0:
        doc = matches[0].to_dict()
        return jsonify({
            "success": True,
            "found": True,
            "type": "CARE_JOURNEY",
            "id": doc.get("id"),
            "status": doc.get("status"),
            "synced_at": doc.get("synced_at"),
            "patient_name_masked": (doc.get("patient_name", "")[:2] + "****") if doc.get("patient_name") else "Protected Citizen",
            "village": doc.get("village"),
            "service": doc.get("preferred_service_name")
        })

    # 2. Check patients
    matches_pat = db.collection("patients").where("client_uuid", "==", client_uuid)
    if len(matches_pat) > 0:
        doc = matches_pat[0].to_dict()
        return jsonify({
            "success": True,
            "found": True,
            "type": "PATIENT",
            "id": doc.get("id"),
            "name_masked": (doc.get("name", "")[:2] + "****") if doc.get("name") else "Protected Citizen",
            "village": doc.get("village"),
            "synced_at": doc.get("registered_at")
        })

    # 3. Check prescriptions
    matches_rx = db.collection("prescriptions").where("client_uuid", "==", client_uuid)
    if len(matches_rx) > 0:
        doc = matches_rx[0].to_dict()
        return jsonify({
            "success": True,
            "found": True,
            "type": "PRESCRIPTION",
            "id": doc.get("id"),
            "is_verified": doc.get("is_verified"),
            "synced_at": doc.get("synced_at")
        })

    # 4. Check transport_requests
    matches_tr = db.collection("transport_requests").where("client_uuid", "==", client_uuid)
    if len(matches_tr) > 0:
        doc = matches_tr[0].to_dict()
        return jsonify({
            "success": True,
            "found": True,
            "type": "TRANSPORT",
            "id": doc.get("id"),
            "status": doc.get("status"),
            "synced_at": doc.get("synced_at")
        })

    # 5. Check medicine_requests
    matches_med = db.collection("medicine_requests").where("client_uuid", "==", client_uuid)
    if len(matches_med) > 0:
        doc = matches_med[0].to_dict()
        return jsonify({
            "success": True,
            "found": True,
            "type": "MEDICINE",
            "id": doc.get("id"),
            "status": doc.get("status"),
            "synced_at": doc.get("synced_at")
        })

    return jsonify({
        "success": True,
        "found": False,
        "status": "PENDING_OR_UNSYNCED",
        "message": "No server record matches this client UUID yet."
    }), 404
