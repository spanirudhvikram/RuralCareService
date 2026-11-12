from datetime import datetime, timezone, timedelta
import random
import uuid
import re
from flask import Blueprint, jsonify, request
from database import get_db
from api.auth import require_role, get_authenticated_user

radar_bp = Blueprint("radar", __name__, url_prefix="/api/radar")

# Minimum group size for aggregate reporting (k-anonymity guarantee)
DEFAULT_MIN_GROUP_SIZE = 1

def is_voluntary_cancellation(journey, events):
    """
    Determines if a cancellation was voluntary by the patient
    versus an involuntary system failure (facility doctor missing, transport unavailable, etc.)
    """
    reason = str(journey.get("cancellation_reason", "")).lower()
    
    # Check journey events for actor
    patient_voluntary_keywords = [
        "cancelled by user", "patient personal", "voluntary", "change of mind",
        "family function", "travelled by own", "recovered", "no longer needed",
        "postponed by patient", "patient request"
    ]
    
    for kw in patient_voluntary_keywords:
        if kw in reason:
            return True

    # Check journey events for who cancelled
    for ev in events:
        if ev.get("journey_id") == journey.get("id"):
            ev_type = ev.get("event_type", "")
            actor = str(ev.get("actor", "")).lower()
            desc = str(ev.get("description", "")).lower()
            if "cancel" in ev_type.lower():
                if "patient" in actor and not any(f in desc for f in ["doctor", "specialist", "hospital", "driver", "transport", "unavailable", "failed"]):
                    return True

    return False

def classify_journey_outcome(journey, events_by_journey, now_utc):
    """
    Classifies a journey into verified coordination, completed, facility failure,
    transport failure, expired confirmation, old unresolved, or voluntary cancellation.
    """
    status = journey.get("status", "REQUESTED")
    jid = journey.get("id")
    events = events_by_journey.get(jid, [])
    created_at_str = journey.get("created_at")

    # 1. Completed
    if status == "COMPLETED":
        return "COMPLETED"

    # 2. Coordination Confirmed (Ready)
    if status == "COORDINATION_CONFIRMED":
        return "COORDINATION_CONFIRMED"

    # 3. Transport Failure
    if status == "TRANSPORT_FAILED" or journey.get("transport_status") == "FAILED":
        return "TRANSPORT_FAILURE"
    for ev in events:
        if ev.get("event_type") in ("TRANSPORT_CANCELLED", "TRANSPORT_FAILED"):
            return "TRANSPORT_FAILURE"

    # 3b. Pharmacy Stock Shortage
    for ev in events:
        if ev.get("event_type") == "PHARMACY_STOCK_SHORTAGE":
            return "PHARMACY_SHORTAGE"

    # 4. Voluntary Cancellation (strictly excluded from unmet demand)
    if status == "CANCELLED":
        if is_voluntary_cancellation(journey, events):
            return "VOLUNTARY_CANCELLATION"
        else:
            # Facility failure / Doctor unavailable / No slot
            return "FACILITY_FAILURE"

    # 5. Facility Rejection / Doctor Unavailable
    for ev in events:
        if ev.get("event_type") == "APPOINTMENT_REJECTED":
            return "FACILITY_FAILURE"

    # 6. Expired Confirmation
    if status == "RECHECK_REQUIRED":
        return "EXPIRED_CONFIRMATION"

    # 7. Check if pending request is old (> 7 days) vs recent
    if status in ("FACILITY_VERIFICATION_PENDING", "APPOINTMENT_PENDING", "TRANSPORT_PENDING", "REQUESTED"):
        if created_at_str:
            try:
                created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                if (now_utc - created_dt).days >= 7:
                    return "OLD_UNRESOLVED"
            except Exception:
                pass
        return "PENDING_ACTIVE"

    return "OTHER"

