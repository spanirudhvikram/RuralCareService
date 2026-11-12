import os
import hashlib
import asyncio
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, current_app
import xml.sax.saxutils as saxutils

tts_bp = Blueprint("tts", __name__, url_prefix="/api/tts")

# Directory for caching synthesized audio
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "tts_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Azure Cognitive Services Speech credentials (optional)
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "").strip()
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "eastus").strip()

# Available Azure Neural Voices
SUPPORTED_VOICES = {
    "ta-IN-PallaviNeural": {
        "name": "ta-IN-PallaviNeural",
        "gender": "Female",
        "locale": "ta-IN",
        "display_name": "Pallavi (Azure Neural Tamil Female - Primary)",
        "is_primary": True
    },
    "ta-IN-ValluvarNeural": {
        "name": "ta-IN-ValluvarNeural",
        "gender": "Male",
        "locale": "ta-IN",
        "display_name": "Valluvar (Azure Neural Tamil Male - Alternative)",
        "is_primary": False
    },
    "en-IN-NeerjaNeural": {
        "name": "en-IN-NeerjaNeural",
        "gender": "Female",
        "locale": "en-IN",
        "display_name": "Neerja (Azure Neural English Female - Primary)",
        "is_primary": True
    },
    "en-IN-PrabhatNeural": {
        "name": "en-IN-PrabhatNeural",
        "gender": "Male",
        "locale": "en-IN",
        "display_name": "Prabhat (Azure Neural English Male - Alternative)",
        "is_primary": False
    }
}
DEFAULT_VOICE = "ta-IN-PallaviNeural"
DEFAULT_ENGLISH_VOICE = "en-IN-NeerjaNeural"

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False


def get_cache_key(text: str, voice: str, rate: str = "+0%") -> str:
    """Generate deterministic MD5 cache key for text and voice settings."""
    payload = f"{voice}|{rate}|{text.strip()}".encode("utf-8")
    return hashlib.md5(payload).hexdigest()


def synthesize_azure_rest(text: str, voice: str, output_path: Path) -> bool:
    """Synthesize speech using official Azure Cognitive Services Speech REST API."""
    if not AZURE_SPEECH_KEY:
        return False
    try:
        import requests
        url = f"https://{AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-16khz-128kbitrate-mono-mp3",
            "User-Agent": "RuralCare-TTS"
        }
        escaped_text = saxutils.escape(text)
        voice_info = SUPPORTED_VOICES.get(voice, {})
        gender = voice_info.get("gender", "Female")
        locale = voice_info.get("locale", "ta-IN")
        ssml = f"""<speak version='1.0' xml:lang='{locale}'>
            <voice xml:lang='{locale}' xml:gender='{gender}' name='{voice}'>
                {escaped_text}
            </voice>
        </speak>"""
        res = requests.post(url, headers=headers, data=ssml.encode("utf-8"), timeout=15)
        if res.status_code == 200 and len(res.content) > 500:
            output_path.write_bytes(res.content)
            return True
        else:
            print(f"[AzureTTS] REST API returned status {res.status_code}: {res.text[:200]}")
            return False
    except Exception as e:
        print(f"[AzureTTS] REST API exception: {e}")
        return False


def synthesize_edge(text: str, voice: str, output_path: Path, rate: str = "+0%") -> bool:
    """Synthesize speech using Azure Neural TTS via edge-tts."""
    if not EDGE_TTS_AVAILABLE:
        print("[AzureTTS] edge_tts package is not installed.")
        return False
    try:
        async def _synthesize():
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(str(output_path))
        
        asyncio.run(_synthesize())
        return output_path.exists() and output_path.stat().st_size > 500
    except Exception as e:
        print(f"[AzureTTS] edge-tts error with voice {voice}: {e}")
        return False


