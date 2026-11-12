from datetime import datetime, timezone
import random
import uuid
import re
from flask import Blueprint, jsonify, request
from database import get_db
from api.readiness import evaluate_readiness, log_journey_event
from api.auth import require_role, get_authenticated_user

journeys_bp = Blueprint("journeys", __name__, url_prefix="/api/journeys")

def generate_journey_id(db):
    """Generate a clean, memorable Care Journey ID: RC-2026-XXXX."""
    for _ in range(10):
        num = random.randint(1000, 9999)
        jid = f"RC-2026-{num}"
        if not db.collection("care_journeys").document(jid).get().exists:
            return jid
    return f"RC-2026-{uuid.uuid4().hex[:6].upper()}"

def sanitize_text(text):
    if not text:
        return ""
    # Strip HTML tags and potential script injections
    return re.sub(r"[<>]", "", str(text)).strip()

def mask_sensitive_journey(journey):
    """Masks patient private data for unauthorized / public lookups."""
    name = journey.get("patient_name", "")
    phone = journey.get("contact_number", "")
    village = journey.get("village", "")

    masked_name = (name[:2] + "****" + name[-1:]) if len(name) > 3 else "Protected Citizen"
    masked_phone = ("XXXXXX" + phone[-4:]) if len(phone) >= 4 else "XXXXXXXXXX"
    masked_village = (village[:2] + "****") if len(village) > 2 else "Rural Ward"

    return {
        "id": journey.get("id"),
        "status": journey.get("status"),
        "preferred_service_name": journey.get("preferred_service_name"),
        "facility_name": journey.get("facility_name"),
        "preferred_date": journey.get("preferred_date"),
        "patient_name": masked_name,
        "contact_number": masked_phone,
        "village": masked_village,
        "age": "**",
        "transport_required": journey.get("transport_required"),
        "is_restricted_preview": True
    }

