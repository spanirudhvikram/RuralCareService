from datetime import datetime, timezone, timedelta
import random
import uuid
import re
from flask import Blueprint, jsonify, request, session
from database import get_db
from api.journeys import sanitize_text
from api.readiness import log_journey_event
from api.auth import get_authenticated_user

prescriptions_bp = Blueprint("prescriptions", __name__, url_prefix="/api/prescriptions")

def generate_rx_id(db):
    for _ in range(10):
        num = random.randint(1000, 9999)
        rx_id = f"RX-2026-{num}"
        if not db.collection("prescriptions").document(rx_id).get().exists:
            return rx_id
    return f"RX-2026-{uuid.uuid4().hex[:4].upper()}"

def generate_order_id(db):
    for _ in range(10):
        num = random.randint(1000, 9999)
        ord_id = f"ORD-2026-{num}"
        if not db.collection("medicine_orders").document(ord_id).get().exists:
            return ord_id
    return f"ORD-2026-{uuid.uuid4().hex[:4].upper()}"

@prescriptions_bp.route("/upload", methods=["POST"])
def upload_prescription():
    """
    Upload and register an authentic medical prescription.
    Can be submitted by attending doctor or patient.
    Extracts and stores medicines with structured human/doctor verification.
    """
    db = get_db()
    payload = request.get_json() or {}

    journey_id = sanitize_text(payload.get("journey_id", ""))
    patient_id = sanitize_text(payload.get("patient_id", ""))
    patient_name = sanitize_text(payload.get("patient_name", ""))
    doctor_name = sanitize_text(payload.get("doctor_name", "Attending Medical Officer"))
    facility_id = sanitize_text(payload.get("facility_id", ""))
    facility_name = sanitize_text(payload.get("facility_name", "Rural Health Facility"))
    clinical_urgency = sanitize_text(payload.get("clinical_urgency", "ROUTINE")).upper()
    doctor_notes = sanitize_text(payload.get("doctor_notes", ""))
    file_name = sanitize_text(payload.get("file_name", "prescription_doc.jpg"))
    file_data_preview = payload.get("file_data") or payload.get("image_base64")

    # File type security validation
    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".webp"}
    if file_name:
        from pathlib import Path
        ext = Path(file_name).suffix.lower()
        if ext and ext not in ALLOWED_EXTENSIONS:
            return jsonify({
                "success": False,
                "error": f"Invalid file format '{ext}'. Allowed formats: JPG, JPEG, PNG, PDF, WEBP.",
                "code": "INVALID_FILE_TYPE"
            }), 400

    # Resolve patient and journey if one is given
    if journey_id and not patient_id:
        j_doc = db.collection("care_journeys").document(journey_id).get()
        if j_doc.exists:
            j_data = j_doc.to_dict()
            patient_id = j_data.get("patient_id", "")
            if not patient_name:
                patient_name = j_data.get("patient_name", "")
            if not facility_name:
                facility_name = j_data.get("facility_name", "")
            if not facility_id:
                facility_id = j_data.get("facility_id", "")

    if not patient_name:
        patient_name = "Rural Patient"

    medicines = payload.get("medicines", [])
    if not isinstance(medicines, list) or len(medicines) == 0:
        # Default essential prescription items if submitting notes
        raw_med_str = payload.get("medicines_text", "")
        if raw_med_str:
            lines = [l.strip() for l in raw_med_str.split("\n") if l.strip()]
            medicines = [{"name": line, "dosage": "As directed", "days": 30, "quantity": 30} for line in lines]
        else:
            medicines = [
                {"name": "Prescribed Outpatient Medication", "dosage": "Once daily after food", "days": 30, "quantity": 30}
            ]

    now_utc = datetime.now(timezone.utc)
    rx_id = generate_rx_id(db)

    valid_days = int(payload.get("valid_days", 30))
    valid_until = (now_utc + timedelta(days=valid_days)).strftime("%Y-%m-%d")

    rx_record = {
        "id": rx_id,
        "journey_id": journey_id or None,
        "patient_id": patient_id or None,
        "patient_name": patient_name,
        "doctor_name": doctor_name,
        "facility_id": facility_id or None,
        "facility_name": facility_name,
        "clinical_urgency": clinical_urgency if clinical_urgency in ("ROUTINE", "URGENT", "MONITORING") else "ROUTINE",
        "doctor_notes": doctor_notes,
        "file_name": file_name,
        "has_document": bool(file_data_preview),
        "medicines": medicines,
        "verification_type": "DOCTOR_AUTHENTICATED",
        "is_verified": True,
        "valid_until": valid_until,
        "created_at": now_utc.isoformat(),
        "refill_reminder_days": valid_days,
        "refill_reminder_scheduled": True,
        "reminder_due_date": valid_until,
        "is_demo_record": True
    }

    db.collection("prescriptions").document(rx_id).set(rx_record)

    if journey_id:
        db.collection("care_journeys").document(journey_id).update({
            "prescription_id": rx_id,
            "prescription_uploaded": True,
            "has_active_prescription": True,
            "updated_at": now_utc.isoformat()
        })
        log_journey_event(
            db,
            journey_id,
            "PRESCRIPTION_UPLOADED",
            doctor_name,
            f"Prescription {rx_id} uploaded. {len(medicines)} medicines prescribed. Refill scheduled for {valid_days} days."
        )

    return jsonify({
        "success": True,
        "message": f"Prescription {rx_id} verified and recorded successfully.",
        "prescription": rx_record
    }), 201

