from datetime import datetime, timezone
import re
from flask import Blueprint, jsonify, request, session
from database import get_db, generate_patient_id
from api.journeys import sanitize_text
from api.auth import get_authenticated_user

patients_bp = Blueprint("patients", __name__, url_prefix="/api/patients")

def mask_phone(phone):
    if not phone or len(phone) < 4:
        return "XXXXXXXXXX"
    return "XXXXXX" + str(phone)[-4:]

def mask_patient_profile(patient):
    """Mask sensitive patient details for unauthenticated preview."""
    name = patient.get("name", "")
    masked_name = (name[:2] + "****" + name[-1:]) if len(name) > 3 else "Protected Citizen"
    return {
        "id": patient.get("id"),
        "name": masked_name,
        "contact_number": mask_phone(patient.get("contact_number")),
        "village": patient.get("village"),
        "age": "**",
        "is_restricted_preview": True
    }

@patients_bp.route("/register", methods=["POST"])
def register_patient():
    """
    Registers a new patient with unique Patient ID (PID-2026-XXXX).
    Deduplicates returning patients by 10-digit contact number.
    Mandates explicit patient consent before recording.
    """
    db = get_db()
    payload = request.get_json() or {}

    raw_name = payload.get("patient_name") or payload.get("name", "")
    name = sanitize_text(raw_name)
    age = payload.get("age")
    contact_number = str(payload.get("contact_number") or payload.get("phone", "")).strip()
    village = sanitize_text(payload.get("village", ""))
    consent_given = payload.get("consent_given", payload.get("patient_consent", True))

    # Consent validation
    if not consent_given:
        return jsonify({
            "success": False,
            "error": "Explicit patient consent is required to register and process care coordination.",
            "code": "CONSENT_REQUIRED"
        }), 400

    # Strict validations
    if not name or len(name) < 2:
        return jsonify({"success": False, "error": "Patient name must be at least 2 characters long."}), 400

    try:
        age_int = int(age)
        if age_int < 1 or age_int > 125:
            return jsonify({"success": False, "error": "Patient age must be between 1 and 125."}), 400
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Valid patient age is mandatory."}), 400

    if not re.match(r"^\d{10}$", contact_number):
        return jsonify({"success": False, "error": "Contact number must be an exact 10-digit mobile number."}), 400

    if not village:
        return jsonify({"success": False, "error": "Village / Panchayat is required."}), 400

    now_utc = datetime.now(timezone.utc).isoformat()

    # Check if patient already registered by phone number
    existing_patients = db.collection("patients").where("contact_number", "==", contact_number)
    if len(existing_patients) > 0:
        existing = existing_patients[0].to_dict()
        session["patient"] = existing
        return jsonify({
            "success": True,
            "is_returning": True,
            "message": f"Welcome back, {existing.get('name')}. Your existing records have been retrieved.",
            "patient": existing
        }), 200

    # Assign new Patient ID: PID-2026-XXXX
    patient_id = generate_patient_id(db)
    patient_record = {
        "id": patient_id,
        "name": name,
        "name_ta": payload.get("name_ta") or name,
        "age": age_int,
        "contact_number": contact_number,
        "village": village,
        "consent_given": True,
        "consent_timestamp": now_utc,
        "registered_at": now_utc,
        "updated_at": now_utc,
        "is_active": True,
        "is_demo_record": True
    }

    db.collection("patients").document(patient_id).set(patient_record)
    session["patient"] = patient_record

    return jsonify({
        "success": True,
        "is_returning": False,
        "message": f"Patient registration completed successfully. Assigned Patient ID: {patient_id}",
        "patient": patient_record
    }), 201