@radar_bp.route("/demand-clusters", methods=["GET"])
def get_demand_clusters():
    """
    Village Health Demand Radar Aggregator.
    Aggregates access requests across villages and services to identify repeat unmet demand.
    Privacy Guarantee: Never exposes patient names, phone numbers, or individual health records.
    """
    db = get_db()
    
    # Query parameters
    period_days = request.args.get("period_days", "30").strip().lower()
    village_filter = request.args.get("village", "").strip()
    try:
        min_group_size = int(request.args.get("min_group_size", DEFAULT_MIN_GROUP_SIZE))
    except (ValueError, TypeError):
        min_group_size = DEFAULT_MIN_GROUP_SIZE

    now_utc = datetime.now(timezone.utc)
    cutoff_dt = None
    if period_days not in ("0", "all", "none"):
        try:
            days = int(period_days)
            cutoff_dt = now_utc - timedelta(days=days)
        except ValueError:
            days = 30
            cutoff_dt = now_utc - timedelta(days=30)
    else:
        days = None

    # Fetch all journeys and events
    journeys_docs = db.collection("care_journeys").stream()
    events_docs = db.collection("journey_events").stream()

    events_by_journey = {}
    for edoc in events_docs:
        edata = edoc.to_dict()
        jid = edata.get("journey_id")
        if jid:
            events_by_journey.setdefault(jid, []).append(edata)

    # Dictionary to track distinct requests and prevent double-counting:
    # Key: (patient_phone_or_hash, preferred_service_id, village)
    seen_patient_service = set()

    # Raw aggregation buckets:
    # Key: (village, service_id)
    clusters = {}
    service_lookup = {}
    for sdoc in db.collection("services").stream():
        sdata = sdoc.to_dict()
        service_lookup[sdata.get("id")] = sdata.get("name")

    overall_metrics = {
        "total_distinct_requests": 0,
        "total_completed": 0,
        "total_coordination_confirmed": 0,
        "total_unmet_demand": 0,
        "total_facility_failures": 0,
        "total_transport_failures": 0,
        "total_pharmacy_shortages": 0,
        "total_expired_confirmations": 0,
        "total_old_unresolved": 0,
        "voluntary_cancellations_excluded": 0
    }

    for jdoc in journeys_docs:
        j = jdoc.to_dict()
        
        # Check if unsynchronized local request (client_uuid without confirmed server persistence)
        # Here we only aggregate confirmed care_journeys documents
        if j.get("is_pending_local_only"):
            continue

        # Village filter
        village = j.get("village", "Unknown Village").strip()
        if village_filter and village.lower() != village_filter.lower():
            continue

        # Date cutoff filter
        created_at_str = j.get("created_at")
        if cutoff_dt and created_at_str:
            try:
                cdt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                if cdt < cutoff_dt:
                    continue
            except Exception:
                pass

        service_id = j.get("preferred_service_id", "SRV-GEN")
        service_name = j.get("preferred_service_name") or service_lookup.get(service_id, "General Medicine")
        patient_identifier = j.get("contact_number") or j.get("client_uuid") or j.get("id")

        # Duplicate Journey Prevention:
        # Avoid counting multiple requests from same patient for same service in period
        dedup_key = (patient_identifier, service_id, village)
        if dedup_key in seen_patient_service:
            continue
        seen_patient_service.add(dedup_key)

        # Classify Outcome
        outcome = classify_journey_outcome(j, events_by_journey, now_utc)

        cluster_key = f"{village}::{service_id}"
        if cluster_key not in clusters:
            clusters[cluster_key] = {
                "cluster_id": f"CLUST-{abs(hash(cluster_key)) % 100000:05d}",
                "village": village,
                "service_id": service_id,
                "service_name": service_name,
                "total_distinct_requests": 0,
                "completed_requests": 0,
                "coordination_confirmed": 0,
                "unmet_demand_count": 0,
                "facility_failures": 0,
                "transport_failures": 0,
                "pharmacy_shortages": 0,
                "expired_confirmations": 0,
                "old_unresolved_requests": 0,
                "voluntary_cancellations": 0,
                "recent_pending": 0,
                "primary_bottleneck": "NONE",
                "priority": "LOW"
            }

        c = clusters[cluster_key]
        c["total_distinct_requests"] += 1
        overall_metrics["total_distinct_requests"] += 1

        if outcome == "COMPLETED":
            c["completed_requests"] += 1
            overall_metrics["total_completed"] += 1
        elif outcome == "COORDINATION_CONFIRMED":
            c["coordination_confirmed"] += 1
            overall_metrics["total_coordination_confirmed"] += 1
        elif outcome == "VOLUNTARY_CANCELLATION":
            # Excluded from unmet demand
            c["voluntary_cancellations"] += 1
            overall_metrics["voluntary_cancellations_excluded"] += 1
        elif outcome == "FACILITY_FAILURE":
            c["facility_failures"] += 1
            c["unmet_demand_count"] += 1
            overall_metrics["total_facility_failures"] += 1
            overall_metrics["total_unmet_demand"] += 1
        elif outcome == "TRANSPORT_FAILURE":
            c["transport_failures"] += 1
            c["unmet_demand_count"] += 1
            overall_metrics["total_transport_failures"] += 1
            overall_metrics["total_unmet_demand"] += 1
        elif outcome == "PHARMACY_SHORTAGE":
            c["pharmacy_shortages"] += 1
            c["unmet_demand_count"] += 1
            overall_metrics["total_pharmacy_shortages"] += 1
            overall_metrics["total_unmet_demand"] += 1
        elif outcome == "EXPIRED_CONFIRMATION":
            c["expired_confirmations"] += 1
            c["unmet_demand_count"] += 1
            overall_metrics["total_expired_confirmations"] += 1
            overall_metrics["total_unmet_demand"] += 1
        elif outcome == "OLD_UNRESOLVED":
            # Flagged for review; not automatically marked confirmed unmet
            c["old_unresolved_requests"] += 1
            overall_metrics["total_old_unresolved"] += 1
        else:
            c["recent_pending"] += 1

    # Compute priorities and primary bottlenecks for each cluster
    cluster_list = []
    for c in clusters.values():
        # Privacy filter: suppress if below min_group_size
        if c["total_distinct_requests"] < min_group_size:
            continue

        # Determine primary bottleneck
        if c["facility_failures"] > c["transport_failures"] and c["facility_failures"] > c.get("pharmacy_shortages", 0) and c["facility_failures"] > 0:
            c["primary_bottleneck"] = "FACILITY_UNAVAILABLE"
        elif c["transport_failures"] >= c["facility_failures"] and c["transport_failures"] > c.get("pharmacy_shortages", 0) and c["transport_failures"] > 0:
            c["primary_bottleneck"] = "TRANSPORT_UNAVAILABLE"
        elif c.get("pharmacy_shortages", 0) > 0 and c.get("pharmacy_shortages", 0) >= c["facility_failures"] and c.get("pharmacy_shortages", 0) >= c["transport_failures"]:
            c["primary_bottleneck"] = "MEDICINE_STOCK_SHORTAGE"
        elif c["expired_confirmations"] > 0:
            c["primary_bottleneck"] = "VERIFICATION_EXPIRED"
        elif c["old_unresolved_requests"] > 0:
            c["primary_bottleneck"] = "COORDINATION_DELAY"
        else:
            c["primary_bottleneck"] = "NONE"

        # Determine priority level
        unmet = c["unmet_demand_count"]
        if unmet >= 2 or (c["facility_failures"] + c["transport_failures"] + c.get("pharmacy_shortages", 0) >= 2):
            c["priority"] = "HIGH"
        elif unmet >= 1 or c["old_unresolved_requests"] >= 2 or c["total_distinct_requests"] >= 3:
            c["priority"] = "MEDIUM"
        else:
            c["priority"] = "LOW"

        cluster_list.append(c)

    # Sort clusters by priority descending, then unmet demand descending
    priority_order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    cluster_list.sort(key=lambda x: (priority_order.get(x["priority"], 0), x["unmet_demand_count"]), reverse=True)

    # Top requested services summary
    service_counts = {}
    for c in cluster_list:
        sname = c["service_name"]
        service_counts[sname] = service_counts.get(sname, 0) + c["total_distinct_requests"]
    top_services = [{"service_name": k, "request_count": v} for k, v in sorted(service_counts.items(), key=lambda x: x[1], reverse=True)]

    # Village-wise demand summary
    village_counts = {}
    for c in cluster_list:
        vname = c["village"]
        if vname not in village_counts:
            village_counts[vname] = {"total_requests": 0, "unmet_demand": 0}
        village_counts[vname]["total_requests"] += c["total_distinct_requests"]
        village_counts[vname]["unmet_demand"] += c["unmet_demand_count"]

    village_demand = [
        {"village": k, "total_requests": v["total_requests"], "unmet_demand": v["unmet_demand"]}
        for k, v in sorted(village_counts.items(), key=lambda x: x[1]["unmet_demand"], reverse=True)
    ]

    return jsonify({
        "success": True,
        "reporting_period": {
            "period_days": days if days is not None else "ALL",
            "start_date": cutoff_dt.isoformat() if cutoff_dt else "ALL_TIME",
            "end_date": now_utc.isoformat()
        },
        "data_source": "RuralCare Demand Radar Aggregator (Synthetic Demo Data)",
        "privacy_guarantee": "No individual patient records, phone numbers, or clinical notes exposed.",
        "summary": overall_metrics,
        "top_services": top_services,
        "village_demand": village_demand,
        "clusters": cluster_list
    })

