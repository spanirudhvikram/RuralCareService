from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from database import get_db
from api.readiness import evaluate_readiness, log_journey_event
from api.auth import require_role, get_authenticated_user

transport_bp = Blueprint("transport", __name__, url_prefix="/api/transport")

def mask_patient_phone_for_driver(phone):
    """Protects patient privacy by redacting phone numbers from drivers."""
    if not phone:
        return "Protected Contact"
    digits = str(phone).strip()
    if len(digits) >= 4:
        return f"XXXXXX{digits[-4:]} (via Kiosk Desk)"
    return "Protected Contact"

@transport_bp.route("/requests", methods=["GET"])
def get_transport_requests():
    """
    List transport requests for drivers.
    Privacy Guarantee: Medical diagnosis and service details are withheld.
    Patient phone number is masked to protect citizen privacy.
    """
    db = get_db()
    status_filter = request.args.get("status")
    village_filter = request.args.get("village")
    driver_id = request.args.get("driver_id")

    docs = db.collection("transport_requests").stream()
    results = []

    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id
        
        if status_filter and d.get("status") != status_filter:
            continue
        if village_filter and d.get("pickup_village", "").lower() != village_filter.lower():
            continue
        if driver_id and d.get("driver_id") and d.get("driver_id") != driver_id:
            continue

        raw_phone = d.get("patient_phone", "")
        masked_phone = mask_patient_phone_for_driver(raw_phone)

        driver_safe_request = {
            "id": d.get("id"),
            "journey_id": d.get("journey_id"),
            "patient_name": d.get("patient_name"),
            "patient_phone": masked_phone,
            "pickup_village": d.get("pickup_village"),
            "pickup_landmark": d.get("pickup_landmark"),
            "destination_facility_name": d.get("destination_facility_name"),
            "scheduled_date": d.get("scheduled_date"),
            "requested_pickup_time": d.get("requested_pickup_time") or d.get("pickup_time") or "09:00 AM",
            "pickup_time": d.get("requested_pickup_time") or d.get("pickup_time") or "09:00 AM",
            "status": d.get("status"),
            "driver_id": d.get("driver_id"),
            "driver_name": d.get("driver_name"),
            "driver_phone": d.get("driver_phone"),
            "vehicle_reg": d.get("vehicle_reg"),
            "pickup_confirmed": d.get("pickup_confirmed", False),
            "destination_confirmed": d.get("destination_confirmed", False),
            "fare_amount": d.get("fare_amount"),
            "patient_fare_accepted": d.get("patient_fare_accepted", False),
            "created_at": d.get("created_at"),
            "cancellation_reason": d.get("cancellation_reason")
        }
        results.append(driver_safe_request)

    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify({"success": True, "count": len(results), "requests": results})

@transport_bp.route("/requests/<request_id>/accept", methods=["POST"])
@require_role("driver", "admin")
def accept_transport(request_id):
    """Driver accepts a transport request and discloses upfront fare."""
    db = get_db()
    t_doc = db.collection("transport_requests").document(request_id).get()
    if not t_doc.exists:
        return jsonify({"success": False, "error": "Transport request not found"}), 404

    user = get_authenticated_user() or {}
    payload = request.get_json() or {}
    driver_id = payload.get("driver_id") or user.get("id", "DRV-01")
    driver_name = payload.get("driver_name") or user.get("name", "Rural Transit Driver")
    driver_phone = payload.get("driver_phone") or user.get("phone", "")
    vehicle_reg = payload.get("vehicle_reg") or user.get("vehicle_reg", "")
    fare_amount = payload.get("fare_amount")

    try:
        fare_val = float(fare_amount)
        if fare_val <= 0 or fare_val > 10000:
            return jsonify({"success": False, "error": "Fare amount must be between ₹1 and ₹10,000."}), 400
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid upfront fare value."}), 400

    now_utc = datetime.now(timezone.utc).isoformat()

    # Update transport record
    t_updates = {
        "status": "ACCEPTED",
        "driver_id": driver_id,
        "driver_name": driver_name,
        "driver_phone": driver_phone,
        "vehicle_reg": vehicle_reg,
        "fare_amount": fare_val,
        "pickup_confirmed": True,
        "destination_confirmed": True,
        "updated_at": now_utc
    }
    db.collection("transport_requests").document(request_id).update(t_updates)

    # Sync back to Care Journey
    journey_id = t_doc.to_dict().get("journey_id")
    if journey_id:
        j_updates = {
            "transport_status": "ACCEPTED",
            "driver_id": driver_id,
            "driver_name": driver_name,
            "driver_phone": driver_phone,
            "vehicle_reg": vehicle_reg,
            "disclosed_fare": fare_val,
            "pickup_confirmed": True,
            "destination_confirmed": True,
            "updated_at": now_utc
        }
        db.collection("care_journeys").document(journey_id).update(j_updates)
        log_journey_event(
            db,
            journey_id,
            "DRIVER_ACCEPTED",
            f"{driver_name} ({vehicle_reg})",
            f"Driver accepted ride. Disclosed fare: ₹{fare_val}. Pickup & destination confirmed."
        )
        evaluate_readiness(journey_id)

    return jsonify({
        "success": True,
        "message": f"Transport request accepted by {driver_name}",
        "fare_amount": fare_val
    })