@journeys_bp.route("", methods=["POST"])
def create_journey():
    """Create a new Care Journey for a patient (Assisted or Self-Service)."""
    db = get_db()
    payload = request.get_json() or {}

    raw_name = payload.get("patient_name", "")
    patient_name = sanitize_text(raw_name)
    age = payload.get("age")
    contact_number = str(payload.get("contact_number", "")).strip()
    village = sanitize_text(payload.get("village", ""))
    service_id = sanitize_text(payload.get("preferred_service_id", ""))
    preferred_date = payload.get("preferred_date", "").strip()
    facility_id = sanitize_text(payload.get("facility_id", ""))
    transport_required = bool(payload.get("transport_required", False))
    pickup_landmark = sanitize_text(payload.get("pickup_landmark", ""))
    pickup_choice = sanitize_text(payload.get("pickup_choice", "HOME_ADDRESS"))
    patient_reported_symptoms = sanitize_text(payload.get("patient_reported_symptoms") or payload.get("notes") or payload.get("symptoms") or "")
    patient_id = sanitize_text(payload.get("patient_id", ""))

    if patient_id and (not age or not patient_name or not contact_number or not village):
        p_doc = db.collection("patients").document(patient_id).get()
        if p_doc.exists:
            p_data = p_doc.to_dict()
            if not age:
                age = p_data.get("age")
            if not patient_name:
                patient_name = p_data.get("name", "")
            if not contact_number:
                contact_number = str(p_data.get("contact_number", "")).strip()
            if not village:
                village = p_data.get("village", "")

    registration_method = sanitize_text(payload.get("registration_method", "SELF_SERVICE"))
    registration_channel = sanitize_text(payload.get("registration_channel", "TOUCH_BASED"))
    operator_id = sanitize_text(payload.get("operator_id")) if registration_method == "ASSISTED_SERVICE" else None
    operator_name = sanitize_text(payload.get("operator_name")) if registration_method == "ASSISTED_SERVICE" else None
    client_uuid = payload.get("client_uuid")  # For offline idempotency check

    # Strict Input Validations
    if not patient_name or len(patient_name) < 2:
        return jsonify({"success": False, "error": "Patient name must be at least 2 characters long."}), 400

    try:
        age_int = int(age)
        if age_int < 1 or age_int > 125:
            return jsonify({"success": False, "error": "Patient age must be a realistic number between 1 and 125."}), 400
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Patient age is mandatory and must be a valid number."}), 400

    if not re.match(r"^\d{10}$", contact_number):
        return jsonify({"success": False, "error": "Contact number must be a valid 10-digit mobile number."}), 400

    if not village:
        return jsonify({"success": False, "error": "Village / Panchayat is required."}), 400

    if not service_id:
        return jsonify({"success": False, "error": "Required healthcare service must be selected."}), 400

    if preferred_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", preferred_date):
        return jsonify({"success": False, "error": "Preferred visit date must be in YYYY-MM-DD format."}), 400

    now_utc = datetime.now(timezone.utc).isoformat()

    # Idempotency / Duplicate prevention check:
    # 1. By client_uuid if sent by offline sync
    if client_uuid:
        existing_by_uuid = db.collection("care_journeys").where("client_uuid", "==", client_uuid)
        if len(existing_by_uuid) > 0:
            existing = existing_by_uuid[0].to_dict()
            return jsonify({
                "success": True,
                "is_duplicate": True,
                "message": "Duplicate request identified. Existing journey returned.",
                "journey": existing
            })

    # 2. By patient contact + date + service within active requests
    existing_by_phone = db.collection("care_journeys").where("contact_number", "==", contact_number)
    for ej in existing_by_phone:
        d = ej.to_dict()
        if (d.get("preferred_date") == preferred_date and 
            d.get("preferred_service_id") == service_id and 
            d.get("status") not in ("CANCELLED", "COMPLETED")):
            return jsonify({
                "success": True,
                "is_duplicate": True,
                "message": "An active Care Journey already exists for this patient and service date.",
                "journey": d
            })

    # Resolve facility & service details
    service_name = payload.get("preferred_service_name", "")
    if not service_name and service_id:
        s_doc = db.collection("services").document(service_id).get()
        if s_doc.exists:
            service_name = s_doc.to_dict().get("name", "")

    facility_name = payload.get("facility_name", "")
    if not facility_name and facility_id:
        f_doc = db.collection("facilities").document(facility_id).get()
        if f_doc.exists:
            facility_name = f_doc.to_dict().get("name", "")

    # Auto-resolve or register patient in patients collection
    if not patient_id:
        p_docs = db.collection("patients").where("contact_number", "==", contact_number)
        if len(p_docs) > 0:
            patient_id = p_docs[0].id
        else:
            from database import generate_patient_id
            patient_id = generate_patient_id(db)
            db.collection("patients").document(patient_id).set({
                "id": patient_id,
                "name": patient_name,
                "age": age_int,
                "contact_number": contact_number,
                "village": village,
                "consent_given": True,
                "registered_at": now_utc,
                "is_demo_record": True
            })

    journey_id = generate_journey_id(db)

    # Initial status - Backend always controls initial status
    initial_status = "FACILITY_VERIFICATION_PENDING"

    journey_data = {
        "id": journey_id,
        "patient_id": patient_id,
        "client_uuid": client_uuid,
        "patient_name": patient_name,
        "age": age_int,
        "contact_number": contact_number,
        "village": village,
        "preferred_service_id": service_id,
        "preferred_service_name": service_name,
        "preferred_date": preferred_date or datetime.now().strftime("%Y-%m-%d"),
        "facility_id": facility_id,
        "facility_name": facility_name,
        "patient_reported_symptoms": patient_reported_symptoms or service_name,
        "registration_method": registration_method,
        "registration_channel": registration_channel,
        "operator_id": operator_id,
        "operator_name": operator_name,
        "transport_required": transport_required,
        "pickup_choice": pickup_choice,
        "pickup_landmark": pickup_landmark or ("RuralCare Self-Service Centre" if pickup_choice == "RURALCARE_CENTRE" else f"{village} Home"),
        "status": initial_status,
        "created_at": now_utc,
        "updated_at": now_utc,
        "service_verified": False,
        "service_verification_timestamp": None,
        "appointment_confirmed": False,
        "appointment_slot": None,
        "clinical_urgency": None,  # Strictly determined by doctor; zero automated AI diagnosis
        "doctor_assessment": None,
        "transport_request_id": None,
        "transport_status": "NONE" if not transport_required else "PENDING",
        "driver_id": None,
        "driver_name": None,
        "driver_phone": None,
        "vehicle_reg": None,
        "pickup_confirmed": False,
        "destination_confirmed": False,
        "disclosed_fare": None,
        "fare_currency": "INR",
        "patient_fare_accepted": False,
        "patient_readiness_confirmed": False,
        "linked_original_journey_id": payload.get("linked_original_journey_id"),
        "is_alternative_transfer": bool(payload.get("linked_original_journey_id"))
    }

    # If transport required, spawn transport request
    if transport_required:
        tr_id = f"TR-{random.randint(1000, 9999)}"
        transport_data = {
            "id": tr_id,
            "journey_id": journey_id,
            "patient_name": patient_name,
            "patient_phone": contact_number,
            "pickup_village": village,
            "pickup_landmark": pickup_landmark or f"{village} Centre",
            "destination_facility_id": facility_id,
            "destination_facility_name": facility_name,
            "scheduled_date": preferred_date,
            "driver_id": None,
            "driver_name": None,
            "status": "REQUESTED",
            "pickup_confirmed": False,
            "destination_confirmed": False,
            "fare_amount": None,
            "patient_fare_accepted": False,
            "created_at": now_utc,
            "updated_at": now_utc,
            "is_demo_record": True
        }
        db.collection("transport_requests").document(tr_id).set(transport_data)
        journey_data["transport_request_id"] = tr_id

    # Check if selected facility is currently verified
    if facility_id:
        f_doc = db.collection("facilities").document(facility_id).get()
        if f_doc.exists:
            f_data = f_doc.to_dict()
            if f_data.get("verification_status") == "VERIFIED" and service_id in f_data.get("available_services", []):
                journey_data["service_verified"] = True
                journey_data["service_verification_timestamp"] = f_data.get("last_verified_at")
                journey_data["status"] = "APPOINTMENT_PENDING"

    db.collection("care_journeys").document(journey_id).set(journey_data)

    # Log creation event
    actor_desc = f"Operator {operator_name} ({operator_id})" if operator_id else f"Patient ({registration_channel})"
    log_journey_event(
        db,
        journey_id,
        "JOURNEY_CREATED",
        actor_desc,
        f"Care Journey registered via {registration_method}. Required service: {service_name}. Transport needed: {transport_required}"
    )

    # Evaluate initial readiness check
    eval_result = evaluate_readiness(journey_id)

    # Return refreshed journey
    final_doc = db.collection("care_journeys").document(journey_id).get()
    return jsonify({
        "success": True,
        "message": "Care Journey created successfully",
        "journey": final_doc.to_dict(),
        "readiness": eval_result.get("readiness")
    }), 201

