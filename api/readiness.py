from datetime import datetime, timezone
import uuid
from flask import Blueprint, jsonify, request
from database import get_db

readiness_bp = Blueprint("readiness", __name__, url_prefix="/api/readiness")

def log_journey_event(db, journey_id, event_type, actor, description):
    ev_id = f"EV-{uuid.uuid4().hex[:8]}"
    event_data = {
        "id": ev_id,
        "journey_id": journey_id,
        "event_type": event_type,
        "actor": actor,
        "description": description,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    db.collection("journey_events").document(ev_id).set(event_data)
    return event_data

def evaluate_readiness(journey_id):
    """
    Core Departure Readiness Engine (Backend-only verification).
    Evaluates:
      1. Requested healthcare service verified
      2. Verification still valid (unexpired)
      3. Appointment accepted (if required)
      4. Transport accepted (if required)
      5. Pickup and destination confirmed (if transport required)
      6. Fare disclosed and accepted (if transport required)
      7. Patient readiness confirmed
    """
    db = get_db()
    j_doc = db.collection("care_journeys").document(journey_id).get()
    if not j_doc.exists:
        return {"error": "Journey not found", "success": False}

    j = j_doc.to_dict()
    current_status = j.get("status", "REQUESTED")
    now_utc = datetime.now(timezone.utc)

    # 1. Facility and service check
    facility_id = j.get("facility_id")
    facility_doc = db.collection("facilities").document(facility_id).get() if facility_id else None
    facility = facility_doc.to_dict() if facility_doc and facility_doc.exists else {}

    service_id = j.get("preferred_service_id")
    available_services = facility.get("available_services", [])
    service_is_available = (service_id in available_services) if service_id else True

    facility_status = facility.get("verification_status", "UNVERIFIED")
    service_verified = (facility_status == "VERIFIED") and service_is_available and j.get("service_verified", False)

    # 2. Verification validity expiration check
    verification_unexpired = False
    valid_until_str = facility.get("verification_valid_until")
    if valid_until_str:
        try:
            valid_until = datetime.fromisoformat(valid_until_str.replace("Z", "+00:00"))
            if valid_until > now_utc:
                verification_unexpired = True
            else:
                verification_unexpired = False
        except Exception:
            verification_unexpired = False
    elif facility_status == "VERIFIED":
        verification_unexpired = True

    # 3. Appointment check
    appointment_accepted = bool(j.get("appointment_confirmed", False))

    # 4, 5, 6. Transport checks
    transport_required = bool(j.get("transport_required", False))
    if transport_required:
        transport_status = j.get("transport_status")
        transport_accepted = (transport_status in ("ACCEPTED", "IN_TRANSIT", "COMPLETED", "PICKED_UP"))
        pickup_destination_confirmed = bool(j.get("pickup_confirmed") and j.get("destination_confirmed"))
        disclosed_fare = j.get("disclosed_fare")
        fare_disclosed_and_accepted = bool(disclosed_fare is not None and disclosed_fare >= 0 and j.get("patient_fare_accepted"))
    else:
        # Not required - trivially passed
        transport_accepted = True
        pickup_destination_confirmed = True
        fare_disclosed_and_accepted = True

    # 7. Patient readiness check
    patient_readiness_confirmed = bool(j.get("patient_readiness_confirmed", False))

    # Collective evaluation
    checks = {
        "service_verified": service_verified,
        "verification_unexpired": verification_unexpired,
        "appointment_accepted": appointment_accepted,
        "transport_accepted": transport_accepted,
        "pickup_destination_confirmed": pickup_destination_confirmed,
        "fare_disclosed_and_accepted": fare_disclosed_and_accepted,
        "patient_readiness_confirmed": patient_readiness_confirmed
    }

    all_passed = all(checks.values())
    
    # Generate human readable explanation
    missing = []
    if not service_verified:
        missing.append("Facility service verification required")
    if not verification_unexpired:
        missing.append("Facility verification validity expired")
    if not appointment_accepted:
        missing.append("Facility appointment acceptance pending")
    if transport_required and not transport_accepted:
        missing.append("Driver transport acceptance pending")
    if transport_required and not pickup_destination_confirmed:
        missing.append("Pickup and drop location confirmation pending")
    if transport_required and not fare_disclosed_and_accepted:
        missing.append("Transport fare disclosure & patient acceptance pending")
    if not patient_readiness_confirmed:
        missing.append("Final patient readiness check pending")

    if all_passed:
        explanation = "All 7 verification checkpoints validated. Journey is officially verified for safe departure."
        if current_status in ("COMPLETED", "IN_TRANSIT"):
            new_status = current_status
        else:
            new_status = "COORDINATION_CONFIRMED"
    else:
        explanation = "Departure unverified: " + "; ".join(missing)
        if current_status in ("CANCELLED", "COMPLETED", "TRANSPORT_FAILED", "IN_TRANSIT", "SLOT_REJECTED"):
            new_status = current_status
        elif current_status in ("COORDINATION_CONFIRMED", "RECHECK_REQUIRED"):
            new_status = "RECHECK_REQUIRED"
        elif not service_verified or not verification_unexpired:
            new_status = "FACILITY_VERIFICATION_PENDING"
        elif not appointment_accepted:
            new_status = "APPOINTMENT_PENDING"
        elif transport_required and (not transport_accepted or not fare_disclosed_and_accepted):
            new_status = "TRANSPORT_PENDING"
        else:
            new_status = current_status

    readiness_result = {
        "is_ready_for_departure": all_passed,
        "evaluated_at": now_utc.isoformat(),
        "checks": checks,
        "explanation": explanation,
        "missing_requirements": missing,
        "disclaimer": "Coordination confirmation verifies scheduled readiness with participating facilities and drivers. It does not guarantee doctor attendance, emergency response, waiting time or medical fitness to travel."
    }

    # Only backend assigns COORDINATION_CONFIRMED or updates status
    status_changed = (new_status != current_status)
    updates = {
        "departure_readiness_check": readiness_result,
        "status": new_status,
        "updated_at": now_utc.isoformat()
    }
    db.collection("care_journeys").document(journey_id).update(updates)

    if status_changed:
        log_journey_event(
            db,
            journey_id,
            "STATUS_TRANSITION",
            "RuralCare Readiness Engine",
            f"Status updated from {current_status} to {new_status} based on Departure Readiness Check."
        )

    return {
        "success": True,
        "journey_id": journey_id,
        "status": new_status,
        "readiness": readiness_result
    }

@readiness_bp.route("/check/<journey_id>", methods=["GET", "POST"])
def check_readiness_endpoint(journey_id):
    """API endpoint to run or fetch the departure readiness evaluation."""
    result = evaluate_readiness(journey_id)
    if not result.get("success"):
        return jsonify(result), 404
    return jsonify(result)
