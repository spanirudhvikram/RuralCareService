import os
from flask import Flask, render_template, send_from_directory, jsonify
from config import Config
from database import init_db, is_using_firebase

# Initialize database
init_db()

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config.from_object(Config)

# Register API blueprints
from api.auth import auth_bp
from api.facilities import facilities_bp
from api.journeys import journeys_bp
from api.readiness import readiness_bp
from api.transport import transport_bp
from api.admin import admin_bp
from api.sync import sync_bp
from api.radar import radar_bp
from api.patients import patients_bp
from api.prescriptions import prescriptions_bp
from api.tts import tts_bp
from api.stt import stt_bp

app.register_blueprint(auth_bp)
app.register_blueprint(facilities_bp)
app.register_blueprint(journeys_bp)
app.register_blueprint(readiness_bp)
app.register_blueprint(transport_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(sync_bp)
app.register_blueprint(radar_bp)
app.register_blueprint(patients_bp)
app.register_blueprint(prescriptions_bp)
app.register_blueprint(tts_bp)
app.register_blueprint(stt_bp)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/manifest.json")
def manifest():
    return send_from_directory(app.static_folder, "manifest.json", mimetype="application/manifest+json")

@app.route("/service-worker.js")
def service_worker():
    response = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response

@app.route("/api/emergency")
def emergency_info():
    """Official emergency dispatch contacts and medical disclaimer."""
    return jsonify({
        "success": True,
        "disclaimer": "RuralCare is a non-emergency planned outpatient coordination service. RuralCare is NOT an emergency response or 911/108 dispatch system. For acute chest pain, trauma, unconsciousness or heavy bleeding, call 108 immediately.",
        "contacts": [
            {
                "number": "108",
                "title": "Ambulance Emergency Service (Tamil Nadu / EMRI)",
                "description": "Free 24x7 government emergency medical transport for critical emergencies, trauma and maternal labour.",
                "color": "#DC2626"
            },
            {
                "number": "112",
                "title": "National Emergency All-in-One Helpline",
                "description": "Unified emergency response support system for police, fire, and medical crises.",
                "color": "#B91C1C"
            },
            {
                "number": "104",
                "title": "Health Advice & Medical Counselling Helpline",
                "description": "24x7 state healthcare assistance, medical guidance and mental wellbeing support.",
                "color": "#0284C7"
            }
        ]
    })

@app.route("/api/health")
def health_check():
    return jsonify({
        "status": "healthy",
        "service": "RuralCare - Verified Care Journey",
        "database": "firebase_firestore" if is_using_firebase() else "persistent_document_store",
        "emergency_dispatch_notice": "For acute emergencies dial 108 / 112"
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5005))
    print(f"RuralCare starting on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=Config.DEBUG)