@journeys_bp.route("/<journey_id>", methods=["GET"])
def get_journey(journey_id):
    """
    Get Care Journey details with verification check.
    Prevents unauthorized strangers from accessing patient private details by ID guessing.
    """
    db = get_db()
    j_doc = db.collection("care_journeys").document(journey_id).get()
    if not j_doc.exists:
        return jsonify({"success": False, "error": "Journey not found"}), 404

    # Run readiness re-evaluation
    eval_res = evaluate_readiness(journey_id)
    j_doc = db.collection("care_journeys").document(journey_id).get()
    j_data = j_doc.to_dict()

    user = get_authenticated_user()
    is_staff_or_admin = user and user.get("role") in ("facility_staff", "kiosk_operator", "admin")

    # Patient verification: check if matching phone provided in param/header or session
    verify_phone = request.args.get("verify_phone") or request.headers.get("X-Verify-Phone")
    registered_phone = j_data.get("contact_number", "")

    phone_verified = False
    if verify_phone:
        v_clean = str(verify_phone).strip()
        if v_clean == registered_phone or (len(v_clean) == 4 and registered_phone.endswith(v_clean)):
            phone_verified = True

    if not is_staff_or_admin and not phone_verified:
        # Return restricted masked preview to prevent unauthorized snooping
        return jsonify({
            "success": True,
            "is_restricted_preview": True,
            "notice": "Verification required: Provide the registered 10-digit mobile number or last 4 digits to unlock full patient details.",
            "journey": mask_sensitive_journey(j_data),
            "readiness": eval_res.get("readiness")
        }), 200

    # Fetch events timeline for verified viewer
    events_docs = db.collection("journey_events").where("journey_id", "==", journey_id)
    events = [e.to_dict() for e in events_docs]
    events.sort(key=lambda x: x.get("timestamp", ""), reverse=False)

    return jsonify({
        "success": True,
        "is_restricted_preview": False,
        "journey": j_data,
        "readiness": eval_res.get("readiness"),
        "events": events
    })

