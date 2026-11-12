import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "ruralcare-secret-key-2026-production-ready")
    DEBUG = os.environ.get("FLASK_DEBUG", "True").lower() in ("true", "1")
    PORT = int(os.environ.get("PORT", 5005))
    
    # Firebase configuration
    _secrets_dir = BASE_DIR / "secrets"
    _default_creds = ""
    if _secrets_dir.exists():
        for _f in _secrets_dir.glob("*.json"):
            if "firebase" in _f.name.lower() or "adminsdk" in _f.name.lower() or "ruralcare" in _f.name.lower():
                _default_creds = str(_f.resolve())
                break

    FIREBASE_CREDENTIALS_PATH = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.environ.get("FIREBASE_CREDENTIALS") or _default_creds
    FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "ruralcare-f61e6")

    if FIREBASE_CREDENTIALS_PATH and os.path.exists(FIREBASE_CREDENTIALS_PATH):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = FIREBASE_CREDENTIALS_PATH
        os.environ["FIREBASE_CREDENTIALS"] = FIREBASE_CREDENTIALS_PATH
    
    # Persistent JSON storage fallback when live Firebase is not configured
    DATA_DIR = BASE_DIR / "data"
    DB_FILE = DATA_DIR / "ruralcare_store.json"
    
    # Verification settings
    VERIFICATION_VALIDITY_HOURS = 48  # Default service verification validity period
    
    # Emergency numbers
    EMERGENCY_AMBULANCE = "108"
    EMERGENCY_NATIONAL = "112"