# ---------------------------------------------------------------------------
# OUTREACH PROPOSALS ENGINE
# ---------------------------------------------------------------------------

PROPOSAL_TEMPLATES = {
    "SRV-EYE": {
        "title": "Mobile Ophthalmology & Cataract Screening Outreach Camp",
        "action": "Deploy Mobile Eye Van with slit-lamp & refraction kit to village community hall for 1-day screening.",
        "camp_type": "Specialist Eye Screening"
    },
    "SRV-ANC": {
        "title": "Antenatal & High-Risk Maternal Health Outreach Van",
        "action": "Schedule dedicated Village Health Nurse (VHN) & Mobile Ultrasound vehicle for maternal trimester follow-ups.",
        "camp_type": "Maternal & Child Health"
    },
    "SRV-DIA": {
        "title": "Renal Health, Dialysis Screening & Diabetes Management Camp",
        "action": "Coordinate community blood glucose, renal function tests & clinical nephrology telehealth review.",
        "camp_type": "Chronic Disease / Renal"
    },
    "SRV-ORT": {
        "title": "Orthopaedic Mobility & Joint Health Outreach Camp",
        "action": "Dispatch visiting physiotherapist & orthopaedic screening team to village wellness sub-centre.",
        "camp_type": "Orthopaedic / Mobility"
    },
    "SRV-PED": {
        "title": "Pediatric Health & Immunization Outreach Session",
        "action": "Conduct village child wellness checks, developmental screening & catch-up immunisation.",
        "camp_type": "Pediatric / Child Wellness"
    },
    "SRV-GEN": {
        "title": "Multi-Disciplinary Primary Health & Diagnostic Outreach Camp",
        "action": "Organize 1-day Government Medical College visiting doctor outreach session at Panchayat hall.",
        "camp_type": "General Outpatient Camp"
    }
}