@journeys_bp.route("/search", methods=["GET"])
def search_journeys():
    """
    Search journeys. Village search is restricted to staff/operators/admins.
    Patients can only lookup using their exact registered 10-digit mobile number.
    """
    db = get_db()
    contact = request.args.get("contact", "").strip()
    village = request.args.get("village", "").strip()
    user = get_authenticated_user()
    is_staff = user and user.get("role") in ("facility_staff", "kiosk_operator", "admin")

    if village and not is_staff:
        return jsonify({
            "success": False,
            "error": "Village directory lookup requires authenticated staff or kiosk operator privileges."
        }), 403

    if contact and not re.match(r"^\d{10}$", contact):
        return jsonify({"success": False, "error": "Search requires an exact 10-digit mobile number."}), 400

    all_docs = db.collection("care_journeys").stream()
    matched = []
    for doc in all_docs:
        d = doc.to_dict()
        if contact and d.get("contact_number") == contact:
            matched.append(d)
        elif village and is_staff and d.get("village", "").lower() == village.lower():
            matched.append(d)

    return jsonify({"success": True, "count": len(matched), "journeys": matched})

@journeys_bp.route("/<journey_id>/patient-readiness", methods=["POST"])
def confirm_patient_readiness(journey_id):
    """Patient or Kiosk confirms patient is ready for the journey."""
    db = get_db()
    j_doc = db.collection("care_journeys").document(journey_id).get()
    if not j_doc.exists:
        return jsonify({"success": False, "error": "Journey not found"}), 404

    j_data = j_doc.to_dict()
    user = get_authenticated_user()
    is_staff = user and user.get("role") in ("kiosk_operator", "admin")

    verify_phone = request.args.get("verify_phone") or request.headers.get("X-Verify-Phone")
    if not is_staff and verify_phone:
        if verify_phone != j_data.get("contact_number") and not j_data.get("contact_number", "").endswith(verify_phone):
            return jsonify({"success": False, "error": "Verification phone does not match journey record."}), 403

    payload = request.get_json() or {}
    is_ready = bool(payload.get("patient_readiness_confirmed", True))

    now_utc = datetime.now(timezone.utc).isoformat()
    db.collection("care_journeys").document(journey_id).update({
        "patient_readiness_confirmed": is_ready,
        "updated_at": now_utc
    })

    log_journey_event(
        db,
        journey_id,
        "PATIENT_READINESS_UPDATE",
        (user or {}).get("name", "Patient / Kiosk"),
        f"Patient departure readiness set to {is_ready}"
    )

    eval_res = evaluate_readiness(journey_id)
    return jsonify({
        "success": True,
        "message": "Patient readiness updated",
        "readiness": eval_res.get("readiness")
    })

@journeys_bp.route("/<journey_id>/patient-accept-fare", methods=["POST"])
def patient_accept_fare(journey_id):
    """Patient accepts the driver's disclosed fare."""
    db = get_db()
    j_doc = db.collection("care_journeys").document(journey_id).get()
    if not j_doc.exists:
        return jsonify({"success": False, "error": "Journey not found"}), 404

    payload = request.get_json() or {}
    accepted = bool(payload.get("accepted", True))
    now_utc = datetime.now(timezone.utc).isoformat()

    updates = {
        "patient_fare_accepted": accepted,
        "updated_at": now_utc
    }
    db.collection("care_journeys").document(journey_id).update(updates)

    tr_id = j_doc.to_dict().get("transport_request_id")
    if tr_id:
        db.collection("transport_requests").document(tr_id).update({
            "patient_fare_accepted": accepted,
            "updated_at": now_utc
        })

    log_journey_event(
        db,
        journey_id,
        "FARE_ACCEPTANCE",
        "Patient",
        f"Patient {'accepted' if accepted else 'declined'} disclosed transport fare."
    )

    eval_res = evaluate_readiness(journey_id)
    return jsonify({
        "success": True,
        "message": "Fare acceptance recorded",
        "readiness": eval_res.get("readiness")
    })