@transport_bp.route("/requests/<request_id>/confirm-locations", methods=["POST"])
@require_role("driver", "admin")
def confirm_locations(request_id):
    """Driver confirms pickup and destination locations."""
    db = get_db()
    t_doc = db.collection("transport_requests").document(request_id).get()
    if not t_doc.exists:
        return jsonify({"success": False, "error": "Transport request not found"}), 404

    payload = request.get_json() or {}
    pickup_confirmed = bool(payload.get("pickup_confirmed", True))
    dest_confirmed = bool(payload.get("destination_confirmed", True))
    now_utc = datetime.now(timezone.utc).isoformat()

    db.collection("transport_requests").document(request_id).update({
        "pickup_confirmed": pickup_confirmed,
        "destination_confirmed": dest_confirmed,
        "updated_at": now_utc
    })

    journey_id = t_doc.to_dict().get("journey_id")
    if journey_id:
        db.collection("care_journeys").document(journey_id).update({
            "pickup_confirmed": pickup_confirmed,
            "destination_confirmed": dest_confirmed,
            "updated_at": now_utc
        })
        evaluate_readiness(journey_id)

    return jsonify({"success": True, "message": "Pickup and destination confirmed."})

@transport_bp.route("/requests/<request_id>/pickup-location", methods=["POST"])
def select_pickup_location(request_id):
    """
    Patient or Kiosk selects pickup location:
    1. HOME_ADDRESS: Patient's Home Address in village
    2. RURALCARE_CENTRE: RuralCare Self-Service Centre in village
    """
    db = get_db()
    t_doc = db.collection("transport_requests").document(request_id).get()
    if not t_doc.exists:
        return jsonify({"success": False, "error": "Transport request not found"}), 404

    payload = request.get_json() or {}
    pickup_choice = str(payload.get("pickup_choice", "HOME_ADDRESS")).strip().upper()
    custom_landmark = str(payload.get("pickup_landmark", "")).strip()

    t_data = t_doc.to_dict()
    village = t_data.get("pickup_village", "Village")

    if pickup_choice == "RURALCARE_CENTRE":
        landmark = f"RuralCare Self-Service Centre ({village})"
    else:
        pickup_choice = "HOME_ADDRESS"
        landmark = custom_landmark or f"Patient Residence / Doorstep ({village})"

    now_utc = datetime.now(timezone.utc).isoformat()
    db.collection("transport_requests").document(request_id).update({
        "pickup_choice": pickup_choice,
        "pickup_landmark": landmark,
        "updated_at": now_utc
    })

    journey_id = t_data.get("journey_id")
    if journey_id:
        db.collection("care_journeys").document(journey_id).update({
            "pickup_choice": pickup_choice,
            "pickup_landmark": landmark,
            "updated_at": now_utc
        })
        log_journey_event(
            db,
            journey_id,
            "PICKUP_LOCATION_SELECTED",
            "Patient",
            f"Pickup location selected: {landmark} ({'RuralCare Kiosk Centre' if pickup_choice == 'RURALCARE_CENTRE' else 'Home Doorstep'})."
        )
        evaluate_readiness(journey_id)

    return jsonify({
        "success": True,
        "pickup_choice": pickup_choice,
        "pickup_landmark": landmark,
        "message": f"Pickup location set to {landmark}."
    })