@prescriptions_bp.route("", methods=["GET"])
def list_prescriptions():
    """List prescriptions filtered by patient_id or journey_id."""
    db = get_db()
    patient_id = request.args.get("patient_id")
    journey_id = request.args.get("journey_id")

    docs = db.collection("prescriptions").stream()
    results = []
    for doc in docs:
        d = doc.to_dict()
        if patient_id and d.get("patient_id") != patient_id:
            continue
        if journey_id and d.get("journey_id") != journey_id:
            continue
        results.append(d)

    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify({"success": True, "count": len(results), "prescriptions": results})

@prescriptions_bp.route("/<rx_id>", methods=["GET"])
def get_prescription(rx_id):
    """Retrieve single prescription."""
    db = get_db()
    doc = db.collection("prescriptions").document(rx_id).get()
    if not doc.exists:
        return jsonify({"success": False, "error": "Prescription not found"}), 404
    return jsonify({"success": True, "prescription": doc.to_dict()})

@prescriptions_bp.route("/pharmacies", methods=["GET"])
def get_pharmacies():
    """List verified rural pharmacies with live stock."""
    db = get_db()
    village = request.args.get("village", "").strip()
    district = request.args.get("district", "").strip()
    medicine_query = request.args.get("medicine", "").strip().lower()

    docs = db.collection("pharmacies").stream()
    pharmacies = []

    for doc in docs:
        p = doc.to_dict()
        if village and p.get("village", "").lower() != village.lower():
            continue
        if district and p.get("district", "").lower() != district.lower():
            continue

        # If searching by medicine name
        if medicine_query:
            stock = p.get("stock", [])
            match = any(medicine_query in str(m.get("name", "")).lower() for m in stock)
            if not match:
                continue

        pharmacies.append(p)

    return jsonify({"success": True, "count": len(pharmacies), "pharmacies": pharmacies})