@journeys_bp.route("/<journey_id>/appointment-action", methods=["POST"])
@require_role("facility_staff", "admin")
def facility_appointment_action(journey_id):
    """Facility staff accepts or rejects appointment."""
    db = get_db()
    j_doc = db.collection("care_journeys").document(journey_id).get()
    if not j_doc.exists:
        return jsonify({"success": False, "error": "Journey not found"}), 404

    j_data = j_doc.to_dict()
    user = get_authenticated_user() or {}
    if user.get("role") == "facility_staff" and user.get("facility_id") != j_data.get("facility_id"):
        return jsonify({"success": False, "error": "Staff may only accept appointments for their own hospital."}), 403

    payload = request.get_json() or {}
    action = payload.get("action", "").lower().strip()
    if action not in ("accept", "reject"):
        return jsonify({"success": False, "error": "Action must be 'accept' or 'reject'."}), 400

    staff_name = sanitize_text(payload.get("staff_name") or user.get("name", "Attending Medical Officer"))
    slot = sanitize_text(payload.get("appointment_slot", "10:30 AM Outpatient"))
    notes = sanitize_text(payload.get("notes", ""))

    now_utc = datetime.now(timezone.utc).isoformat()

    if action == "accept":
        db.collection("care_journeys").document(journey_id).update({
            "appointment_confirmed": True,
            "appointment_slot": slot,
            "service_verified": True,
            "service_verification_timestamp": now_utc,
            "updated_at": now_utc
        })
        log_journey_event(
            db,
            journey_id,
            "APPOINTMENT_ACCEPTED",
            staff_name,
            f"Appointment confirmed for slot: {slot}. {notes}"
        )
    elif action == "reject":
        db.collection("care_journeys").document(journey_id).update({
            "appointment_confirmed": False,
            "appointment_slot": None,
            "status": "CANCELLED",
            "cancellation_reason": notes or "Facility doctor unavailable on requested date.",
            "updated_at": now_utc
        })
        log_journey_event(
            db,
            journey_id,
            "APPOINTMENT_REJECTED",
            staff_name,
            f"Appointment rejected: {notes or 'Specialist unavailable'}"
        )

    eval_res = evaluate_readiness(journey_id)
    refreshed = db.collection("care_journeys").document(journey_id).get().to_dict()
    return jsonify({
        "success": True,
        "journey": refreshed,
        "readiness": eval_res.get("readiness")
    })

@journeys_bp.route("/<journey_id>/patient-confirm-slot", methods=["POST"])
def patient_confirm_slot(journey_id):
    """
    Patient confirms the appointment slot offered/assigned by the doctor.
    Advances journey state and triggers Departure Readiness Check.
    """
    db = get_db()
    j_doc = db.collection("care_journeys").document(journey_id).get()
    if not j_doc.exists:
        return jsonify({"success": False, "error": "Journey not found"}), 404

    j_data = j_doc.to_dict()
    slot = j_data.get("appointment_slot")
    if not slot:
        return jsonify({"success": False, "error": "No appointment slot has been offered by facility yet."}), 400

    now_utc = datetime.now(timezone.utc).isoformat()
    db.collection("care_journeys").document(journey_id).update({
        "appointment_confirmed": True,
        "patient_slot_confirmed": True,
        "updated_at": now_utc
    })

    log_journey_event(
        db,
        journey_id,
        "APPOINTMENT_SLOT_CONFIRMED",
        j_data.get("patient_name", "Patient"),
        f"Patient confirmed acceptance of appointment slot: {slot}."
    )

    eval_res = evaluate_readiness(journey_id)
    refreshed = db.collection("care_journeys").document(journey_id).get().to_dict()
    return jsonify({
        "success": True,
        "message": f"Appointment slot {slot} confirmed by patient.",
        "journey": refreshed,
        "readiness": eval_res.get("readiness")
    })