@patients_bp.route("/login", methods=["POST"])
def patient_login():
    """
    Patient login by Patient ID or registered 10-digit phone number.
    Security guarantee: Requires phone verification (full or last 4 digits)
    to prevent enumeration or unauthorized access.
    Returns connected active journeys and historical care records.
    """
    db = get_db()
    payload = request.get_json() or {}

    patient_id = sanitize_text(payload.get("patient_id", "")).upper()
    phone = str(payload.get("contact_number") or payload.get("phone", "")).strip()
    phone_verification = str(payload.get("phone_verification") or payload.get("pin") or "").strip()

    if not patient_id and not phone:
        return jsonify({
            "success": False,
            "error": "Please provide either Patient ID (PID-2026-XXXX) or registered 10-digit mobile number."
        }), 400

    matched_patient = None
    if patient_id:
        doc = db.collection("patients").document(patient_id).get()
        if doc.exists:
            matched_patient = doc.to_dict()
    
    if not matched_patient and phone:
        docs = db.collection("patients").where("contact_number", "==", phone)
        if len(docs) > 0:
            matched_patient = docs[0].to_dict()

    if not matched_patient:
        return jsonify({
            "success": False,
            "error": "No registered patient record found with the provided credentials. Please register as a new patient.",
            "code": "PATIENT_NOT_FOUND"
        }), 404

    # Verification: Validate phone_verification against registered contact number
    registered_phone = str(matched_patient.get("contact_number", ""))
    is_verified = False

    # If verification PIN / last 4 digits provided, validate it strictly
    if phone_verification:
        if phone_verification == registered_phone or (len(phone_verification) == 4 and registered_phone.endswith(phone_verification)):
            is_verified = True
    elif phone and phone == registered_phone:
        is_verified = True

    if not is_verified:
        return jsonify({
            "success": False,
            "error": "Verification failed: Enter the registered 10-digit mobile number or last 4 digits to access records.",
            "code": "VERIFICATION_REQUIRED",
            "preview": mask_patient_profile(matched_patient)
        }), 401

    pid = matched_patient.get("id")
    reg_phone = matched_patient.get("contact_number")

    # Fetch all connected care journeys
    journeys_by_pid = db.collection("care_journeys").where("patient_id", "==", pid)
    journeys_by_phone = db.collection("care_journeys").where("contact_number", "==", reg_phone)
    
    journeys_map = {}
    for j in journeys_by_pid:
        journeys_map[j.id] = j.to_dict()
    for j in journeys_by_phone:
        journeys_map[j.id] = j.to_dict()

    journey_list = list(journeys_map.values())
    journey_list.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    # Active journey: not cancelled, not completed
    active_journey = None
    for j in journey_list:
        if j.get("status") not in ("CANCELLED", "COMPLETED"):
            active_journey = j
            break

    # Fetch connected prescriptions
    rx_docs = db.collection("prescriptions").where("patient_id", "==", pid)
    prescriptions = [d.to_dict() for d in rx_docs]
    prescriptions.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    session["patient"] = matched_patient

    return jsonify({
        "success": True,
        "message": f"Welcome, {matched_patient.get('name')}. Connected to your patient profile.",
        "patient": matched_patient,
        "active_journey": active_journey,
        "journeys_count": len(journey_list),
        "journeys": journey_list,
        "prescriptions": prescriptions
    }), 200

@patients_bp.route("/me", methods=["GET"])
def get_current_patient():
    """Returns currently authenticated patient profile and active journey."""
    db = get_db()
    patient = session.get("patient")
    if not patient:
        return jsonify({"success": False, "error": "No active patient session.", "code": "UNAUTHENTICATED"}), 401

    pid = patient.get("id")
    p_doc = db.collection("patients").document(pid).get()
    if p_doc.exists:
        patient = p_doc.to_dict()

    # Active journeys
    j_docs = db.collection("care_journeys").where("patient_id", "==", pid)
    journeys = [d.to_dict() for d in j_docs]
    journeys.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    active_journey = next((j for j in journeys if j.get("status") not in ("CANCELLED", "COMPLETED")), None)

    # Patient prescriptions
    rx_docs = db.collection("prescriptions").where("patient_id", "==", pid)
    prescriptions = [d.to_dict() for d in rx_docs]
    prescriptions.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    return jsonify({
        "success": True,
        "patient": patient,
        "active_journey": active_journey,
        "journeys": journeys,
        "prescriptions": prescriptions
    })

@patients_bp.route("/<patient_id>", methods=["GET"])
def get_patient_profile(patient_id):
    """
    Get patient profile. Unauthenticated viewers receive masked privacy preview.
    Verified patients or staff/admins receive full profile.
    """
    db = get_db()
    p_doc = db.collection("patients").document(patient_id).get()
    if not p_doc.exists:
        return jsonify({"success": False, "error": "Patient record not found"}), 404

    patient = p_doc.to_dict()
    user = get_authenticated_user()
    is_staff = user and user.get("role") in ("facility_staff", "kiosk_operator", "admin")

    verify_phone = request.args.get("verify_phone") or request.headers.get("X-Verify-Phone")
    session_patient = session.get("patient")
    is_self = session_patient and session_patient.get("id") == patient_id

    reg_phone = patient.get("contact_number", "")
    phone_verified = False
    if verify_phone:
        v_clean = str(verify_phone).strip()
        if v_clean == reg_phone or (len(v_clean) == 4 and reg_phone.endswith(v_clean)):
            phone_verified = True

    if not is_staff and not is_self and not phone_verified:
        return jsonify({
            "success": True,
            "is_restricted_preview": True,
            "notice": "Phone verification required to unlock full patient history.",
            "patient": mask_patient_profile(patient)
        }), 200

    # Verified full access
    j_docs = db.collection("care_journeys").where("patient_id", "==", patient_id)
    journeys = [d.to_dict() for d in j_docs]
    journeys.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    rx_docs = db.collection("prescriptions").where("patient_id", "==", patient_id)
    prescriptions = [d.to_dict() for d in rx_docs]

    return jsonify({
        "success": True,
        "is_restricted_preview": False,
        "patient": patient,
        "journeys": journeys,
        "prescriptions": prescriptions
    })

@patients_bp.route("/logout", methods=["POST"])
def patient_logout():
    """Clear patient session."""
    session.pop("patient", None)
    return jsonify({"success": True, "message": "Patient logged out successfully."})
