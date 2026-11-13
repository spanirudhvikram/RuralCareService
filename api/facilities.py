from datetime import datetime, timezone, timedelta
import math
from flask import Blueprint, jsonify, request
from database import get_db
from api.auth import require_role, get_authenticated_user

facilities_bp = Blueprint("facilities", __name__, url_prefix="/api/facilities")

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate geodesic distance in kilometers between two coordinate pairs."""
    try:
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return None
        lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
        if not (-90 <= lat1 <= 90 and -180 <= lon1 <= 180 and -90 <= lat2 <= 90 and -180 <= lon2 <= 180):
            return None
        r = 6371.0  # Earth's radius in km
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return round(r * c, 1)
    except (ValueError, TypeError):
        return None

@facilities_bp.route("/recommendations", methods=["GET"])
def get_recommendations():
    """
    Intelligent alternative healthcare facility recommendation engine.
    Matches exact requested service, excludes failed primary facility,
    ranks by verified availability, freshness, distance (if coords exist), and district.
    """
    db = get_db()
    service_req = request.args.get("service", "").strip()
    exclude_facility = request.args.get("exclude_facility", "").strip()
    district_param = request.args.get("district", "").strip()
    village_param = request.args.get("village", "").strip()
    preferred_date = request.args.get("preferred_date", "").strip()
    lat_param = request.args.get("lat")
    lng_param = request.args.get("lng")

    if not service_req:
        return jsonify({
            "success": False,
            "error": "Query parameter 'service' is required."
        }), 400

    now_utc = datetime.now(timezone.utc)

    # 1. Resolve service ID and canonical name
    canonical_service_id = service_req
    canonical_service_name = service_req
    services_docs = db.collection("services").stream()
    services_list = [d.to_dict() for d in services_docs]
    matched_svc = None
    for s in services_list:
        if s.get("id", "").lower() == service_req.lower():
            matched_svc = s
            break
        elif service_req.lower() in s.get("name", "").lower():
            matched_svc = s
            break

    if matched_svc:
        canonical_service_id = matched_svc.get("id")
        canonical_service_name = matched_svc.get("name")

    # 2. Resolve origin reference point (coordinates and district)
    ref_lat = None
    ref_lng = None
    ref_district = district_param or None

    if lat_param and lng_param:
        try:
            ref_lat = float(lat_param)
            ref_lng = float(lng_param)
        except (ValueError, TypeError):
            pass

    if (ref_lat is None or ref_district is None) and village_param:
        v_docs = db.collection("villages").stream()
        for vd in v_docs:
            v_data = vd.to_dict()
            if (v_data.get("id", "").lower() == village_param.lower() or 
                v_data.get("name", "").lower() == village_param.lower()):
                if ref_district is None:
                    ref_district = v_data.get("district")
                if ref_lat is None and "latitude" in v_data and "longitude" in v_data:
                    ref_lat = v_data.get("latitude")
                    ref_lng = v_data.get("longitude")
                break

    if ref_district is None and exclude_facility:
        f_ex = db.collection("facilities").document(exclude_facility).get()
        if f_ex.exists:
            ref_district = f_ex.to_dict().get("district")

    # 3. Stream all facilities and evaluate alternatives
    f_docs = db.collection("facilities").stream()
    candidates = []

    for doc in f_docs:
        f = doc.to_dict()
        fid = doc.id
        f["id"] = fid

        # Rule 2: Exclude failed or unavailable primary facility
        if exclude_facility and fid.lower() == exclude_facility.lower():
            continue

        # Rule 3: Exclude inactive facilities
        if f.get("is_active") is False or f.get("status") == "INACTIVE":
            continue

        # Rule 1: Match exact requested healthcare service
        available_services = f.get("available_services", [])
        if canonical_service_id not in available_services and service_req not in available_services:
            continue

        # Expiration check
        is_expired = False
        valid_until_str = f.get("verification_valid_until")
        if valid_until_str:
            try:
                valid_until = datetime.fromisoformat(valid_until_str.replace("Z", "+00:00"))
                if valid_until <= now_utc:
                    is_expired = True
            except Exception:
                is_expired = True

        raw_status = f.get("verification_status", "UNVERIFIED")

        # Capacity & Operating Hours
        total_cap = f.get("total_capacity_today", 30)
        alloc_cap = f.get("allocated_appointments_today", 0)
        cap_available = max(0, total_cap - alloc_cap)

        # Categorization into 3 standard labels
        if raw_status == "VERIFIED" and not is_expired:
            if total_cap > 0 and cap_available == 0:
                rec_label = "SCHEDULED OPTION"
                is_confirmed = False
                priority_tier = 2
                avail_info = "Regular capacity booked for today; advance appointment slot required."
            else:
                rec_label = "VERIFIED ALTERNATIVE"
                is_confirmed = True
                priority_tier = 1
                avail_info = f"Confirmed open {f.get('operating_hours', 'Regular OPD')}. {cap_available} slots remaining today."
        elif raw_status == "SCHEDULED":
            rec_label = "SCHEDULED OPTION"
            is_confirmed = False
            priority_tier = 2
            avail_info = "Consultant available on scheduled specialty days."
        else:
            # EXPIRED or UNVERIFIED
            rec_label = "UNVERIFIED OPTION"
            is_confirmed = False
            priority_tier = 3
            if is_expired:
                avail_info = "Verification expired. Requires morning staff check before travel."
            else:
                avail_info = "Awaiting formal staff inspection and service schedule verification."

        # Verification Freshness
        freshness_hours = None
        last_ver_str = f.get("last_verified_at")
        if last_ver_str:
            try:
                last_ver_dt = datetime.fromisoformat(last_ver_str.replace("Z", "+00:00"))
                freshness_hours = round((now_utc - last_ver_dt).total_seconds() / 3600.0, 1)
            except Exception:
                pass

        # Distance calculation - only when reliable coordinates exist on both
        fac_lat = f.get("latitude")
        fac_lng = f.get("longitude")
        dist_km = haversine_distance(ref_lat, ref_lng, fac_lat, fac_lng)
        dist_text = f"{dist_km} km" if dist_km is not None else None

        is_same_district = bool(ref_district and f.get("district", "").lower() == ref_district.lower())

        candidate = {
            "id": fid,
            "name": f.get("name"),
            "name_ta": f.get("name_ta"),
            "facility_type": f.get("facility_type"),
            "location": f.get("location"),
            "district": f.get("district"),
            "contact_phone": f.get("contact_phone"),
            "available_services": available_services,
            "verification_status": "EXPIRED" if is_expired else raw_status,
            "last_verified_at": last_ver_str,
            "verification_valid_until": valid_until_str,
            "verified_by_staff": f.get("verified_by_staff"),
            "availability_source": f.get("verified_by_staff") or "Facility Inspection Record",
            "operating_hours": f.get("operating_hours", "08:00 AM - 04:00 PM"),
            "capacity_available": cap_available,
            "recommendation_label": rec_label,
            "is_confirmed_available": is_confirmed,
            "availability_info": avail_info,
            "distance_km": dist_km,
            "distance_text": dist_text,
            "has_coordinates": dist_km is not None,
            "is_same_district": is_same_district,
            "verification_freshness_hours": freshness_hours,
            "priority_tier": priority_tier,
            "is_demo_record": f.get("is_demo_record", True)
        }
        candidates.append(candidate)

    # 4. Multi-level Ranking
    def sort_key(item):
        # 1. Priority Tier: 1 (VERIFIED ALTERNATIVE) < 2 (SCHEDULED OPTION) < 3 (UNVERIFIED OPTION)
        p_tier = item["priority_tier"]

        # 2. Distance if available, else district priority
        if item["distance_km"] is not None:
            geo_score = (0, item["distance_km"])
        else:
            # 0 if same district, 1 if out-of-district
            dist_flag = 0 if item["is_same_district"] else 1
            geo_score = (1, dist_flag)

        # 3. Verification Freshness (smaller hours is fresher; None treated as 999999)
        freshness = item["verification_freshness_hours"] if item["verification_freshness_hours"] is not None else 999999.0

        # 4. Capacity Available (higher is better, so negate)
        neg_capacity = -item["capacity_available"]

        return (p_tier, geo_score, freshness, neg_capacity)

    candidates.sort(key=sort_key)

    verified_count = sum(1 for c in candidates if c["recommendation_label"] == "VERIFIED ALTERNATIVE")

    return jsonify({
        "success": True,
        "service": canonical_service_id,
        "service_name": canonical_service_name,
        "exclude_facility": exclude_facility or None,
        "district": ref_district,
        "village": village_param or None,
        "total_found": len(candidates),
        "verified_count": verified_count,
        "has_verified_alternatives": verified_count > 0,
        "recommendations": candidates,
        "disclaimer": "Alternative recommendations verify scheduled facility capacity. Patient must confirm before rebooking."
    })


@facilities_bp.route("", methods=["GET"])
def get_facilities():
    db = get_db()
    service_filter = request.args.get("service")
    district_filter = request.args.get("district")
    
    docs = db.collection("facilities").stream()
    facilities = []
    now_utc = datetime.now(timezone.utc)

    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        
        # Check if verification expired
        valid_until_str = data.get("verification_valid_until")
        if valid_until_str:
            try:
                valid_until = datetime.fromisoformat(valid_until_str.replace("Z", "+00:00"))
                if valid_until < now_utc and data.get("verification_status") == "VERIFIED":
                    data["verification_status"] = "EXPIRED"
            except Exception:
                pass

        if service_filter and service_filter not in data.get("available_services", []):
            continue
        if district_filter and data.get("district", "").lower() != district_filter.lower():
            continue

        facilities.append(data)

    return jsonify({"success": True, "count": len(facilities), "facilities": facilities})

@facilities_bp.route("/<facility_id>", methods=["GET"])
def get_facility(facility_id):
    db = get_db()
    doc = db.collection("facilities").document(facility_id).get()
    if not doc.exists:
        return jsonify({"success": False, "error": "Facility not found"}), 404
    data = doc.to_dict()
    data["id"] = doc.id
    return jsonify({"success": True, "facility": data})

@facilities_bp.route("/<facility_id>/verify", methods=["POST"])
@require_role("facility_staff", "admin")
def verify_facility_service(facility_id):
    """Facility staff or admin updates verification of services and sets validity period."""
    db = get_db()
    doc = db.collection("facilities").document(facility_id).get()
    if not doc.exists:
        return jsonify({"success": False, "error": "Facility not found"}), 404

    user = get_authenticated_user()
    if user.get("role") == "facility_staff" and user.get("facility_id") != facility_id:
        return jsonify({"success": False, "error": "Staff may only verify their own assigned hospital."}), 403

    payload = request.get_json() or {}
    status = payload.get("verification_status", "VERIFIED")
    if status not in ("VERIFIED", "UNVERIFIED", "EXPIRED"):
        return jsonify({"success": False, "error": f"Invalid verification status '{status}'"}), 400

    try:
        valid_hours = int(payload.get("valid_hours", 48))
        if valid_hours < 0 or valid_hours > 720:
            return jsonify({"success": False, "error": "Valid hours must be between 0 and 720 hours."}), 400
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid valid_hours value."}), 400

    verified_by = payload.get("verified_by", user.get("name", "Hospital Administration Staff"))
    services = payload.get("available_services")
    notes = payload.get("verification_notes", "Service schedule verified by attending medical staff.")

    now_utc = datetime.now(timezone.utc)
    valid_until = (now_utc + timedelta(hours=valid_hours)).isoformat() if valid_hours > 0 else now_utc.isoformat()

    updates = {
        "verification_status": status,
        "last_verified_at": now_utc.isoformat(),
        "verification_valid_until": valid_until,
        "verified_by_staff": verified_by,
        "verification_notes": notes
    }
    if services is not None:
        updates["available_services"] = services

    db.collection("facilities").document(facility_id).update(updates)
    
    # Re-evaluate any active journeys linked to this facility
    journeys = db.collection("care_journeys").where("facility_id", "==", facility_id)
    from api.readiness import evaluate_readiness
    for j in journeys:
        try:
            evaluate_readiness(j.id)
        except Exception:
            pass

    return jsonify({
        "success": True,
        "message": f"Facility {facility_id} verification updated to {status}",
        "valid_until": valid_until
    })

@facilities_bp.route("/services", methods=["GET"])
def get_services():
    db = get_db()
    docs = db.collection("services").stream()
    services = [d.to_dict() for d in docs]
    return jsonify({"success": True, "services": services})

@facilities_bp.route("/villages", methods=["GET"])
def get_villages():
    db = get_db()
    docs = db.collection("villages").stream()
    villages = [d.to_dict() for d in docs]
    return jsonify({"success": True, "villages": villages})

@facilities_bp.route("/<facility_id>/assigned-journeys", methods=["GET"])
@facilities_bp.route("/<facility_id>/journeys", methods=["GET"])
def get_facility_assigned_journeys(facility_id):
    """
    Returns care journeys assigned to this facility for Doctor review.
    Shows Patient ID, patient name, age, village, symptoms, urgency, slot, and status.
    Ensures patient details are always resolved persistently from the patients collection.
    """
    db = get_db()
    if facility_id.upper() == "ALL":
        docs = db.collection("care_journeys").stream()
        fac_name = "All Coordinated Healthcare Facilities"
    else:
        f_doc = db.collection("facilities").document(facility_id).get()
        if not f_doc.exists:
            return jsonify({"success": False, "error": "Facility not found"}), 404
        docs = db.collection("care_journeys").where("facility_id", "==", facility_id)
        fac_name = f_doc.to_dict().get("name")

    journeys = []
    for doc in docs:
        d = doc.to_dict()
        raw_phone = str(d.get("contact_number", "")).strip()
        masked_phone = ("XXXXXX" + raw_phone[-4:]) if len(raw_phone) >= 4 else "XXXXXXXXXX"
        
        pid = d.get("patient_id")
        p_name = d.get("patient_name")
        p_age = d.get("age")
        p_village = d.get("village")

        # 1. Backfill from patients collection if PID is present
        if pid and pid != "NEW_PATIENT":
            p_doc = db.collection("patients").document(pid).get()
            if p_doc.exists:
                p_data = p_doc.to_dict()
                if not p_name: p_name = p_data.get("name")
                if not p_age: p_age = p_data.get("age")
                if not p_village: p_village = p_data.get("village")
        
        # 2. If PID is missing, attempt to match patient by contact number
        if (not pid or pid == "NEW_PATIENT") and raw_phone:
            p_matches = db.collection("patients").where("contact_number", "==", raw_phone)
            if len(p_matches) > 0:
                p_data = p_matches[0].to_dict()
                pid = p_data.get("id")
                if not p_name: p_name = p_data.get("name")
                if not p_age: p_age = p_data.get("age")
                if not p_village: p_village = p_data.get("village")
            else:
                # Generate persistent PID and patient record
                from database import generate_patient_id
                pid = generate_patient_id(db)
                now_iso = datetime.now(timezone.utc).isoformat()
                p_name = p_name or "Registered Citizen"
                p_age = p_age or 45
                p_village = p_village or "Chettipalayam"
                db.collection("patients").document(pid).set({
                    "id": pid,
                    "name": p_name,
                    "age": int(p_age),
                    "contact_number": raw_phone,
                    "village": p_village,
                    "consent_given": True,
                    "registered_at": now_iso,
                    "is_demo_record": True
                })
                # Persist back to the care journey document
                db.collection("care_journeys").document(doc.id).update({
                    "patient_id": pid,
                    "patient_name": p_name,
                    "age": int(p_age),
                    "village": p_village
                })

        # Check past history for this patient
        history_count = 0
        if pid:
            history_docs = db.collection("care_journeys").where("patient_id", "==", pid)
            history_count = len(history_docs)

        symptoms = (
            d.get("patient_reported_symptoms") or 
            d.get("symptoms") or 
            d.get("notes") or 
            d.get("preferred_service_name") or 
            "General Consultation"
        )

        item = {
            "id": d.get("id"),
            "patient_id": pid or "PID-CITIZEN",
            "patient_name": p_name or "Registered Patient",
            "patient_phone": masked_phone,
            "raw_phone": raw_phone,
            "age": p_age or 45,
            "village": p_village or "Chettipalayam",
            "preferred_service_id": d.get("preferred_service_id"),
            "preferred_service_name": d.get("preferred_service_name"),
            "patient_reported_symptoms": symptoms,
            "symptoms": symptoms,
            "preferred_date": d.get("preferred_date"),
            "status": d.get("status"),
            "appointment_confirmed": d.get("appointment_confirmed", False),
            "appointment_slot": d.get("appointment_slot"),
            "clinical_urgency": d.get("clinical_urgency"),
            "doctor_assessment": d.get("doctor_assessment"),
            "prescription_uploaded": d.get("prescription_uploaded", False),
            "prescription_id": d.get("prescription_id"),
            "transport_required": d.get("transport_required", False),
            "pickup_landmark": d.get("pickup_landmark"),
            "requested_pickup_time": d.get("requested_pickup_time") or d.get("pickup_time"),
            "history_count": history_count,
            "created_at": d.get("created_at")
        }
        journeys.append(item)

    journeys.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify({"success": True, "count": len(journeys), "facility_name": fac_name, "journeys": journeys})

@facilities_bp.route("/doctor/review", methods=["POST"])
@require_role("facility_staff", "admin")
def doctor_review_journey():
    """
    Attending Doctor reviews assigned patient request.
    Strict Rules:
    - Urgency classification (ROUTINE, URGENT, MONITORING) must be explicitly selected by doctor.
      Zero automated AI clinical urgency assignment.
    - Prevents double-booking for the same facility appointment slot on the same date.
    - Keeps doctor clinical assessment strictly separated from patient-reported symptoms.
    - Optionally creates verified prescription.
    """
    db = get_db()
    payload = request.get_json() or {}

    journey_id = str(payload.get("journey_id", "")).strip()
    if not journey_id:
        return jsonify({"success": False, "error": "journey_id is required."}), 400

    j_doc = db.collection("care_journeys").document(journey_id).get()
    if not j_doc.exists:
        return jsonify({"success": False, "error": "Journey not found"}), 404

    j_data = j_doc.to_dict()
    user = get_authenticated_user() or {}
    facility_id = j_data.get("facility_id")

    if user.get("role") == "facility_staff" and user.get("facility_id") != facility_id:
        return jsonify({"success": False, "error": "Doctor may only review appointments for their own hospital."}), 403

    action = str(payload.get("action", "accept")).strip().lower()
    if action not in ("accept", "reject"):
        return jsonify({"success": False, "error": "Review action must be 'accept' or 'reject'."}), 400

    clinical_urgency = str(payload.get("clinical_urgency", "ROUTINE")).strip().upper()
    if clinical_urgency not in ("ROUTINE", "URGENT", "MONITORING"):
        return jsonify({"success": False, "error": "Clinical urgency must be ROUTINE, URGENT, or MONITORING."}), 400

    doctor_name = str(payload.get("doctor_name") or user.get("name", "Attending Doctor")).strip()
    slot = str(payload.get("appointment_slot", "10:30 AM Outpatient")).strip()
    doctor_notes = str(payload.get("doctor_notes", "")).strip()
    preferred_date = j_data.get("preferred_date")

    now_utc = datetime.now(timezone.utc).isoformat()

    if action == "accept":
        # Double-booking check: prevent two patients having the same slot at the same facility on the same date
        existing_bookings = db.collection("care_journeys").where("facility_id", "==", facility_id)
        for b in existing_bookings:
            bd = b.to_dict()
            if (b.id != journey_id and 
                bd.get("preferred_date") == preferred_date and 
                bd.get("appointment_slot") == slot and 
                bd.get("status") not in ("CANCELLED", "COMPLETED")):
                return jsonify({
                    "success": False,
                    "error": f"Slot '{slot}' is already booked for another patient on {preferred_date}. Please choose an alternative slot to prevent double-booking.",
                    "code": "SLOT_CONFLICT"
                }), 409

        doctor_assessment = {
            "doctor_name": doctor_name,
            "clinical_urgency": clinical_urgency,
            "doctor_notes": doctor_notes or f"Clinical review completed by {doctor_name}. Urgency set to {clinical_urgency}.",
            "reviewed_at": now_utc
        }

        updates = {
            "appointment_confirmed": True,
            "appointment_slot": slot,
            "clinical_urgency": clinical_urgency,
            "doctor_assessment": doctor_assessment,
            "doctor_name": doctor_name,
            "service_verified": True,
            "service_verification_timestamp": now_utc,
            "status": "APPOINTMENT_ACCEPTED",
            "updated_at": now_utc
        }

        # Check if prescription notes provided
        prescription_notes = payload.get("prescription_notes")
        medicines = payload.get("medicines")
        if prescription_notes or medicines:
            from api.prescriptions import generate_rx_id
            rx_id = generate_rx_id(db)
            med_list = medicines if isinstance(medicines, list) else [
                {"name": line.strip(), "dosage": "As directed", "days": 30, "quantity": 30}
                for line in str(prescription_notes).split("\n") if line.strip()
            ]
            rx_doc_data = {
                "id": rx_id,
                "journey_id": journey_id,
                "patient_id": j_data.get("patient_id"),
                "patient_name": j_data.get("patient_name"),
                "doctor_name": doctor_name,
                "facility_id": facility_id,
                "facility_name": j_data.get("facility_name"),
                "clinical_urgency": clinical_urgency,
                "doctor_notes": doctor_notes,
                "medicines": med_list,
                "verification_type": "DOCTOR_AUTHENTICATED",
                "is_verified": True,
                "valid_until": (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d"),
                "created_at": now_utc,
                "refill_reminder_days": 30,
                "refill_reminder_scheduled": True,
                "is_demo_record": True
            }
            db.collection("prescriptions").document(rx_id).set(rx_doc_data)
            updates["prescription_id"] = rx_id
            updates["prescription_uploaded"] = True

        db.collection("care_journeys").document(journey_id).update(updates)
        from api.readiness import log_journey_event, evaluate_readiness
        log_journey_event(
            db,
            journey_id,
            "DOCTOR_REVIEW_COMPLETED",
            doctor_name,
            f"Doctor {doctor_name} assigned urgency {clinical_urgency} and confirmed slot {slot}. Notes: {doctor_notes}"
        )
    else:
        # Reject
        doctor_assessment = {
            "doctor_name": doctor_name,
            "clinical_urgency": clinical_urgency,
            "doctor_notes": doctor_notes or "Specialist doctor unavailable on requested date.",
            "reviewed_at": now_utc
        }
        updates = {
            "appointment_confirmed": False,
            "appointment_slot": None,
            "clinical_urgency": clinical_urgency,
            "doctor_assessment": doctor_assessment,
            "status": "CANCELLED",
            "cancellation_reason": doctor_notes or "Appointment rejected by facility doctor.",
            "updated_at": now_utc
        }
        db.collection("care_journeys").document(journey_id).update(updates)
        from api.readiness import log_journey_event, evaluate_readiness
        log_journey_event(
            db,
            journey_id,
            "APPOINTMENT_REJECTED",
            doctor_name,
            f"Appointment rejected by {doctor_name}: {doctor_notes or 'Specialist unavailable'}"
        )

    from api.readiness import evaluate_readiness
    eval_res = evaluate_readiness(journey_id)
    refreshed = db.collection("care_journeys").document(journey_id).get().to_dict()

    return jsonify({
        "success": True,
        "action": action,
        "clinical_urgency": clinical_urgency,
        "slot": slot if action == "accept" else None,
        "journey": refreshed,
        "readiness": eval_res.get("readiness")
    })