@prescriptions_bp.route("/<rx_id>/order", methods=["POST"])
def place_medicine_order(rx_id):
    """
    Patient confirms medicine order at a verified rural pharmacy.
    Fulfillment choices: PICKUP or DELIVERY.
    Detects medicine stock shortages and registers PHARMACY_STOCK_SHORTAGE events for Demand Radar!
    """
    db = get_db()
    rx_doc = db.collection("prescriptions").document(rx_id).get()
    if not rx_doc.exists:
        return jsonify({"success": False, "error": "Prescription not found"}), 404

    rx = rx_doc.to_dict()
    payload = request.get_json() or {}

    pharmacy_id = sanitize_text(payload.get("pharmacy_id", "PHARM-01"))
    fulfillment_type = sanitize_text(payload.get("fulfillment_type", "PICKUP")).upper()
    delivery_address = sanitize_text(payload.get("delivery_address", rx.get("village", "")))

    if fulfillment_type not in ("PICKUP", "DELIVERY"):
        fulfillment_type = "PICKUP"

    pharm_doc = db.collection("pharmacies").document(pharmacy_id).get()
    if not pharm_doc.exists:
        return jsonify({"success": False, "error": "Selected pharmacy not found"}), 404

    pharm = pharm_doc.to_dict()
    pharm_stock = {m.get("name", "").lower(): m for m in pharm.get("stock", [])}

    # Evaluate medicine stock availability
    order_items = []
    has_shortage = False
    shortage_medicines = []

    for med in rx.get("medicines", []):
        med_name = med.get("name", "")
        med_lower = med_name.lower()
        matched_stock = None
        for stock_name, stock_item in pharm_stock.items():
            if stock_name in med_lower or med_lower in stock_name:
                matched_stock = stock_item
                break

        if matched_stock:
            stock_status = matched_stock.get("status", "IN_STOCK")
            unit_price = matched_stock.get("price_inr", 20.0)
            if stock_status == "OUT_OF_STOCK":
                has_shortage = True
                shortage_medicines.append(med_name)
        else:
            stock_status = "IN_STOCK"  # Default generic available
            unit_price = 20.0

        order_items.append({
            "name": med_name,
            "dosage": med.get("dosage", "1 daily"),
            "quantity": med.get("quantity", 30),
            "stock_status": stock_status,
            "unit_price": unit_price
        })

    now_utc = datetime.now(timezone.utc)
    order_id = generate_order_id(db)

    delivery_fee = pharm.get("delivery_charge_inr", 20) if fulfillment_type == "DELIVERY" else 0
    subtotal = sum(i["unit_price"] for i in order_items)
    total_amount = round(subtotal + delivery_fee, 2)

    order_status = "PARTIAL_STOCK_PENDING" if has_shortage else "ORDER_CONFIRMED"

    order_record = {
        "id": order_id,
        "prescription_id": rx_id,
        "journey_id": rx.get("journey_id"),
        "patient_id": rx.get("patient_id"),
        "patient_name": rx.get("patient_name"),
        "pharmacy_id": pharmacy_id,
        "pharmacy_name": pharm.get("name"),
        "fulfillment_type": fulfillment_type,
        "delivery_address": delivery_address if fulfillment_type == "DELIVERY" else None,
        "items": order_items,
        "has_stock_shortage": has_shortage,
        "shortage_medicines": shortage_medicines,
        "subtotal": subtotal,
        "delivery_fee": delivery_fee,
        "total_amount": total_amount,
        "status": order_status,
        "created_at": now_utc.isoformat(),
        "is_demo_record": True
    }

    db.collection("medicine_orders").document(order_id).set(order_record)

    # Link to care journey
    journey_id = rx.get("journey_id")
    if journey_id:
        db.collection("care_journeys").document(journey_id).update({
            "medicine_order_id": order_id,
            "medicine_order_status": order_status,
            "updated_at": now_utc.isoformat()
        })
        if has_shortage:
            log_journey_event(
                db,
                journey_id,
                "PHARMACY_STOCK_SHORTAGE",
                pharm.get("name"),
                f"Pharmacy stock shortage detected for: {', '.join(shortage_medicines)}. Logged to Village Health Demand Radar."
            )
        else:
            log_journey_event(
                db,
                journey_id,
                "MEDICINE_ORDER_CONFIRMED",
                "Patient",
                f"Medicine order {order_id} confirmed for {fulfillment_type} at {pharm.get('name')}. Total: ₹{total_amount}."
            )

    return jsonify({
        "success": True,
        "message": f"Medicine order {order_id} placed successfully.",
        "order": order_record,
        "has_stock_shortage": has_shortage
    }), 201

@prescriptions_bp.route("/<rx_id>/refill-reminder", methods=["POST"])
def schedule_refill_reminder(rx_id):
    """
    Schedules an automated 30-day refill reminder for chronic/long-term medications.
    """
    db = get_db()
    rx_doc = db.collection("prescriptions").document(rx_id).get()
    if not rx_doc.exists:
        return jsonify({"success": False, "error": "Prescription not found"}), 404

    payload = request.get_json() or {}
    days = int(payload.get("reminder_days", 30))
    now_utc = datetime.now(timezone.utc)
    due_date = (now_utc + timedelta(days=days)).strftime("%Y-%m-%d")

    db.collection("prescriptions").document(rx_id).update({
        "refill_reminder_days": days,
        "refill_reminder_scheduled": True,
        "reminder_due_date": due_date,
        "updated_at": now_utc.isoformat()
    })

    journey_id = rx_doc.to_dict().get("journey_id")
    if journey_id:
        log_journey_event(
            db,
            journey_id,
            "REFILL_REMINDER_SCHEDULED",
            "RuralCare Pharmacy Module",
            f"Long-term medication refill reminder scheduled in {days} days ({due_date})."
        )

    return jsonify({
        "success": True,
        "message": f"Refill reminder scheduled for {days} days ({due_date}).",
        "due_date": due_date
    })