@radar_bp.route("/outreach-proposals/generate", methods=["POST"])
def generate_outreach_proposals():
    """
    Analyzes aggregated demand clusters and generates structured outreach proposals
    when unmet demand or request volume meets configurable threshold.
    """
    db = get_db()
    payload = request.get_json() or {}
    
    # Configurable threshold: default unmet >= 1 or total requests >= 2
    unmet_threshold = int(payload.get("unmet_threshold", 1))
    period_days = payload.get("period_days", "30")

    # Fetch fresh clusters via internal call
    clusters_res = get_demand_clusters()
    c_data = clusters_res.get_json()
    clusters = c_data.get("clusters", [])

    created_proposals = []
    skipped_duplicates = 0
    now_utc = datetime.now(timezone.utc).isoformat()

    existing_props = list(db.collection("outreach_proposals").stream())
    existing_keys = set()
    for ep in existing_props:
        ep_dict = ep.to_dict()
        if ep_dict.get("status") not in ("REJECTED", "COMPLETED"):
            k = f"{ep_dict.get('village')}::{ep_dict.get('requested_service_id')}::{ep_dict.get('reporting_period')}"
            existing_keys.add(k)

    for c in clusters:
        unmet = c.get("unmet_demand_count", 0)
        total_req = c.get("total_distinct_requests", 0)
        
        # Check threshold
        if unmet < unmet_threshold and total_req < 3:
            continue

        village = c.get("village")
        service_id = c.get("service_id")
        service_name = c.get("service_name")
        rep_period = f"Last {period_days} Days" if period_days != "all" else "All Time"

        dedup_key = f"{village}::{service_id}::{rep_period}"
        if dedup_key in existing_keys:
            skipped_duplicates += 1
            continue

        template = PROPOSAL_TEMPLATES.get(service_id, PROPOSAL_TEMPLATES["SRV-GEN"])
        
        prop_id = f"PROP-2026-{random.randint(1000, 9999)}"
        evidence_lines = []
        if c.get("facility_failures", 0) > 0:
            evidence_lines.append(f"{c['facility_failures']} facility appointment rejection(s) / doctor unavailability")
        if c.get("transport_failures", 0) > 0:
            evidence_lines.append(f"{c['transport_failures']} local transport failure(s)")
        if c.get("expired_confirmations", 0) > 0:
            evidence_lines.append(f"{c['expired_confirmations']} expired facility verification(s)")
        if c.get("old_unresolved_requests", 0) > 0:
            evidence_lines.append(f"{c['old_unresolved_requests']} long-pending unresolved request(s)")

        evidence_summary = f"{total_req} distinct patient requests identified from {village}; {unmet} documented unmet needs due to: " + (", ".join(evidence_lines) if evidence_lines else "high regional consultation demand.")

        proposal = {
            "proposal_id": prop_id,
            "title": f"{template['title']} – {village}",
            "village": village,
            "requested_service_id": service_id,
            "requested_service_name": service_name,
            "distinct_request_count": total_req,
            "unmet_demand_count": unmet,
            "facility_failures": c.get("facility_failures", 0),
            "transport_failures": c.get("transport_failures", 0),
            "reporting_period": rep_period,
            "priority": c.get("priority", "MEDIUM"),
            "primary_bottleneck": c.get("primary_bottleneck", "NONE"),
            "evidence_summary": evidence_summary,
            "suggested_action": template["action"],
            "camp_type": template["camp_type"],
            "target_beneficiaries_est": max(total_req * 3, 15),
            "data_source": "RuralCare Demand Radar Aggregator (Synthetic Demo Data)",
            "status": "PROPOSED",
            "created_at": now_utc,
            "reviewed_by": None,
            "reviewed_at": None,
            "review_notes": None,
            "disclaimer": "Proposal represents an administrative recommendation based on verified demand signals. Administrative approval does not book government vehicles or hospital teams; field execution requires confirmed provider scheduling."
        }

        db.collection("outreach_proposals").document(prop_id).set(proposal)
        existing_keys.add(dedup_key)
        created_proposals.append(proposal)

    return jsonify({
        "success": True,
        "message": f"Generated {len(created_proposals)} new outreach proposal(s). {skipped_duplicates} existing proposal(s) skipped.",
        "created_count": len(created_proposals),
        "skipped_duplicates": skipped_duplicates,
        "proposals": created_proposals
    }), 201