@journeys_bp.route("/<journey_id>/patient-reject-slot", methods=["POST"])
def patient_reject_slot(journey_id):
    """
    Patient rejects the appointment slot offered by the doctor/facility.
    Resets appointment_confirmed, updates status to SLOT_REJECTED,
    and logs event to notify staff to offer alternative time.
    """
    db = get_db()
    j_doc = db.collection("care_journeys").document(journey_id).get()
    if not j_doc.exists:
        return jsonify({"success": False, "error": "Journey not found"}), 404

    j_data = j_doc.to_dict()
    payload = request.get_json() or {}
    reason = sanitize_text(payload.get("reason", "Patient cannot attend at offered slot."))

    rejected_slot = j_data.get("appointment_slot", "Offered Slot")
    now_utc = datetime.now(timezone.utc).isoformat()

    db.collection("care_journeys").document(journey_id).update({
        "appointment_confirmed": False,
        "patient_slot_confirmed": False,
        "rejected_slot": rejected_slot,
        "appointment_slot": None,
        "status": "SLOT_REJECTED",
        "updated_at": now_utc
    })

    log_journey_event(
        db,
        journey_id,
        "APPOINTMENT_SLOT_REJECTED",
        j_data.get("patient_name", "Patient"),
        f"Patient rejected slot {rejected_slot}. Reason: {reason}. Alternative slot required."
    )

    eval_res = evaluate_readiness(journey_id)
    refreshed = db.collection("care_journeys").document(journey_id).get().to_dict()
    return jsonify({
        "success": True,
        "message": f"Appointment slot {rejected_slot} rejected by patient. Notification sent for alternative scheduling.",
        "journey": refreshed,
        "readiness": eval_res.get("readiness")
    })

@journeys_bp.route("/<journey_id>/cancel", methods=["POST"])
def cancel_journey(journey_id):
    db = get_db()
    j_doc = db.collection("care_journeys").document(journey_id).get()
    if not j_doc.exists:
        return jsonify({"success": False, "error": "Journey not found"}), 404

    payload = request.get_json() or {}
    reason = sanitize_text(payload.get("reason", "Cancelled by user"))
    actor = sanitize_text(payload.get("actor", "Patient"))

    now_utc = datetime.now(timezone.utc).isoformat()
    db.collection("care_journeys").document(journey_id).update({
        "status": "CANCELLED",
        "cancellation_reason": reason,
        "updated_at": now_utc
    })

    log_journey_event(db, journey_id, "JOURNEY_CANCELLED", actor, f"Journey cancelled: {reason}")
    return jsonify({"success": True, "message": "Journey cancelled successfully"})

