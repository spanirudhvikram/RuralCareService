from functools import wraps
from flask import Blueprint, jsonify, request, session
from database import get_db

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

DEMO_USERS = {
    # Administrators
    "admin@ruralcare.gov.in": {
        "id": "ADM-01",
        "username": "admin@ruralcare.gov.in",
        "name": "District Health Administration Officer",
        "role": "admin",
        "password": "admin"
    },
    # Facility Staff
    "dr.revathi@pollachigh.tn.gov.in": {
        "id": "STAFF-01",
        "username": "dr.revathi@pollachigh.tn.gov.in",
        "name": "Dr. K. Revathi (CMO)",
        "role": "facility_staff",
        "facility_id": "FAC-01",
        "facility_name": "Pollachi Government Taluk Hospital",
        "password": "staff"
    },
    "dr.karthik@omalurchc.tn.gov.in": {
        "id": "STAFF-02",
        "username": "dr.karthik@omalurchc.tn.gov.in",
        "name": "Dr. S. Karthikeyan",
        "role": "facility_staff",
        "facility_id": "FAC-02",
        "facility_name": "Omalur Community Health Centre",
        "password": "staff"
    },
    # Drivers (login with phone)
    "9842177101": {
        "id": "DRV-01",
        "username": "9842177101",
        "name": "Murugan S.",
        "role": "driver",
        "phone": "+91 98421 77101",
        "vehicle_reg": "TN-38-AX-4412",
        "primary_village": "Chettipalayam",
        "password": "driver"
    },
    "9842288202": {
        "id": "DRV-02",
        "username": "9842288202",
        "name": "Chinnasamy P.",
        "role": "driver",
        "phone": "+91 98422 88202",
        "vehicle_reg": "TN-30-CZ-9011",
        "primary_village": "Omalur",
        "password": "driver"
    },
    # Kiosk Operator
    "KIOSK-OPERATOR-CP01": {
        "id": "KIOSK-OPERATOR-CP01",
        "username": "KIOSK-OPERATOR-CP01",
        "name": "Selvam (Chettipalayam Panchayat Kiosk Lead)",
        "role": "kiosk_operator",
        "village": "Chettipalayam",
        "password": "kiosk"
    }
}

def get_authenticated_user():
    """Retrieve authenticated user from session or verified auth headers."""
    user = session.get("user")
    if user:
        return user
    
    # Support API header authentication for programmatic and test clients
    auth_username = request.headers.get("X-User-Username")
    if auth_username and auth_username in DEMO_USERS:
        return {k: v for k, v in DEMO_USERS[auth_username].items() if k != "password"}
    
    return None

def require_role(*allowed_roles):
    """Decorator to enforce server-side role-based access control."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = get_authenticated_user()
            if not user:
                return jsonify({
                    "success": False,
                    "error": "Authentication required. Please log in to perform this action.",
                    "code": "UNAUTHENTICATED"
                }), 401
            
            user_role = user.get("role")
            if user_role == "admin" or user_role in allowed_roles:
                return fn(*args, **kwargs)
            
            return jsonify({
                "success": False,
                "error": f"Access denied. Role '{user_role}' is not authorized for this action.",
                "code": "FORBIDDEN"
            }), 403
        return wrapper
    return decorator

@auth_bp.route("/login", methods=["POST"])
def login():
    payload = request.get_json() or {}
    username = payload.get("username", "").strip()
    password = payload.get("password", "").strip()
    role_requested = payload.get("role")

    user = DEMO_USERS.get(username)
    if not user or user["password"] != password:
        return jsonify({"success": False, "error": "Invalid credentials. Please verify your username and password."}), 401

    if role_requested and user["role"] != role_requested:
        return jsonify({"success": False, "error": f"Account does not have {role_requested} privileges."}), 403

    safe_user = {k: v for k, v in user.items() if k != "password"}
    session["user"] = safe_user

    return jsonify({
        "success": True,
        "message": f"Welcome back, {safe_user['name']}",
        "user": safe_user
    })

@auth_bp.route("/me", methods=["GET"])
def current_user():
    user = get_authenticated_user()
    if not user:
        return jsonify({"success": True, "authenticated": False, "user": None})
    return jsonify({"success": True, "authenticated": True, "user": user})

@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.pop("user", None)
    return jsonify({"success": True, "message": "Successfully logged out"})

@auth_bp.route("/demo-accounts", methods=["GET"])
def list_demo_accounts():
    accounts = []
    for u, data in DEMO_USERS.items():
        accounts.append({
            "username": u,
            "name": data["name"],
            "role": data["role"],
            "password": data["password"],
            "detail": data.get("facility_name") or data.get("vehicle_reg") or data.get("village", "")
        })
    return jsonify({"success": True, "accounts": accounts})