@transport_bp.route("/requests/<request_id>/record-fare-acceptance", methods=["POST"])
def record_fare_acceptance(request_id):
    """Records that the patient has formally accepted the disclosed fare."""
    db = get_db()
    t_doc = db.collection("transport_requests").document(request_id).get()
    if not t_doc.exists:
        return jsonify({"success": False, "error": "Transport request not found"}), 404

    payload = request.get_json() or {}
    accepted = bool(payload.get("patient_fare_accepted", True))
    now_utc = datetime.now(timezone.utc).isoformat()

    db.collection("transport_requests").document(request_id).update({
        "patient_fare_accepted": accepted,
        "updated_at": now_utc
    })

    journey_id = t_doc.to_dict().get("journey_id")
    fare_val = t_doc.to_dict().get("fare_amount", 0)
    if journey_id:
        db.collection("care_journeys").document(journey_id).update({
            "patient_fare_accepted": accepted,
            "updated_at": now_utc
        })
        if accepted:
            log_journey_event(
                db,
                journey_id,
                "FARE_CONFIRMED",
                "Patient",
                f"Patient accepted disclosed upfront fare of ₹{fare_val}."
            )
        else:
            log_journey_event(
                db,
                journey_id,
                "FARE_REJECTED",
                "Patient",
                f"Patient rejected quoted fare of ₹{fare_val}. Fare re-negotiation or driver re-assignment required."
            )
        evaluate_readiness(journey_id)

    msg = f"Fare of ₹{fare_val} accepted by patient." if accepted else f"Fare of ₹{fare_val} rejected by patient."
    return jsonify({"success": True, "patient_fare_accepted": accepted, "message": msg})

@transport_bp.route("/requests/<request_id>/status", methods=["POST"])
@require_role("driver", "admin")
def update_transport_status(request_id):
    """Driver updates ongoing transport status (e.g. IN_TRANSIT, COMPLETED)."""
    db = get_db()
    t_doc = db.collection("transport_requests").document(request_id).get()
    if not t_doc.exists:
        return jsonify({"success": False, "error": "Transport request not found"}), 404

    payload = request.get_json() or {}
    new_status = payload.get("status", "IN_TRANSIT").strip().upper()
    if new_status not in ("IN_TRANSIT", "COMPLETED", "PICKED_UP"):
        return jsonify({"success": False, "error": f"Invalid transport status '{new_status}'"}), 400

    now_utc = datetime.now(timezone.utc).isoformat()

    db.collection("transport_requests").document(request_id).update({
        "status": new_status,
        "updated_at": now_utc
    })

    journey_id = t_doc.to_dict().get("journey_id")
    if journey_id:
        j_doc = db.collection("care_journeys").document(journey_id).get()
        j_data = j_doc.to_dict() if j_doc.exists else {}

        # Transition journey status when driver starts or completes transport
        if new_status == "COMPLETED":
            new_journey_status = "COMPLETED"
        elif new_status in ("IN_TRANSIT", "PICKED_UP"):
            new_journey_status = "IN_TRANSIT"
        else:
            new_journey_status = j_data.get("status", "COORDINATION_CONFIRMED")

        db.collection("care_journeys").document(journey_id).update({
            "transport_status": new_status,
            "status": new_journey_status,
            "updated_at": now_utc
        })
        log_journey_event(
            db,
            journey_id,
            f"TRANSPORT_{new_status}",
            "Driver",
            f"Driver updated transport status to {new_status}."
        )
        # Re-evaluate readiness
        evaluate_readiness(journey_id)

    return jsonify({"success": True, "status": new_status})

@transport_bp.route("/requests/<request_id>/cancel", methods=["POST"])
@require_role("driver", "admin")
def cancel_transport(request_id):
    """Driver cancels transport request with a mandatory documented reason."""
    db = get_db()
    t_doc = db.collection("transport_requests").document(request_id).get()
    if not t_doc.exists:
        return jsonify({"success": False, "error": "Transport request not found"}), 404

    payload = request.get_json() or {}
    reason = payload.get("reason", "").strip()
    if not reason:
        return jsonify({"success": False, "error": "A valid cancellation reason is required for driver accountability."}), 400

    user = get_authenticated_user() or {}
    driver_name = payload.get("driver_name") or user.get("name", "Driver")
    now_utc = datetime.now(timezone.utc).isoformat()

    db.collection("transport_requests").document(request_id).update({
        "status": "CANCELLED",
        "cancellation_reason": reason,
        "updated_at": now_utc
    })

    journey_id = t_doc.to_dict().get("journey_id")
    if journey_id:
        db.collection("care_journeys").document(journey_id).update({
            "transport_status": "FAILED",
            "status": "TRANSPORT_FAILED",
            "updated_at": now_utc
        })
        log_journey_event(
            db,
            journey_id,
            "TRANSPORT_CANCELLED",
            driver_name,
            f"Transport cancelled by driver: {reason}. Status updated to TRANSPORT_FAILED."
        )
        # Re-evaluate readiness -> triggers RECHECK_REQUIRED / TRANSPORT_FAILED
        evaluate_readiness(journey_id)

    return jsonify({"success": True, "message": "Transport request cancelled."})