@journeys_bp.route("/<journey_id>/rebook-alternative", methods=["POST"])
def rebook_alternative_facility(journey_id):
    """
    Rebook appointment to a verified alternative facility after patient review and confirmation.
    Preserves original journey history and links the new request.
    Does NOT mark appointment accepted automatically.
    """
    db = get_db()
    orig_doc = db.collection("care_journeys").document(journey_id).get()
    if not orig_doc.exists:
        return jsonify({"success": False, "error": "Original journey not found"}), 404

    orig = orig_doc.to_dict()
    payload = request.get_json() or {}

    alt_facility_id = payload.get("alternative_facility_id", "").strip()
    if not alt_facility_id:
        return jsonify({"success": False, "error": "alternative_facility_id is required"}), 400

    f_doc = db.collection("facilities").document(alt_facility_id).get()
    if not f_doc.exists:
        return jsonify({"success": False, "error": "Selected alternative facility not found"}), 404

    alt_facility = f_doc.to_dict()
    service_id = orig.get("preferred_service_id")
    if service_id and service_id not in alt_facility.get("available_services", []):
        return jsonify({"success": False, "error": "Alternative facility does not offer the requested service."}), 400

    now_utc = datetime.now(timezone.utc).isoformat()
    new_journey_id = generate_journey_id(db)

    preferred_date = payload.get("preferred_date") or orig.get("preferred_date")
    transport_required = bool(payload.get("transport_required", orig.get("transport_required", False)))
    pickup_landmark = payload.get("pickup_landmark") or orig.get("pickup_landmark", "")

    # Check alternative facility verification
    is_fac_verified = (alt_facility.get("verification_status") == "VERIFIED")
    valid_until_str = alt_facility.get("verification_valid_until")
    if valid_until_str:
        try:
            vu = datetime.fromisoformat(valid_until_str.replace("Z", "+00:00"))
            if vu <= datetime.now(timezone.utc):
                is_fac_verified = False
        except Exception:
            is_fac_verified = False

    initial_status = "APPOINTMENT_PENDING" if is_fac_verified else "FACILITY_VERIFICATION_PENDING"

    new_journey_data = {
        "id": new_journey_id,
        "patient_name": orig.get("patient_name"),
        "age": orig.get("age"),
        "contact_number": orig.get("contact_number"),
        "village": orig.get("village"),
        "preferred_service_id": service_id,
        "preferred_service_name": orig.get("preferred_service_name"),
        "preferred_date": preferred_date,
        "facility_id": alt_facility_id,
        "facility_name": alt_facility.get("name"),
        "registration_method": orig.get("registration_method", "SELF_SERVICE"),
        "registration_channel": orig.get("registration_channel", "TOUCH_BASED"),
        "operator_id": orig.get("operator_id"),
        "operator_name": orig.get("operator_name"),
        "transport_required": transport_required,
        "pickup_landmark": pickup_landmark,
        "status": initial_status,
        "created_at": now_utc,
        "updated_at": now_utc,
        "service_verified": is_fac_verified,
        "service_verification_timestamp": alt_facility.get("last_verified_at"),
        "appointment_confirmed": False,  # Crucial rule: DO NOT mark accepted automatically
        "appointment_slot": None,
        "transport_request_id": None,
        "transport_status": "NONE" if not transport_required else "PENDING",
        "driver_id": None,
        "driver_name": None,
        "driver_phone": None,
        "vehicle_reg": None,
        "pickup_confirmed": False,
        "destination_confirmed": False,
        "disclosed_fare": None,
        "fare_currency": "INR",
        "patient_fare_accepted": False,
        "patient_readiness_confirmed": False,
        "linked_original_journey_id": journey_id,
        "is_alternative_transfer": True
    }

    if transport_required:
        tr_id = f"TR-{random.randint(1000, 9999)}"
        transport_data = {
            "id": tr_id,
            "journey_id": new_journey_id,
            "patient_name": orig.get("patient_name"),
            "patient_phone": orig.get("contact_number"),
            "pickup_village": orig.get("village"),
            "pickup_landmark": pickup_landmark or f"{orig.get('village')} Centre",
            "destination_facility_id": alt_facility_id,
            "destination_facility_name": alt_facility.get("name"),
            "scheduled_date": preferred_date,
            "driver_id": None,
            "driver_name": None,
            "status": "REQUESTED",
            "pickup_confirmed": False,
            "destination_confirmed": False,
            "fare_amount": None,
            "patient_fare_accepted": False,
            "created_at": now_utc,
            "updated_at": now_utc,
            "is_demo_record": True
        }
        db.collection("transport_requests").document(tr_id).set(transport_data)
        new_journey_data["transport_request_id"] = tr_id

    db.collection("care_journeys").document(new_journey_id).set(new_journey_data)

    # Preserve and update original journey
    db.collection("care_journeys").document(journey_id).update({
        "rebooked_to_journey_id": new_journey_id,
        "alternative_offered_facility_id": alt_facility_id,
        "updated_at": now_utc
    })

    # Log events on both records
    log_journey_event(
        db,
        journey_id,
        "ALTERNATIVE_FACILITY_SELECTED",
        "Patient",
        f"Patient selected verified alternative: {alt_facility.get('name')}. New request created: {new_journey_id}."
    )
    log_journey_event(
        db,
        new_journey_id,
        "JOURNEY_CREATED_FROM_ALTERNATIVE",
        "Patient",
        f"Care Journey transferred from previous request {journey_id} due to facility change. Awaiting appointment review by {alt_facility.get('name')}."
    )

    eval_res = evaluate_readiness(new_journey_id)
    refreshed_new = db.collection("care_journeys").document(new_journey_id).get().to_dict()

    return jsonify({
        "success": True,
        "message": f"Successfully created new appointment request at {alt_facility.get('name')}.",
        "new_journey": refreshed_new,
        "original_journey_id": journey_id,
        "readiness": eval_res.get("readiness")
    }), 201