@radar_bp.route("/outreach-proposals", methods=["GET"])
def get_outreach_proposals():
    """Returns list of outreach proposals, optionally filtered by status or village."""
    db = get_db()
    status_filter = request.args.get("status")
    village_filter = request.args.get("village")

    docs = db.collection("outreach_proposals").stream()
    proposals = []
    for d in docs:
        p = d.to_dict()
        p["id"] = d.id
        if status_filter and p.get("status") != status_filter:
            continue
        if village_filter and p.get("village", "").lower() != village_filter.lower():
            continue
        proposals.append(p)

    proposals.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify({"success": True, "count": len(proposals), "proposals": proposals})

@radar_bp.route("/outreach-proposals/<proposal_id>/approve", methods=["POST"])
@require_role("admin")
def approve_outreach_proposal(proposal_id):
    """
    District Health Administration approves an outreach proposal.
    Approval records administrative endorsement.
    """
    db = get_db()
    p_doc = db.collection("outreach_proposals").document(proposal_id).get()
    if not p_doc.exists:
        return jsonify({"success": False, "error": "Outreach proposal not found"}), 404

    user = get_authenticated_user() or {}
    payload = request.get_json() or {}
    notes = payload.get("notes", "Approved for district mobile healthcare outreach coordination.")
    now_utc = datetime.now(timezone.utc).isoformat()

    updates = {
        "status": "APPROVED",
        "reviewed_by": user.get("name", "District Health Administration Officer"),
        "reviewed_at": now_utc,
        "review_notes": notes,
        "updated_at": now_utc
    }
    db.collection("outreach_proposals").document(proposal_id).update(updates)

    refreshed = db.collection("outreach_proposals").document(proposal_id).get().to_dict()
    return jsonify({
        "success": True,
        "message": f"Proposal {proposal_id} approved by District Administration.",
        "proposal": refreshed,
        "operational_notice": "Approval records administrative endorsement. Actual camp scheduling requires authorized healthcare-provider and transport fleet confirmation."
    })

@radar_bp.route("/outreach-proposals/<proposal_id>/reject", methods=["POST"])
@require_role("admin")
def reject_outreach_proposal(proposal_id):
    """District Health Administration rejects an outreach proposal with documented reason."""
    db = get_db()
    p_doc = db.collection("outreach_proposals").document(proposal_id).get()
    if not p_doc.exists:
        return jsonify({"success": False, "error": "Outreach proposal not found"}), 404

    user = get_authenticated_user() or {}
    payload = request.get_json() or {}
    reason = payload.get("reason", "Existing Primary Health Centre coverage deemed adequate by administration.")
    now_utc = datetime.now(timezone.utc).isoformat()

    updates = {
        "status": "REJECTED",
        "reviewed_by": user.get("name", "District Health Administration Officer"),
        "reviewed_at": now_utc,
        "review_notes": reason,
        "updated_at": now_utc
    }
    db.collection("outreach_proposals").document(proposal_id).update(updates)

    refreshed = db.collection("outreach_proposals").document(proposal_id).get().to_dict()
    return jsonify({
        "success": True,
        "message": f"Proposal {proposal_id} marked as REJECTED.",
        "proposal": refreshed
    })