def synthesize_speech(text: str, voice: str = DEFAULT_VOICE, rate: str = "+0%") -> Path:
    """
    Synthesize speech for given text, caching the result.
    Tries Azure REST API first if configured, then edge-tts, with fallback voice.
    """
    if voice not in SUPPORTED_VOICES:
        voice = DEFAULT_VOICE
        
    cache_key = get_cache_key(text, voice, rate)
    cached_file = CACHE_DIR / f"{cache_key}.mp3"
    
    # Return cached audio if valid
    if cached_file.exists() and cached_file.stat().st_size > 500:
        return cached_file
        
    tmp_file = CACHE_DIR / f"tmp_{cache_key}.mp3"
    success = False
    
    # Attempt 1: Official Azure Speech REST API if key exists
    if AZURE_SPEECH_KEY:
        success = synthesize_azure_rest(text, voice, tmp_file)
        
    # Attempt 2: Azure Neural edge-tts
    if not success:
        success = synthesize_edge(text, voice, tmp_file, rate=rate)
        
    # Attempt 3: Fallback voice if primary voice failed
    if not success and voice == DEFAULT_VOICE:
        alt_voice = "ta-IN-ValluvarNeural"
        print(f"[AzureTTS] Trying fallback voice: {alt_voice}")
        if AZURE_SPEECH_KEY:
            success = synthesize_azure_rest(text, alt_voice, tmp_file)
        if not success:
            success = synthesize_edge(text, alt_voice, tmp_file, rate=rate)
            
    if success and tmp_file.exists():
        tmp_file.replace(cached_file)
        return cached_file
    else:
        if tmp_file.exists():
            tmp_file.unlink()
        raise RuntimeError(f"Azure Neural TTS synthesis failed for voice '{voice}' and text '{text[:40]}...'")


@tts_bp.route("/voices", methods=["GET"])
def get_voices():
    """Return list of supported Azure Neural Tamil voices."""
    return jsonify({
        "success": True,
        "default_voice": DEFAULT_VOICE,
        "voices": list(SUPPORTED_VOICES.values()),
        "azure_api_configured": bool(AZURE_SPEECH_KEY)
    })


@tts_bp.route("/synthesize", methods=["GET", "POST"])
def synthesize_endpoint():
    """
    Synthesize Tamil text to Azure Neural MP3 audio.
    Accepts text and voice parameters via GET query params or POST JSON.
    Returns audio/mpeg directly, or JSON metadata if requested.
    """
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form.to_dict() or {}
        text = data.get("text", "").strip()
        voice = data.get("voice", DEFAULT_VOICE).strip()
        rate = data.get("rate", "+0%").strip()
        as_json = data.get("json", False) or request.args.get("json") == "1"
    else:
        text = request.args.get("text", "").strip()
        voice = request.args.get("voice", DEFAULT_VOICE).strip()
        rate = request.args.get("rate", "+0%").strip()
        as_json = request.args.get("json") == "1"

    if not text:
        return jsonify({
            "success": False,
            "error": "Missing 'text' parameter for speech synthesis."
        }), 400

    if voice not in SUPPORTED_VOICES:
        voice = DEFAULT_VOICE

    try:
        audio_file = synthesize_speech(text, voice, rate)
        
        if as_json or request.headers.get("Accept") == "application/json":
            return jsonify({
                "success": True,
                "voice": voice,
                "audio_url": f"/api/tts/audio/{audio_file.name}",
                "size_bytes": audio_file.stat().st_size,
                "cached": True
            })
            
        response = send_file(
            audio_file,
            mimetype="audio/mpeg",
            as_attachment=False,
            download_name=f"tamil_tts_{audio_file.stem}.mp3"
        )
        response.headers["X-TTS-Voice"] = voice
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response

    except Exception as e:
        print(f"[AzureTTS] Synthesis error: {e}")
        return jsonify({
            "success": False,
            "error": f"Tamil speech synthesis failed: {str(e)}",
            "voice": voice
        }), 500


@tts_bp.route("/audio/<filename>", methods=["GET"])
def serve_audio_cache(filename: str):
    """Serve cached audio files."""
    # Sanitize filename
    safe_name = Path(filename).name
    target = CACHE_DIR / safe_name
    if not target.exists() or not target.is_file():
        return jsonify({"success": False, "error": "Audio file not found"}), 404
    return send_file(target, mimetype="audio/mpeg")
