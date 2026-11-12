from datetime import datetime, timezone
import uuid
import re
from flask import Blueprint, jsonify, request
from database import get_db
from api.auth import require_role

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")

@admin_bp.route("/overview", methods=["GET"])
@require_role("admin")
def get_overview():
    db = get_db()
    journeys = [d.to_dict() for d in db.collection("care_journeys").stream()]
    facilities = [d.to_dict() for d in db.collection("facilities").stream()]
    drivers = [d.to_dict() for d in db.collection("drivers").stream()]
    villages = [d.to_dict() for d in db.collection("villages").stream()]
    services = [d.to_dict() for d in db.collection("services").stream()]

    total_journeys = len(journeys)
    confirmed_ready = sum(1 for j in journeys if j.get("status") == "COORDINATION_CONFIRMED")
    pending = sum(1 for j in journeys if j.get("status") in ("FACILITY_VERIFICATION_PENDING", "APPOINTMENT_PENDING", "TRANSPORT_PENDING"))
    recheck = sum(1 for j in journeys if j.get("status") == "RECHECK_REQUIRED")
    cancelled = sum(1 for j in journeys if j.get("status") in ("CANCELLED", "TRANSPORT_FAILED"))

    verified_facilities = sum(1 for f in facilities if f.get("verification_status") == "VERIFIED")
    active_drivers = sum(1 for d in drivers if d.get("is_active", True))

    return jsonify({
        "success": True,
        "metrics": {
            "total_journeys": total_journeys,
            "confirmed_ready": confirmed_ready,
            "pending_coordination": pending,
            "recheck_required": recheck,
            "cancelled_or_failed": cancelled,
            "total_facilities": len(facilities),
            "verified_facilities": verified_facilities,
            "active_drivers": active_drivers,
            "total_villages": len(villages),
            "total_services": len(services)
        },
        "recent_journeys": sorted(journeys, key=lambda x: x.get("created_at", ""), reverse=True)[:15]
    })

@admin_bp.route("/facilities", methods=["POST"])
@require_role("admin")
def add_facility():
    db = get_db()
    payload = request.get_json() or {}
    name = re.sub(r"[<>]", "", payload.get("name", "")).strip()
    if not name or len(name) < 3:
        return jsonify({"success": False, "error": "A valid facility name is required"}), 400

    fac_id = f"FAC-{str(uuid.uuid4().hex[:4]).upper()}"
    facility_data = {
        "id": fac_id,
        "name": name,
        "name_ta": re.sub(r"[<>]", "", payload.get("name_ta", name)).strip(),
        "facility_type": payload.get("facility_type", "Primary Health Centre"),
        "location": re.sub(r"[<>]", "", payload.get("location", "")).strip(),
        "district": payload.get("district", "Coimbatore"),
        "contact_phone": payload.get("contact_phone", ""),
        "verification_status": payload.get("verification_status", "UNVERIFIED"),
        "last_verified_at": None,
        "verification_valid_until": None,
        "verified_by_staff": None,
        "available_services": payload.get("available_services", ["SRV-GEN"]),
        "operating_hours": payload.get("operating_hours", "09:00 AM - 04:00 PM"),
        "total_capacity_today": int(payload.get("total_capacity_today", 20)),
        "allocated_appointments_today": 0,
        "is_demo_record": True
    }
    db.collection("facilities").document(fac_id).set(facility_data)
    return jsonify({"success": True, "facility": facility_data}), 201

@admin_bp.route("/drivers", methods=["POST"])
@require_role("admin")
def add_driver():
    db = get_db()
    payload = request.get_json() or {}
    name = re.sub(r"[<>]", "", payload.get("name", "")).strip()
    phone = payload.get("phone", "").strip()
    if not name or not phone or not re.match(r"^\+?[\d\s-]{10,14}$", phone):
        return jsonify({"success": False, "error": "Valid driver name and contact phone are required"}), 400

    drv_id = f"DRV-{str(uuid.uuid4().hex[:4]).upper()}"
    driver_data = {
        "id": drv_id,
        "name": name,
        "name_ta": re.sub(r"[<>]", "", payload.get("name_ta", name)).strip(),
        "role": "driver",
        "phone": phone,
        "vehicle_type": payload.get("vehicle_type", "Auto-Rickshaw"),
        "vehicle_reg": payload.get("vehicle_reg", ""),
        "primary_village": payload.get("primary_village", ""),
        "coverage_villages": payload.get("coverage_villages", [payload.get("primary_village", "")]),
        "is_active": True,
        "verification_status": "VERIFIED",
        "standard_per_km_rate": float(payload.get("standard_per_km_rate", 15)),
        "is_demo_record": True
    }
    db.collection("drivers").document(drv_id).set(driver_data)
    return jsonify({"success": True, "driver": driver_data}), 201

@admin_bp.route("/events", methods=["GET"])
@require_role("admin")
def get_all_events():
    db = get_db()
    docs = db.collection("journey_events").stream()
    events = [d.to_dict() for d in docs]
    events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return jsonify({"success": True, "events": events[:50]})
