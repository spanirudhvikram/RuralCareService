/**
 * RuralCare Voice Interaction Module
 * Natural Tamil & English Voice Interaction Controller
 * Web Speech API (SpeechRecognition + SpeechSynthesis)
 * 
 * Features:
 * 1. Multi-Intent Spoken Healthcare Guide:
 *    - Main Spoken Menu
 *    - 1. Spoken Hospital Discovery & Recommendations
 *    - 2. Spoken Appointment & Care Journey Booking
 *    - 3. Spoken Transport Coordination
 *    - 4. Spoken Care Journey Tracking & Departure Readiness status
 * 2. Tamil-first Speech Recognition with 'ta-IN', 'en-IN', and Tamil+English Mixed mode.
 * 3. Live interim speech transcription display with real-time feedback.
 * 4. 7-state microphone state machine (READY, LISTENING, PROCESSING, TRANSCRIPTION READY, etc.).
 * 5. Strict Tamil voice selection guard (never speak Tamil with English voice).
 * 6. Sentence-chunked utterance queue to prevent browser 15s speech timeout/hang bug.
 * 7. Audio playback controls: Repeat, Pause, Resume, Stop.
 * 8. Read-back verification and zero auto-submission (patient confirmation required).
 * 9. Non-destructive Tamil Unicode preservation.
 */

const TAMIL_NUMBER_WORDS = {
  "ஒன்று": 1, "ஒன்னு": 1,
  "இரண்டு": 2, "ரெண்டு": 2,
  "மூன்று": 3, "மூணு": 3,
  "நான்கு": 4, "நாலு": 4,
  "ஐந்து": 5, "அஞ்சு": 5,
  "ஆறு": 6,
  "ஏழு": 7,
  "எட்டு": 8,
  "ஒன்பது": 9,
  "பத்து": 10,
  "இருபது": 20, "இருவது": 20,
  "முப்பது": 30,
  "நாற்பது": 40, "நாப்பது": 40,
  "ஐம்பது": 50, "அம்பது": 50,
  "அறுபது": 60,
  "எழுபது": 70,
  "எண்பது": 80, "எம்பது": 80,
  "தொண்ணூறு": 90,
  "நூறு": 100
};

/**
 * In-Browser 16kHz Linear PCM WAV Audio Recorder
 * Records pure uncompressed audio from the microphone via Web Audio API,
 * resamples to 16kHz mono, and exports 16-bit signed PCM WAV bytes.
 * Compatible with Chrome, Safari, Firefox, Edge, iOS, and Android.
 */
class WavAudioRecorder {
  constructor() {
    this.audioContext = null;
    this.mediaStream = null;
    this.scriptProcessor = null;
    this.pcmBuffers = [];
    this.isRecording = false;
    this.mediaRecorder = null;
    this.mediaRecorderChunks = [];
  }

  async start() {
    this.pcmBuffers = [];
    this.mediaRecorderChunks = [];
    this.isRecording = false;

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("getUserMedia is not supported in this browser.");
    }

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }
    });
    this.mediaStream = stream;

    // 1. Try Web Audio API ScriptProcessor for pure 16kHz PCM WAV
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) {
        this.audioContext = new AudioCtx();
        const source = this.audioContext.createMediaStreamSource(stream);
        const bufferSize = 4096;
        this.scriptProcessor = this.audioContext.createScriptProcessor(bufferSize, 1, 1);
        this.scriptProcessor.onaudioprocess = (e) => {
          if (!this.isRecording) return;
          const channelData = e.inputBuffer.getChannelData(0);
          this.pcmBuffers.push(new Float32Array(channelData));
        };
        source.connect(this.scriptProcessor);
        this.scriptProcessor.connect(this.audioContext.destination);
      }
    } catch (e) {
      console.warn("[WavAudioRecorder] AudioContext setup error, falling back to MediaRecorder:", e);
    }

    // 2. Also start MediaRecorder as robust fallback
    if (window.MediaRecorder) {
      try {
        const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
          ? "audio/webm;codecs=opus"
          : (MediaRecorder.isTypeSupported("audio/mp4") ? "audio/mp4" : "");
        this.mediaRecorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
        this.mediaRecorder.ondataavailable = (e) => {
          if (e.data && e.data.size > 0) this.mediaRecorderChunks.push(e.data);
        };
        this.mediaRecorder.start(250);
      } catch (e) {
        console.warn("[WavAudioRecorder] MediaRecorder init error:", e);
      }
    }

    this.isRecording = true;
  }

  async stop() {
    this.isRecording = false;

    // Stop MediaRecorder if running
    if (this.mediaRecorder && this.mediaRecorder.state !== "inactive") {
      try { this.mediaRecorder.stop(); } catch (e) {}
    }

    // Stop microphone tracks
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach(track => track.stop());
      this.mediaStream = null;
    }

    // Disconnect script processor
    if (this.scriptProcessor) {
      try { this.scriptProcessor.disconnect(); } catch (e) {}
      this.scriptProcessor = null;
    }

    // If PCM buffers captured, export standard 16kHz mono 16-bit WAV
    if (this.audioContext && this.pcmBuffers.length > 0) {
      const inputRate = this.audioContext.sampleRate || 44100;
      try {
        await this.audioContext.close();
      } catch (e) {}
      this.audioContext = null;
      return this.exportWavBlob(inputRate, 16000);
    }

    // Fallback: Return MediaRecorder Blob
    if (this.mediaRecorderChunks.length > 0) {
      const mime = (this.mediaRecorder && this.mediaRecorder.mimeType) || "audio/webm";
      return new Blob(this.mediaRecorderChunks, { type: mime });
    }

    return null;
  }

  exportWavBlob(inputSampleRate, targetSampleRate = 16000) {
    let totalSamples = 0;
    for (let i = 0; i < this.pcmBuffers.length; i++) {
      totalSamples += this.pcmBuffers[i].length;
    }
    const merged = new Float32Array(totalSamples);
    let offset = 0;
    for (let i = 0; i < this.pcmBuffers.length; i++) {
      merged.set(this.pcmBuffers[i], offset);
      offset += this.pcmBuffers[i].length;
    }

    // Resample to targetSampleRate (16kHz)
    let resampled = merged;
    if (inputSampleRate !== targetSampleRate) {
      const ratio = inputSampleRate / targetSampleRate;
      const newLength = Math.round(merged.length / ratio);
      resampled = new Float32Array(newLength);
      for (let i = 0; i < newLength; i++) {
        const srcIdx = i * ratio;
        const low = Math.floor(srcIdx);
        const high = Math.min(low + 1, merged.length - 1);
        const weight = srcIdx - low;
        resampled[i] = merged[low] * (1 - weight) + merged[high] * weight;
      }
    }

    // Build 16-bit PCM Linear WAV ArrayBuffer
    const buffer = new ArrayBuffer(44 + resampled.length * 2);
    const view = new DataView(buffer);

    // RIFF chunk
    this.writeAscii(view, 0, "RIFF");
    view.setUint32(4, 36 + resampled.length * 2, true);
    this.writeAscii(view, 8, "WAVE");

    // fmt sub-chunk
    this.writeAscii(view, 12, "fmt ");
    view.setUint32(16, 16, true); // Subchunk1Size (16 for PCM)
    view.setUint16(20, 1, true); // AudioFormat (1 = Linear PCM)
    view.setUint16(22, 1, true); // NumChannels (1 = Mono)
    view.setUint32(24, targetSampleRate, true); // SampleRate (16000)
    view.setUint32(28, targetSampleRate * 2, true); // ByteRate (16000 * 1 * 2)
    view.setUint16(32, 2, true); // BlockAlign (1 * 2 bytes)
    view.setUint16(34, 16, true); // BitsPerSample (16 bits)

    // data sub-chunk
    this.writeAscii(view, 36, "data");
    view.setUint32(40, resampled.length * 2, true);

    // Write samples clamped to int16
    let byteOffset = 44;
    for (let i = 0; i < resampled.length; i++, byteOffset += 2) {
      const s = Math.max(-1, Math.min(1, resampled[i]));
      view.setInt16(byteOffset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    }

    return new Blob([view], { type: "audio/wav" });
  }

  writeAscii(view, offset, str) {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  }
}

class VoiceAssistant {
  constructor() {
    this.recognition = null;
    this.synth = window.speechSynthesis || null;
    this.isListening = false;
    this.isSpeaking = false;
    this.isPaused = false;
    this.utteranceQueue = [];
    this.lastSpokenText = "";
    this.lastSpokenLang = "ta";
    
    // Azure Neural TTS configuration (Primary: ta-IN-PallaviNeural, Alternative: ta-IN-ValluvarNeural)
    this.azureVoice = "ta-IN-PallaviNeural";
    this.currentAudio = null;
    
    // Neural STT & Audio Recording Controller
    this.audioRecorder = new WavAudioRecorder();
    this.pendingTranscription = null;
    this.lastWebSpeechTranscript = "";
    
    // Language mode: 'ta' (default for Tamil kiosk), 'en', or 'mixed'
    this.languageMode = "ta";

    // Conversational Flow Mode: 'menu', 'discovery', 'booking', 'tracking', 'transport'
    this.voiceFlow = "menu";
    this.currentStepIndex = 0;
    this.currentState = "READY"; // 7 states
    this.isAwaitingFinalConfirmation = false;
    this.pendingDiscoveryBooking = null;

    this.extractedData = {
      patient_name: "",
      age: "",
      contact_number: "",
      village: "",
      service_query: "",
      transport_required: false
    };

    // Human-friendly natural questions across language modes
    this.questions = {
      ta: [
        {
          id: "patient_name",
          spoken: "நோயாளியின் பெயர் என்ன?",
          display: "நோயாளியின் பெயரை தெளிவாகக் கூறவும்.",
          prompt_en: "What is the patient's name?"
        },
        {
          id: "age",
          spoken: "நோயாளியின் வயது எத்தனை ஆண்டுகள்?",
          display: "நோயாளியின் வயதைக் கூறவும் (எ.கா: அறுபது, 65).",
          prompt_en: "What is the patient's age in years?"
        },
        {
          id: "contact_number",
          spoken: "உங்கள் 10 இலக்க மொபைல் எண்ணை மெதுவாகக் கூறவும்.",
          display: "10 இலக்க தொலைபேசி எண்ணைக் கூறவும்.",
          prompt_en: "What is your 10-digit mobile number?"
        },
        {
          id: "village",
          spoken: "உங்கள் கிராமம் அல்லது இருப்பிடம் எது?",
          display: "உங்கள் கிராமத்தின் பெயரைக் கூறவும் (எ.கா: செட்டிபாளையம், ஓமலூர், மேலூர்).",
          prompt_en: "Which village do you live in?"
        },
        {
          id: "service_query",
          spoken: "உங்களுக்கு என்ன மருத்துவ சிகிச்சை அல்லது ஆலோசனை தேவை? பொது மருத்துவம், கண் சிகிச்சை, தாய் சேய் நலம் அல்லது டையாலிசிஸ்?",
          display: "தேவையான மருத்துவ சேவையைக் கூறவும்.",
          prompt_en: "What healthcare service is needed?"
        },
        {
          id: "transport_required",
          spoken: "மருத்துவமனைக்கு செல்ல வாகன உதவி தேவையா? ஆம் அல்லது இல்லை என்று கூறவும்.",
          display: "வாகன உதவி தேவையா? 'ஆம்' அல்லது 'இல்லை' என்று கூறவும்.",
          prompt_en: "Do you need transport assistance? Say yes or no."
        }
      ],
      en: [
        {
          id: "patient_name",
          spoken: "What is the patient's full name?",
          display: "Please speak the patient's full name clearly.",
          prompt_en: "What is the patient's full name?"
        },
        {
          id: "age",
          spoken: "What is the patient's age in years?",
          display: "Please speak the patient's age (e.g. 60, sixty).",
          prompt_en: "What is the patient's age in years?"
        },
        {
          id: "contact_number",
          spoken: "What is your 10 digit mobile phone number?",
          display: "Please speak your 10-digit contact number slowly.",
          prompt_en: "What is your 10-digit mobile number?"
        },
        {
          id: "village",
          spoken: "Which village or panchayat do you live in?",
          display: "Please speak your village name (e.g., Chettipalayam, Omalur, Melur).",
          prompt_en: "Which village do you live in?"
        },
        {
          id: "service_query",
          spoken: "What medical service is needed? General Medicine, Eye Care, Antenatal Care, or Dialysis?",
          display: "Please state the required healthcare service.",
          prompt_en: "What medical service is needed?"
        },
        {
          id: "transport_required",
          spoken: "Do you need vehicle transport assistance to reach the hospital? Please say Yes or No.",
          display: "Do you need transport? Please say 'Yes' or 'No'.",
          prompt_en: "Do you need transport assistance? Say yes or no."
        }
      ],
      mixed: [
        {
          id: "patient_name",
          spoken: "நோயாளியின் முழுப் பெயர் (Patient Name) என்ன?",
          display: "நோயாளியின் பெயரை தெளிவாகக் கூறவும் (Patient Name).",
          prompt_en: "What is the patient's name?"
        },
        {
          id: "age",
          spoken: "நோயாளியின் வயது எத்தனை ஆண்டுகள் (Age in years)?",
          display: "நோயாளியின் வயதைக் கூறவும் (எ.கா: 60, அறுபது).",
          prompt_en: "What is the patient's age?"
        },
        {
          id: "contact_number",
          spoken: "உங்கள் 10 இலக்க மொபைல் போன் நம்பரை மெதுவாகக் கூறவும்.",
          display: "10-digit contact mobile number.",
          prompt_en: "What is your 10-digit mobile number?"
        },
        {
          id: "village",
          spoken: "உங்கள் கிராமம் அல்லது Village பெயர் எது?",
          display: "உங்கள் ஊர் / கிராமத்தின் பெயர் (Village name).",
          prompt_en: "Which village do you live in?"
        },
        {
          id: "service_query",
          spoken: "உங்களுக்கு என்ன Medical Service தேவை? General Medicine, Eye Care, Dialysis அல்லது Maternity?",
          display: "தேவையான மருத்துவ சேவை (Healthcare service needed).",
          prompt_en: "What healthcare service is needed?"
        },
        {
          id: "transport_required",
          spoken: "மருத்துவமனை செல்ல Transport அல்லது ஆட்டோ உதவி தேவையா? Yes அல்லது No என்று கூறவும்.",
          display: "Transport needed? 'Yes' / 'ஆம்' அல்லது 'No' / 'இல்லை'.",
          prompt_en: "Do you need transport assistance?"
        }
      ]
    };

    this.initRecognition();
    this.initVoiceList();
  }

  isSupported() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    return !!SpeechRecognition;
  }

  initVoiceList() {
    const updateVoices = () => {
      if (this.synth) {
        this.voices = this.synth.getVoices();
      }
      const enVoice = this.getBestVoice("en-IN");
      console.log(`[VoiceAssistant] Available TTS Voices loaded. Primary Tamil Engine: Azure Neural (${this.azureVoice})`);
      this.updateDebugVoiceUI(this.languageMode === "en" ? enVoice : { name: this.azureVoice, lang: "ta-IN" }, this.languageMode === "en" ? "en-IN" : "ta-IN");
    };

    if (this.synth && this.synth.onvoiceschanged !== undefined) {
      this.synth.onvoiceschanged = updateVoices;
      this.synth.addEventListener("voiceschanged", updateVoices);
    }
    updateVoices();
  }

  getRecognitionLang() {
    if (this.languageMode === "en") return "en-IN";
    return "ta-IN";
  }

  setLanguageMode(mode) {
    if (!["ta", "en", "mixed"].includes(mode)) return;
    this.languageMode = mode;
    this.stopListening();
    this.stopSpeech();

    const activeLangCode = mode === "en" ? "en-IN" : "ta-IN";
    const voice = mode === "en" ? this.getBestVoice(activeLangCode) : { name: this.azureVoice, lang: "ta-IN" };
    this.updateDebugVoiceUI(voice, activeLangCode);

    // Update UI active buttons
    const modes = ["ta", "en", "mixed"];
    modes.forEach(m => {
      const btn = document.getElementById(`btn-voice-mode-${m}`);
      if (btn) {
        if (m === mode) btn.classList.add("active");
        else btn.classList.remove("active");
      }
    });

    // Re-render flow in newly selected language
    this.setVoiceFlow(this.voiceFlow);
  }

  // =========================================================================
  // MULTI-INTENT VOICE ROUTER (Discovery, Booking, Tracking, Transport)
  // =========================================================================
  setVoiceFlow(flow = "menu") {
    this.voiceFlow = flow;
    this.stopListening();
    this.stopSpeech();
    this.isAwaitingFinalConfirmation = false;

    // Update active pill
    const flows = ["menu", "discovery", "booking", "transport", "tracking"];
    flows.forEach(f => {
      const pill = document.getElementById(`btn-flow-${f}`);
      if (pill) {
        if (f === flow) pill.classList.add("active");
        else pill.classList.remove("active");
      }
    });

    // Update panel visibility
    const discoveryPanel = document.getElementById("voice-discovery-panel");
    const trackingPanel = document.getElementById("voice-tracking-panel");
    const bookingPanel = document.getElementById("voice-booking-panel");
    const readbackBanner = document.getElementById("voice-readback-banner");

    if (discoveryPanel) discoveryPanel.classList.toggle("hidden", flow !== "discovery");
    if (trackingPanel) trackingPanel.classList.toggle("hidden", flow !== "tracking");
    if (bookingPanel) bookingPanel.classList.toggle("hidden", flow === "discovery" || flow === "tracking");
    if (readbackBanner) readbackBanner.classList.add("hidden");

    const taskLabel = document.getElementById("voice-task-label");
    const promptText = document.getElementById("voice-prompt-text");
    const subText = document.getElementById("voice-prompt-subtext");
    const stepBadge = document.getElementById("voice-step-badge");

    const isTamil = this.languageMode === "ta";

    if (flow === "menu") {
      if (taskLabel) taskLabel.textContent = isTamil ? "குரல் முகப்பு (Main Menu)" : "Voice Assistant • Main Menu";
      if (stepBadge) stepBadge.textContent = "Ready";
      
      const welcomeSpoken = isTamil
        ? "வணக்கம்! RuralCare குரல் உதவிக்கு வரவேற்கிறோம். உங்களுக்கு என்ன உதவி வேண்டும்? 'மருத்துவமனை தேட', 'புதிய பதிவு செய்ய', அல்லது 'பயண நிலையை அறிய' என்று கூறவும்."
        : "Hello! Welcome to RuralCare Voice Assistant. How can we help you today? You can say: 'Find Hospital', 'Book Appointment', 'Request Transport', or 'Track Journey'.";

      if (promptText) promptText.textContent = isTamil ? "வணக்கம்! உங்களுக்கு என்ன உதவி வேண்டும்?" : "Hello! How can we help you today?";
      if (subText) subText.textContent = isTamil ? "'மருத்துவமனை தேட', 'புதிய பதிவு செய்ய', அல்லது 'பயண நிலையை அறிய' என்று கூறவும்." : "Say: 'Find Hospital', 'Book Appointment', 'Request Transport', or 'Track Journey'.";

      this.speakText(welcomeSpoken, () => {
        this.listen();
      });

    } else if (flow === "discovery") {
      if (taskLabel) taskLabel.textContent = isTamil ? "1. மருத்துவமனை தேடல் (Discovery)" : "1. Hospital Discovery";
      if (stepBadge) stepBadge.textContent = "Discovery";

      const promptSpoken = isTamil
        ? "உங்களுக்கு என்ன மருத்துவ சிகிச்சை அல்லது ஆலோசனை தேவை? பொது மருத்துவம், கண் சிகிச்சை, தாய் சேய் நலம் அல்லது டையாலிசிஸ்?"
        : "What medical service is needed? General Medicine, Eye Care, Antenatal Care, or Dialysis?";

      if (promptText) promptText.textContent = isTamil ? "உங்களுக்கு என்ன மருத்துவ சேவை தேவை?" : "What medical service is needed?";
      if (subText) subText.textContent = isTamil ? "பொது மருத்துவம், கண் சிகிச்சை, தாய் சேய் நலம், அல்லது டையாலிசிஸ் என்று கூறவும்." : "Please speak: General Medicine, Eye Care, Maternity, or Dialysis.";

      this.speakText(promptSpoken, () => {
        this.listen();
      });

    } else if (flow === "tracking") {
      if (taskLabel) taskLabel.textContent = isTamil ? "4. பயண நிலை அறிதல் (Tracking)" : "4. Track Care Journey";
      if (stepBadge) stepBadge.textContent = "Tracking";

      const promptSpoken = isTamil
        ? "உங்கள் 10 இலக்க மொபைல் எண் அல்லது பயண அடையாள எண்ணை மெதுவாகக் கூறவும்."
        : "Please speak your 10-digit contact phone number or Care Journey ID.";

      if (promptText) promptText.textContent = isTamil ? "பயண நிலை அறிய எண் கூறவும்:" : "Speak Phone or Care Journey ID:";
      if (subText) subText.textContent = isTamil ? "உங்கள் 10 இலக்க மொபைல் எண் (எ.கா: 9842100001) அல்லது RC-2026-..." : "Speak 10-digit mobile number or RC-2026-... ID.";

      this.speakText(promptSpoken, () => {
        this.listen();
      });

    } else if (flow === "transport") {
      if (taskLabel) taskLabel.textContent = isTamil ? "3. வாகன உதவி (Transport)" : "3. Arrange Transport";
      if (stepBadge) stepBadge.textContent = "Transport";

      const transCheckbox = document.getElementById("voice-review-transport_required");
      if (transCheckbox) transCheckbox.checked = true;
      this.extractedData.transport_required = true;

      // Switch to booking flow to gather patient details
      this.currentStepIndex = 0;
      this.askCurrentQuestion();

    } else if (flow === "booking") {
      if (taskLabel) taskLabel.textContent = isTamil ? "2. மருத்துவ ஆலோசனை பதிவு (Booking)" : "2. Book Care Journey";
      this.currentStepIndex = 0;
      this.askCurrentQuestion();
    }
  }

  // =========================================================================
  // RECOGNITION CONTROLLER
  // =========================================================================
  initRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      console.warn("[VoiceAssistant] Web Speech API not supported in this browser. Touch fallback active.");
      this.setMicrophoneState("VOICE INPUT NOT SUPPORTED");
      return;
    }

    try {
      this.recognition = new SpeechRecognition();
      this.recognition.continuous = false;
      this.recognition.interimResults = true;
      this.recognition.maxAlternatives = 1;

      this.recognition.onstart = () => {
        this.isListening = true;
        this.setMicrophoneState("LISTENING");
        this.showInterimFeedback("", true);
      };

      this.recognition.onresult = (event) => {
        let interimTranscript = "";
        let finalTranscript = "";

        for (let i = event.resultIndex; i < event.results.length; ++i) {
          const item = event.results[i];
          const text = item[0].transcript;
          if (item.isFinal) {
            finalTranscript += text;
          } else {
            interimTranscript += text;
          }
        }

        if (interimTranscript) {
          this.showInterimFeedback(interimTranscript, true);
        }

        if (finalTranscript) {
          this.lastWebSpeechTranscript = finalTranscript.trim();
          this.showInterimFeedback(finalTranscript.trim(), false);
        }
      };

      this.recognition.onerror = (event) => {
        this.isListening = false;
        console.warn("[VoiceAssistant] Speech recognition error:", event.error);

        if (event.error === "not-allowed" || event.error === "service-not-allowed") {
          this.setMicrophoneState("MICROPHONE PERMISSION REQUIRED");
          const msg = this.languageMode === "ta"
            ? "மைக் அணுகல் மறுக்கப்பட்டது. உலாவியில் மைக் அனுமதியை வழங்கவும் அல்லது தொடுதிரை படிவத்தைப் பயன்படுத்தவும்."
            : "Microphone permission required. Please allow microphone access in your browser or switch to the touchscreen form.";
          this.showFallbackMessage(msg);
        } else if (event.error === "network") {
          this.setMicrophoneState("NETWORK UNAVAILABLE");
          const msg = this.languageMode === "ta"
            ? "இணைய இணைப்பு இல்லை (Offline). குரல் பதிவு இணையத்தை சார்ந்தது. ஆஃப்லைன் தொடுதிரை படிவத்தைப் பயன்படுத்தவும்."
            : "Network unavailable (Offline). Voice recognition requires connectivity. Please use the offline-ready touchscreen form.";
          this.showFallbackMessage(msg);
        } else if (event.error === "no-speech") {
          this.setMicrophoneState("READY");
          const msg = this.languageMode === "ta"
            ? "சத்தம் கேட்கவில்லை. மைக் பொத்தானை அழுத்தி மீண்டும் பேசவும்."
            : "No speech detected. Please tap the microphone and speak again.";
          this.showInterimFeedback(msg, false);
        } else {
          this.setMicrophoneState("READY");
        }
      };

      this.recognition.onend = () => {
        this.isListening = false;
        if (this.currentState === "LISTENING") {
          this.setMicrophoneState("READY");
        }
      };
    } catch (e) {
      console.warn("[VoiceAssistant] Recognition init exception:", e);
      this.setMicrophoneState("VOICE INPUT NOT SUPPORTED");
    }
  }

  setMicrophoneState(state) {
    this.currentState = state;
    const statusEl = document.getElementById("voice-status-pill");
    const micBtn = document.getElementById("voice-mic-icon");
    if (!statusEl) return;

    statusEl.className = "voice-indicator";
    const isTamil = this.languageMode === "ta";

    switch (state) {
      case "READY":
        statusEl.classList.add("idle");
        statusEl.textContent = isTamil ? "தயாராக உள்ளது" : "READY";
        if (micBtn) {
          micBtn.classList.remove("pulse-listening", "pulse-speaking");
          micBtn.setAttribute("aria-label", "Start Microphone");
        }
        break;

      case "LISTENING":
        statusEl.classList.add("listening");
        statusEl.textContent = isTamil ? "கேட்கிறது... (LISTENING)" : "LISTENING...";
        if (micBtn) {
          micBtn.classList.remove("pulse-speaking");
          micBtn.classList.add("pulse-listening");
          micBtn.setAttribute("aria-label", "Listening... Speak now");
        }
        break;

      case "PROCESSING":
        statusEl.classList.add("processing");
        statusEl.textContent = isTamil ? "செயலாக்குகிறது..." : "PROCESSING...";
        if (micBtn) micBtn.classList.remove("pulse-listening", "pulse-speaking");
        break;

      case "TRANSCRIPTION READY":
        statusEl.classList.add("completed");
        statusEl.textContent = isTamil ? "உரை பெறப்பட்டது" : "TRANSCRIPTION READY";
        if (micBtn) micBtn.classList.remove("pulse-listening", "pulse-speaking");
        break;

      case "MICROPHONE PERMISSION REQUIRED":
        statusEl.classList.add("error");
        statusEl.textContent = isTamil ? "மைக் அனுமதி தேவை" : "MICROPHONE PERMISSION REQUIRED";
        if (micBtn) micBtn.classList.remove("pulse-listening", "pulse-speaking");
        break;

      case "NETWORK UNAVAILABLE":
        statusEl.classList.add("offline");
        statusEl.textContent = isTamil ? "இணைய இணைப்பு இல்லை (Offline)" : "NETWORK UNAVAILABLE";
        if (micBtn) micBtn.classList.remove("pulse-listening", "pulse-speaking");
        break;

      case "VOICE INPUT NOT SUPPORTED":
        statusEl.classList.add("error");
        statusEl.textContent = isTamil ? "குரல் பதிவு ஆதரிக்கப்படவில்லை" : "VOICE INPUT NOT SUPPORTED";
        if (micBtn) micBtn.classList.remove("pulse-listening", "pulse-speaking");
        break;

      default:
        statusEl.classList.add("idle");
        statusEl.textContent = "READY";
        if (micBtn) micBtn.classList.remove("pulse-listening", "pulse-speaking");
    }
  }

  showInterimFeedback(text, isInterim) {
    const box = document.getElementById("voice-interim-box");
    if (!box) return;

    if (!text) {
      const defaultHint = this.languageMode === "ta"
        ? "மைக் பொத்தானை அழுத்திப் பேசவும்... நீங்கள் பேசுவது உடனுக்குடன் இங்கே தெரியும்."
        : "Press the microphone and speak... Recognized words will appear live here.";
      box.innerHTML = `<span class="interim-idle-hint">${defaultHint}</span>`;
      return;
    }

    if (isInterim) {
      box.innerHTML = `<span class="interim-live-tag">🎙️ ${this.languageMode === 'ta' ? 'கேட்கிறது:' : 'Hearing:'}</span> <span class="interim-text-live">${this.escapeHtml(text)}</span>`;
    } else {
      box.innerHTML = `<span class="interim-final-tag">✓</span> <strong class="interim-text-final">${this.escapeHtml(text)}</strong>`;
    }
  }

  escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // =========================================================================
  // NATURAL READ-ALOUD (SpeechSynthesis) CONTROLLER
  // =========================================================================
  async ensureVoicesLoaded() {
    if (!this.synth) return [];
    let voices = this.synth.getVoices();
    if (voices && voices.length > 0) {
      this.voices = voices;
      return voices;
    }

    return new Promise((resolve) => {
      let resolved = false;
      const onVoices = () => {
        if (resolved) return;
        resolved = true;
        this.voices = this.synth.getVoices();
        resolve(this.voices);
      };

      if (this.synth.onvoiceschanged !== undefined) {
        this.synth.addEventListener("voiceschanged", onVoices, { once: true });
        this.synth.onvoiceschanged = onVoices;
      }

      setTimeout(() => {
        if (!resolved) {
          resolved = true;
          this.voices = this.synth.getVoices();
          resolve(this.voices);
        }
      }, 600);
    });
  }

  getBestVoice(langCode = "ta-IN") {
    if (!this.synth) return null;
    let voices = this.synth.getVoices();
    if (!voices || voices.length === 0) {
      voices = this.voices || [];
    }
    if (!voices || voices.length === 0) return null;

    if (langCode.startsWith("ta")) {
      // 1. Exact locale matching: ta-IN or ta_IN
      let taVoice = voices.find(v => v.lang && (v.lang === "ta-IN" || v.lang === "ta_IN"));

      // 2. Verified Tamil voice names (e.g., Apple Vani, Google தமிழ், Microsoft Valluvar/Latha)
      if (!taVoice) {
        taVoice = voices.find(v => {
          const name = (v.name || "").toLowerCase();
          const vLang = (v.lang || "").toLowerCase();
          return (
            vLang.startsWith("ta") ||
            name.includes("tamil") ||
            name.includes("vani") ||
            name.includes("valluvar") ||
            name.includes("latha")
          );
        });
      }

      // 3. STRICT RULE: Never silently fall back to an English voice for Tamil text.
      return taVoice || null;
    }

    if (langCode.startsWith("en")) {
      const inVoice = voices.find(v => 
        (v.lang && (v.lang === "en-IN" || v.lang === "en_IN")) ||
        (v.name && (
          v.name.toLowerCase().includes("rishi") || 
          v.name.toLowerCase().includes("aman") || 
          v.name.toLowerCase().includes("tara") || 
          v.name.toLowerCase().includes("india")
        ))
      );
      if (inVoice) return inVoice;
      return voices.find(v => v.lang && v.lang.startsWith("en")) || voices[0] || null;
    }

    return voices.find(v => v.lang && v.lang.startsWith(langCode.slice(0, 2))) || null;
  }

  cleanSpeechText(rawText, langCode = "ta-IN") {
    if (!rawText) return "";
    let clean = String(rawText);

    // 1. Remove all Unicode emojis, decorative icons, and pictographs
    clean = clean.replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE00}-\u{FE0F}\u{1F900}-\u{1F9FF}]/gu, " ");

    // 2. Remove UI interface symbols, bullets, arrows, checkmarks, asterisks
    clean = clean.replace(/[•·*#✓—–~|►▶️🔊🎙️📢ℹ️⚠️🚨→←↑↓]/g, " ");

    // 3. Remove quotation marks, parentheses, brackets, braces, backticks
    clean = clean.replace(/[()[\]{}"'“”‘’`]/g, " ");

    // 4. Handle ampersands
    if (langCode.startsWith("ta")) {
      clean = clean.replace(/\s*&\s*/g, " மற்றும் ");
    } else {
      clean = clean.replace(/\s*&\s*/g, " and ");
    }

    // 5. Handle hyphens (e.g. "RuralCare-க்கு" -> "RuralCare க்கு")
    clean = clean.replace(/-/g, " ");

    // 6. Handle slashes (e.g. "General Medicine / Eye Care" -> pause)
    clean = clean.replace(/\//g, " ");

    // 7. Convert exclamation marks into natural pause (commas)
    // Never pronounce the punctuation word "exclamation mark"!
    clean = clean.replace(/!+/g, ", ");

    // 8. Convert colons and semicolons into natural pause
    clean = clean.replace(/[:;]+/g, ", ");

    // 9. Convert question marks:
    // If mid-sentence: comma pause
    // If end of sentence: period pause
    clean = clean.replace(/\?\s+(?=[^\s])/g, ", ");
    clean = clean.replace(/\?+/g, ".");

    // 10. Normalize whitespace and multiple punctuation marks
    clean = clean.replace(/\s*,\s*,+/g, ", ");
    clean = clean.replace(/\s*\.\s*\.+/g, ". ");
    clean = clean.replace(/,\s*\./g, ".");
    clean = clean.replace(/\s+/g, " ").trim();

    // 11. Ensure proper sentence termination with a period for clean TTS cadence
    if (clean && !clean.endsWith(".")) {
      clean += ".";
    }

    return clean;
  }

  async speakText(text, options = {}, callback = null) {
    if (typeof options === "function") {
      callback = options;
      options = {};
    }

    if (!text || !String(text).trim()) {
      if (callback) callback();
      return;
    }

    this.stopSpeech();

    const lang = options.lang || (this.languageMode === "en" ? "en-IN" : "ta-IN");
    this.lastSpokenText = text;
    this.lastSpokenLang = lang;
    let voice = options.voice || (lang.startsWith("ta") ? this.azureVoice : null);

    // =========================================================================
    // 1. TAMIL SPEECH OUTPUT: MICROSOFT AZURE NEURAL TTS (ta-IN-PallaviNeural)
    // =========================================================================
    // Strict Tamil voice guard: Never silently fall back to an English voice
    if (lang.startsWith("ta") && !voice) {
      voice = this.azureVoice || "ta-IN-PallaviNeural";
      console.warn("[VoiceAssistant] Using Microsoft Azure Neural Tamil TTS:", voice);
    }

    if (lang.startsWith("ta") || this.languageMode === "ta" || this.languageMode === "mixed") {
      const selectedVoice = voice || this.azureVoice || "ta-IN-PallaviNeural";
      this.updateDebugVoiceUI({ name: selectedVoice, lang: "ta-IN" }, "ta-IN", text);
      this.updateAudioPlayerUI(true, "Connecting Azure Neural...", `${selectedVoice} (Azure Neural)`);

      try {
        const response = await fetch("/api/tts/synthesize", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Accept": "audio/mpeg"
          },
          body: JSON.stringify({
            text: text,
            voice: selectedVoice,
            rate: "+0%"
          })
        });

        if (!response.ok) {
          throw new Error(`Azure Neural TTS returned HTTP ${response.status}`);
        }

        const audioBlob = await response.blob();
        const audioUrl = URL.createObjectURL(audioBlob);
        const audio = new Audio(audioUrl);
        this.currentAudio = audio;
        this.isSpeaking = true;
        this.isPaused = false;

        audio.onplay = () => {
          this.isSpeaking = true;
          this.isPaused = false;
          this.updateAudioPlayerUI(true, "Playing", `${voice} (Azure Neural)`);
        };

        audio.onpause = () => {
          if (this.isSpeaking) {
            this.isPaused = true;
            this.updateAudioPlayerUI(true, "Paused", `${voice} (Azure Neural)`);
          }
        };

        audio.onended = () => {
          this.isSpeaking = false;
          this.isPaused = false;
          this.currentAudio = null;
          URL.revokeObjectURL(audioUrl);
          this.updateAudioPlayerUI(false);
          if (callback) callback();
        };

        audio.onerror = (e) => {
          console.warn("[VoiceAssistant] Azure Audio error:", e);
          this.isSpeaking = false;
          this.isPaused = false;
          this.currentAudio = null;
          URL.revokeObjectURL(audioUrl);
          this.updateAudioPlayerUI(false);
          if (callback) callback();
        };

        await audio.play();
        return;
      } catch (err) {
        console.warn("[VoiceAssistant] Backend Neural TTS unavailable, attempting browser speech fallback:", err);
        const browserTaVoice = this.getBestVoice("ta-IN");
        if (browserTaVoice && this.synth) {
          console.log("[VoiceAssistant] Falling back to browser Tamil voice:", browserTaVoice.name);
          this.speakWithBrowserVoice(text, "ta-IN", browserTaVoice, callback);
          return;
        } else {
          this.notifyMissingTamilVoice();
          this.isSpeaking = false;
          this.updateAudioPlayerUI(false);
          if (callback) callback();
          return;
        }
      }
    }

    // =========================================================================
    // 2. ENGLISH SPEECH OUTPUT: BROWSER SPEECH SYNTHESIS (en-IN)
    // =========================================================================
    this.speakWithBrowserVoice(text, lang, voice, callback);
  }

  speakWithBrowserVoice(text, lang = "ta-IN", voice = null, callback = null) {
    if (!this.synth) {
      if (callback) callback();
      return;
    }

    voice = voice || this.getBestVoice(lang);
    const cleanedSpeech = this.cleanSpeechText(text, lang);
    this.updateDebugVoiceUI(voice, lang, cleanedSpeech);

    const rate = 0.92;
    const pitch = 1.0;

    const sentences = cleanedSpeech
      .split(/(?<=[.\n])\s+/)
      .map(s => s.trim())
      .filter(s => s.length > 0);

    const chunks = sentences.length > 0 ? sentences : [cleanedSpeech];
    this.utteranceQueue = [...chunks];
    this.isSpeaking = true;
    this.isPaused = false;
    this.updateAudioPlayerUI(true, "Playing (Browser Voice)", voice ? `${voice.name} (${voice.lang})` : lang);

    const playNextChunk = () => {
      if (this.utteranceQueue.length === 0) {
        this.isSpeaking = false;
        this.updateAudioPlayerUI(false);
        if (callback) callback();
        return;
      }

      const chunkText = this.utteranceQueue.shift();
      const utterance = new SpeechSynthesisUtterance(chunkText);
      utterance.lang = lang;
      utterance.rate = rate;
      utterance.pitch = pitch;
      if (voice) {
        utterance.voice = voice;
      }

      utterance.onend = () => {
        playNextChunk();
      };

      utterance.onerror = (err) => {
        console.warn("[VoiceAssistant] Utterance error:", err);
        playNextChunk();
      };

      try {
        this.synth.speak(utterance);
      } catch (e) {
        console.warn("[VoiceAssistant] synth.speak exception:", e);
        this.isSpeaking = false;
        this.updateAudioPlayerUI(false);
        if (callback) callback();
      }
    };

    playNextChunk();
  }

  notifyMissingTamilVoice() {
    const alertBox = document.getElementById("voice-fallback-alert");
    const warningMsg = this.languageMode === "ta"
      ? "கவனிக்க: தமிழ் குரல் செயலாக்கம் தற்காலிகமாக கிடைக்கவில்லை. திரையில் உள்ள தமிழ் உரையை வாசிக்கவும்."
      : "Notice: Tamil speech service unavailable. Please refer to on-screen text.";

    if (alertBox) {
      alertBox.innerHTML = `
        <div style="display: flex; align-items: center; gap: 8px;">
          <span style="font-size: 20px;">ℹ️</span>
          <span>${warningMsg}</span>
        </div>
      `;
      alertBox.classList.remove("hidden");
    }
  }

  updateDebugVoiceUI(voice, lang, cleanedText = "") {
    const badge = document.getElementById("speech-active-voice-badge");
    const voiceNameEl = document.getElementById("debug-tts-voice-name");
    const langEl = document.getElementById("debug-tts-lang");
    const previewEl = document.getElementById("tts-cleaned-text-preview");

    let voiceDisplayName = "";
    if (lang.startsWith("ta") || this.languageMode === "ta" || this.languageMode === "mixed") {
      voiceDisplayName = `${this.azureVoice || "ta-IN-PallaviNeural"} (Azure Neural)`;
    } else {
      voiceDisplayName = voice ? `${voice.name} (${voice.lang})` : "System English Default";
    }

    if (badge) {
      badge.textContent = `Voice: ${voiceDisplayName}`;
      badge.style.display = "inline-block";
    }
    if (voiceNameEl) {
      voiceNameEl.textContent = voiceDisplayName;
    }
    if (langEl) {
      langEl.textContent = lang.startsWith("ta") ? "ta-IN" : "en-IN";
    }
    if (previewEl && cleanedText) {
      previewEl.textContent = cleanedText;
    }
  }

  setAzureVoice(voiceName) {
    if (voiceName === "ta-IN-PallaviNeural" || voiceName === "ta-IN-ValluvarNeural") {
      this.azureVoice = voiceName;
      console.log(`[VoiceAssistant] Active Azure Tamil Voice set to: ${this.azureVoice}`);
      const voiceNameEl = document.getElementById("debug-tts-voice-name");
      if (voiceNameEl) {
        voiceNameEl.textContent = `${this.azureVoice} (Azure Neural)`;
      }
      const selectEl = document.getElementById("azure-voice-select");
      if (selectEl && selectEl.value !== voiceName) {
        selectEl.value = voiceName;
      }
    }
  }

  testTamilSentence(testNumber) {
    const testSentences = {
      1: "வணக்கம்! ரூரல்கேர் மருத்துவ சேவைக்கு உங்களை வரவேற்கிறோம்.",
      2: "உங்கள் பெயரைச் சொல்லுங்கள்.",
      3: "உங்களுக்கு எந்த மருத்துவ உதவி தேவை?",
      4: "அருகிலுள்ள மருத்துவமனைகளைத் தேடுகிறோம். தயவுசெய்து காத்திருக்கவும்.",
      5: "உங்கள் பயண விவரங்களை உறுதிப்படுத்துங்கள்."
    };

    const text = testSentences[testNumber];
    if (!text) return;

    console.log(`[VoiceAssistant] Testing Official Tamil Sentence ${testNumber} via Azure Neural (${this.azureVoice}): "${text}"`);
    this.speakText(text, { lang: "ta-IN", voice: this.azureVoice });
  }

  testMandatoryTTS(index) {
    const list = {
      1: "வணக்கம்! உங்கள் பெயரைச் சொல்லுங்கள்.",
      2: "உங்களுக்கு எந்த உடல்நலப் பிரச்சினை உள்ளது?",
      3: "நீங்கள் எந்த மருத்துவமனைக்குச் செல்ல விரும்புகிறீர்கள்?"
    };
    const text = list[index];
    if (!text) return;
    console.log(`[VoiceAssistant] Testing Mandatory Tamil TTS ${index}: "${text}" (${this.azureVoice})`);
    this.speakText(text, { lang: "ta-IN", voice: this.azureVoice });
  }

  async testTamilSTTSentence(index) {
    const testSentences = {
      1: "எனக்கு தலைவலி இருக்கிறது.",
      2: "எனக்கு காய்ச்சல் உள்ளது.",
      3: "எனக்கு அருகில் மருத்துவமனை வேண்டும்.",
      4: "எனக்கு போக்குவரத்து வசதி தேவை.",
      5: "நான் பொள்ளாச்சியில் வசிக்கிறேன்.",
      6: "வணக்கம், எனக்கு உடம்பு சரியில்லை, டாக்டர் பார்க்க வேண்டும்.",
      7: "எனக்கு eye checkup மற்றும் hospital appointment வேண்டும்."
    };
    const targetText = testSentences[index];
    if (!targetText) return;

    const previewEl = document.getElementById("stt-test-result-preview");
    const confBadge = document.getElementById("stt-test-confidence-badge");
    if (previewEl) previewEl.textContent = `ஒலி மாதிரி உருவாக்கப்பட்டு பகுப்பாய்வு செய்யப்படுகிறது: "${targetText}"...`;
    if (confBadge) confBadge.textContent = "Processing...";

    this.setMicrophoneState("PROCESSING");

    try {
      // 1. Synthesize audio for this target sentence
      const synthRes = await fetch("/api/tts/synthesize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: targetText, voice: this.azureVoice })
      });
      if (!synthRes.ok) throw new Error("TTS generation failed");
      const audioBlob = await synthRes.blob();

      // 2. Feed into STT service
      const formData = new FormData();
      formData.append("audio", audioBlob, "benchmark.mp3");
      formData.append("language", "ta-IN");

      const sttRes = await fetch("/api/stt/transcribe", {
        method: "POST",
        body: formData
      });
      const data = await sttRes.json();

      if (sttRes.ok && data.success) {
        this.setMicrophoneState("TRANSCRIPTION READY");
        const confPct = Math.round((data.confidence || 0.90) * 100);
        if (previewEl) {
          previewEl.innerHTML = `<strong>இலக்கு:</strong> "${targetText}"<br><strong>பெறப்பட்ட உரை:</strong> <span style="color:#059669; font-weight:800;">"${data.transcription}"</span> (${data.engine})`;
        }
        if (confBadge) confBadge.textContent = `${confPct}% Confidence`;
        
        // Populate into editable review box for inspection
        this.showTranscriptionForReview(data.transcription, data.engine, data.confidence);
      } else {
        throw new Error(data.message || "STT failed");
      }
    } catch (e) {
      console.error("[VoiceAssistant] STT test error:", e);
      this.setMicrophoneState("READY");
      if (previewEl) previewEl.textContent = `பிழை: ${e.message}`;
      if (confBadge) confBadge.textContent = "Error";
    }
  }

  pauseSpeech() {
    if (this.currentAudio && !this.currentAudio.paused) {
      this.currentAudio.pause();
      this.isPaused = true;
      this.updateAudioPlayerUI(true, "Paused", `${this.azureVoice} (Azure Neural)`);
    } else if (this.synth && this.isSpeaking && !this.isPaused) {
      this.synth.pause();
      this.isPaused = true;
      this.updateAudioPlayerUI(true, "Paused");
    }
  }

  resumeSpeech() {
    if (this.currentAudio && this.currentAudio.paused) {
      this.currentAudio.play().catch(e => console.warn(e));
      this.isPaused = false;
      this.updateAudioPlayerUI(true, "Playing", `${this.azureVoice} (Azure Neural)`);
    } else if (this.synth && this.isSpeaking && this.isPaused) {
      this.synth.resume();
      this.isPaused = false;
      this.updateAudioPlayerUI(true, "Playing");
    }
  }

  stopSpeech() {
    if (this.currentAudio) {
      this.currentAudio.pause();
      this.currentAudio.currentTime = 0;
      this.currentAudio = null;
    }
    this.utteranceQueue = [];
    if (this.synth) {
      this.synth.cancel();
    }
    this.isSpeaking = false;
    this.isPaused = false;
    this.updateAudioPlayerUI(false);
  }

  repeatSpeech() {
    if (this.lastSpokenText) {
      this.speakText(this.lastSpokenText, { lang: this.lastSpokenLang, voice: this.azureVoice });
    } else {
      this.repeatQuestion();
    }
  }

  updateAudioPlayerUI(visible, status = "Playing", voiceInfo = "") {
    const player = document.getElementById("toolbar-speech-player");
    const statusLabel = document.getElementById("speech-player-status");
    const pauseBtn = document.getElementById("btn-speech-pause");
    const voiceBadge = document.getElementById("speech-active-voice-badge");

    if (player) {
      if (visible) {
        player.classList.remove("hidden");
        player.style.display = "inline-flex";
      } else {
        player.classList.add("hidden");
        player.style.display = "none";
      }
    }

    if (statusLabel) {
      statusLabel.textContent = status;
    }

    if (voiceBadge && voiceInfo) {
      voiceBadge.textContent = voiceInfo;
      voiceBadge.style.display = "inline-block";
    }

    if (pauseBtn) {
      if (this.isPaused) {
        pauseBtn.innerHTML = `▶️ <span data-i18n="audio_resume">Resume</span>`;
        pauseBtn.onclick = () => this.resumeSpeech();
      } else {
        pauseBtn.innerHTML = `⏸️ <span data-i18n="audio_pause">Pause</span>`;
        pauseBtn.onclick = () => this.pauseSpeech();
      }
    }
  }

  // =========================================================================
  // WORKFLOW EXECUTION
  // =========================================================================
  startWorkflow() {
    this.setVoiceFlow("menu");
  }

  getCurrentQuestionList() {
    const mode = this.languageMode || "ta";
    return this.questions[mode] || this.questions.ta;
  }

  askCurrentQuestion() {
    const list = this.getCurrentQuestionList();
    if (this.currentStepIndex >= list.length) {
      this.finishVoiceWorkflow();
      return;
    }

    const q = list[this.currentStepIndex];
    const questionTextEl = document.getElementById("voice-prompt-text");
    const subTextEl = document.getElementById("voice-prompt-subtext");
    const stepBadge = document.getElementById("voice-step-badge");

    if (questionTextEl) questionTextEl.textContent = q.spoken;
    if (subTextEl) subTextEl.textContent = q.display;
    if (stepBadge) stepBadge.textContent = `${this.currentStepIndex + 1} / ${list.length}`;

    this.setMicrophoneState("READY");
    this.showInterimFeedback("", false);

    this.speakText(q.spoken, () => {
      this.listen();
    });
  }

  repeatQuestion() {
    if (this.voiceFlow === "booking") {
      this.askCurrentQuestion();
    } else {
      this.setVoiceFlow(this.voiceFlow);
    }
  }

  retryQuestion() {
    this.stopListening();
    this.stopSpeech();
    
    if (this.voiceFlow === "booking") {
      const list = this.getCurrentQuestionList();
      const currentQ = list[this.currentStepIndex];
      if (currentQ) {
        const input = document.getElementById(`voice-review-${currentQ.id}`);
        if (input) {
          if (input.type === "checkbox") input.checked = false;
          else input.value = "";
        }
        this.extractedData[currentQ.id] = "";
      }
      this.showInterimFeedback("", false);
      this.askCurrentQuestion();
    } else {
      this.setVoiceFlow(this.voiceFlow);
    }
  }

  async listen() {
    this.stopSpeech();
    this.showInterimFeedback("", false);
    this.hideTranscriptionReview();

    try {
      this.isListening = true;
      this.setMicrophoneState("LISTENING");

      // Start actual microphone recording
      await this.audioRecorder.start();

      // Concurrently run SpeechRecognition if available for live interim feedback animation
      if (this.recognition) {
        try {
          this.recognition.lang = this.getRecognitionLang();
          this.recognition.start();
        } catch (e) {}
      }

      const promptMsg = this.languageMode === "ta"
        ? "மைக் கேட்கிறது... பேசுங்கள் (பேசி முடித்தவுடன் 'Stop' அழுத்தவும்)..."
        : "Listening... speak now (tap 'Stop' when finished)...";
      this.showInterimFeedback(promptMsg, true);
    } catch (err) {
      console.warn("[VoiceAssistant] Microphone access error:", err);
      this.isListening = false;
      this.setMicrophoneState("MICROPHONE PERMISSION REQUIRED");
      const msg = this.languageMode === "ta"
        ? "மைக் அணுகல் தேவை. உலாவியில் மைக் அனுமதியை வழங்கவும் அல்லது கீழே தட்டச்சு செய்யவும்."
        : "Microphone permission required. Please allow microphone access or type below.";
      this.showFallbackMessage(msg);
      this.showManualFallbackInput();
    }
  }

  async stopListening() {
    if (!this.isListening) return;
    this.isListening = false;
    this.setMicrophoneState("PROCESSING");

    // Stop parallel WebSpeech if active
    if (this.recognition) {
      try { this.recognition.stop(); } catch (e) {}
    }

    try {
      const audioBlob = await this.audioRecorder.stop();

      // If no valid audio captured
      if (!audioBlob || audioBlob.size < 500) {
        if (this.lastWebSpeechTranscript) {
          const t = this.lastWebSpeechTranscript;
          this.lastWebSpeechTranscript = "";
          this.showTranscriptionForReview(t, "Browser WebSpeech", 0.85);
          return;
        }
        this.setMicrophoneState("READY");
        const msg = this.languageMode === "ta"
          ? "சத்தம் தெளிவாகக் கேட்கவில்லை. மீண்டும் பேசவும் அல்லது கீழே உள்ள தட்டச்சுப் பெட்டியைப் பயன்படுத்தவும்."
          : "No speech detected. Please speak again or enter text manually.";
        this.showInterimFeedback(msg, false);
        this.showManualFallbackInput();
        return;
      }

      this.showInterimFeedback(
        this.languageMode === "ta"
          ? "குரல் பதிவு பகுப்பாய்வு செய்யப்படுகிறது (Neural STT)..."
          : "Analyzing spoken audio with Neural STT...",
        true
      );

      // Upload to /api/stt/transcribe
      const formData = new FormData();
      formData.append("audio", audioBlob, "speech.wav");
      formData.append("language", this.getRecognitionLang());

      const res = await fetch("/api/stt/transcribe", {
        method: "POST",
        body: formData
      });
      const data = await res.json();

      if (res.ok && data.success && data.transcription) {
        this.setMicrophoneState("TRANSCRIPTION READY");
        this.showTranscriptionForReview(data.transcription, data.engine, data.confidence);
      } else {
        if (this.lastWebSpeechTranscript) {
          const t = this.lastWebSpeechTranscript;
          this.lastWebSpeechTranscript = "";
          this.setMicrophoneState("TRANSCRIPTION READY");
          this.showTranscriptionForReview(t, "Browser WebSpeech (Fallback)", 0.85);
          return;
        }
        const errorMsg = data.message || (this.languageMode === "ta"
          ? "சத்தம் தெளிவாகக் கேட்கவில்லை. மீண்டும் பேசவும் அல்லது கீழே உள்ள தட்டச்சுப் பெட்டியைப் பயன்படுத்தவும்."
          : "Speech was unclear. Please retry or enter manually.");
        this.setMicrophoneState("READY");
        this.showInterimFeedback(errorMsg, false);
        this.showManualFallbackInput();
      }
    } catch (err) {
      console.warn("[VoiceAssistant] STT fetch error, checking browser WebSpeech fallback:", err);
      if (this.lastWebSpeechTranscript) {
        const t = this.lastWebSpeechTranscript;
        this.lastWebSpeechTranscript = "";
        this.setMicrophoneState("TRANSCRIPTION READY");
        this.showTranscriptionForReview(t, "Browser WebSpeech (Fallback)", 0.85);
        return;
      }
      this.setMicrophoneState("READY");
      const errTxt = this.languageMode === "ta"
        ? "குரல் அறிதல் சேவையில் பிழை. மீண்டும் பேசவும் அல்லது தட்டச்சு செய்யவும்."
        : "Recognition service error. Please retry or type below.";
      this.showInterimFeedback(errTxt, false);
      this.showManualFallbackInput();
    }
  }

  showTranscriptionForReview(transcript, engine = "Neural STT", confidence = 0.90) {
    this.pendingTranscription = transcript;
    this.showInterimFeedback(transcript, false);

    const container = document.getElementById("voice-transcription-edit-container");
    const input = document.getElementById("voice-transcription-input");
    const badge = document.getElementById("stt-engine-badge");

    if (container && input) {
      input.value = transcript;
      if (badge) {
        const pct = Math.round((confidence || 0.90) * 100);
        badge.textContent = `${engine} (${pct}%)`;
      }
      container.classList.remove("hidden");
      input.focus();
    }

    // Play gentle read-back confirmation prompt
    if (this.languageMode === "ta") {
      const readbackPrompt = `நீங்கள் கூறியது: ${transcript}. விவரங்களை உறுதிப்படுத்த 'ஆம்' அல்லது உறுதி செய் பொத்தானை அழுத்தவும்.`;
      this.speakText(readbackPrompt, { lang: "ta-IN", voice: this.azureVoice });
    }
  }

  confirmCurrentTranscription() {
    const input = document.getElementById("voice-transcription-input");
    const confirmedText = input ? input.value.trim() : (this.pendingTranscription || "").trim();
    if (!confirmedText) return;

    this.hideTranscriptionReview();
    this.handleVoiceAnswer(confirmedText);
  }

  hideTranscriptionReview() {
    const container = document.getElementById("voice-transcription-edit-container");
    if (container) container.classList.add("hidden");
    this.pendingTranscription = null;
  }

  cancelCurrentPrompt() {
    this.hideTranscriptionReview();
    this.stopListening();
    this.stopSpeech();
    this.setMicrophoneState("READY");
    this.showInterimFeedback("", false);
  }

  insertQuickText(text) {
    const input = document.getElementById("voice-transcription-input");
    if (input) {
      input.value = text;
      input.focus();
    }
    const manualInput = document.getElementById("manual-tamil-text-input");
    if (manualInput) {
      manualInput.value = text;
      manualInput.focus();
    }
  }

  submitManualText() {
    const manualInput = document.getElementById("manual-tamil-text-input");
    if (!manualInput) return;
    const val = manualInput.value.trim();
    if (!val) return;
    manualInput.value = "";
    this.hideTranscriptionReview();
    this.handleVoiceAnswer(val);
  }

  showManualFallbackInput() {
    const card = document.getElementById("voice-manual-fallback-card");
    if (card) {
      card.scrollIntoView({ behavior: "smooth", block: "nearest" });
      const input = document.getElementById("manual-tamil-text-input");
      if (input) input.focus();
    }
  }

  // =========================================================================
  // ANSWER PARSING & SPOKEN CONVERSATIONAL DISPATCHER
  // =========================================================================
  handleVoiceAnswer(transcript) {
    if (!transcript) return;
    const clean = transcript.trim();
    const lower = clean.toLowerCase();

    // Universal Spoken Cancel / Home Control
    if (lower.includes("ரத்து") || lower.includes("cancel") || lower.includes("முகப்பு") || lower.includes("home")) {
      const cancelMsg = this.languageMode === "ta" ? "ரத்து செய்யப்பட்டது. முதன்மை குரல் பட்டியலுக்குத் திரும்புகிறோம்." : "Cancelled. Returning to main menu.";
      this.speakText(cancelMsg, () => {
        this.setVoiceFlow("menu");
      });
      return;
    }

    // Universal Spoken Repeat Control
    if (lower.includes("மீண்டும் சொல்") || lower.includes("repeat") || lower.includes("மறுபடி")) {
      this.repeatSpeech();
      return;
    }

    // Handle Final Pre-Submission Confirmation
    if (this.isAwaitingFinalConfirmation) {
      const confirmWords = ["ஆம்", "சரி", "ஆமா", "yes", "confirm", "பதிவு செய்", "உறுதி", "correct"];
      let confirmed = false;
      for (const w of confirmWords) {
        if (lower.includes(w)) { confirmed = true; break; }
      }

      if (confirmed) {
        this.isAwaitingFinalConfirmation = false;
        if (window.app) {
          window.app.submitVoiceRegistration();
        }
      } else {
        const recheckPrompt = this.languageMode === "ta"
          ? "தயவுசெய்து திரையில் உள்ள விவரங்களைச் சரிபார்க்கவும். உறுதி செய்ய 'ஆம்' அல்லது 'சரி' என்று கூறவும்."
          : "Please check the details on screen. Say 'Yes' or click Confirm to proceed.";
        this.speakText(recheckPrompt, () => this.listen());
      }
      return;
    }

    // =======================================================================
    // 1. MAIN SPOKEN MENU DISPATCHER
    // =======================================================================
    if (this.voiceFlow === "menu") {
      if (lower.includes("மருத்துவமனை") || lower.includes("தேட") || lower.includes("hospital") || lower.includes("find") || lower.includes("clinic")) {
        this.setVoiceFlow("discovery");
      } else if (lower.includes("பதிவு") || lower.includes("அப்பாயின்ட்மென்ட்") || lower.includes("book") || lower.includes("appointment") || lower.includes("new")) {
        this.setVoiceFlow("booking");
      } else if (lower.includes("பயண நிலை") || lower.includes("டிராக்") || lower.includes("நிலை") || lower.includes("track") || lower.includes("status")) {
        this.setVoiceFlow("tracking");
      } else if (lower.includes("வாகனம்") || lower.includes("ஆட்டோ") || lower.includes("transport") || lower.includes("ride")) {
        this.setVoiceFlow("transport");
      } else {
        const retryMenu = this.languageMode === "ta"
          ? "மருத்துவமனை தேட, பதிவு செய்ய, அல்லது பயண நிலையை அறிய என்று கூறவும்."
          : "Please say: 'Find Hospital', 'Book Appointment', or 'Track Journey'.";
        this.speakText(retryMenu, () => this.listen());
      }
      return;
    }

    // =======================================================================
    // 2. SPOKEN HOSPITAL DISCOVERY DISPATCHER
    // =======================================================================
    if (this.voiceFlow === "discovery") {
      // Check if user is confirming booking for a suggested hospital
      if (this.pendingDiscoveryBooking) {
        const yesWords = ["ஆம்", "சரி", "ஆமா", "yes", "book", "தேர்வு"];
        let choseBooking = false;
        for (const y of yesWords) {
          if (lower.includes(y)) { choseBooking = true; break; }
        }
        if (choseBooking) {
          const rec = this.pendingDiscoveryBooking;
          this.acceptDiscoveryOption(rec.facId, rec.facName, rec.srvCode, rec.srvName);
          return;
        }
      }

      // Analyze requested medical service
      let srvCode = "SRV-GEN";
      if (lower.includes("கண்") || lower.includes("eye")) {
        srvCode = "SRV-EYE";
      } else if (lower.includes("டையா") || lower.includes("dialysis") || lower.includes("சிறுநீரக")) {
        srvCode = "SRV-DIA";
      } else if (lower.includes("தாய்") || lower.includes("ante") || lower.includes("mater") || lower.includes("கர்ப்ப")) {
        srvCode = "SRV-ANC";
      }
      this.executeSpokenDiscovery(srvCode);
      return;
    }

    // =======================================================================
    // 3. SPOKEN JOURNEY TRACKING DISPATCHER
    // =======================================================================
    if (this.voiceFlow === "tracking") {
      this.submitVoiceTrackSearch(clean);
      return;
    }

    // =======================================================================
    // 4. APPOINTMENT BOOKING QUESTIONNAIRE
    // =======================================================================
    if (this.voiceFlow === "booking" || this.voiceFlow === "transport") {
      const list = this.getCurrentQuestionList();
      const currentQ = list[this.currentStepIndex];
      if (!currentQ) return;

      const parsedVal = this.parseAnswer(currentQ.id, clean);
      this.extractedData[currentQ.id] = parsedVal;
      this.updateLiveField(currentQ.id, parsedVal, clean);

      this.currentStepIndex++;
      if (this.currentStepIndex < list.length) {
        setTimeout(() => {
          this.askCurrentQuestion();
        }, 600);
      } else {
        this.finishVoiceWorkflow();
      }
    }
  }

  // =========================================================================
  // HOSPITAL DISCOVERY EXECUTOR
  // =========================================================================
  async executeSpokenDiscovery(srvCode) {
    const srvName = srvCode === "SRV-EYE" ? "Eye Care / Ophthalmology" : srvCode === "SRV-DIA" ? "Dialysis" : srvCode === "SRV-ANC" ? "Antenatal Care" : "General Medicine";
    const panel = document.getElementById("voice-discovery-panel");
    const cards = document.getElementById("voice-discovery-cards");
    const summary = document.getElementById("voice-discovery-summary");
    if (panel) panel.classList.remove("hidden");

    try {
      const res = await fetch(`/api/facilities/recommendations?service=${srvCode}&village=Chettipalayam`);
      const data = await res.json();
      const recs = data.recommendations || [];

      if (recs.length > 0) {
        const top = recs[0];
        this.pendingDiscoveryBooking = { srvCode, srvName, facId: top.id, facName: top.name };
        
        if (summary) {
          summary.textContent = `Found ${recs.length} verified healthcare facility options for ${srvName}:`;
        }
        if (cards) {
          cards.innerHTML = recs.map(r => `
            <div style="background: #FFFFFF; border: 1.5px solid var(--border-light); border-radius: var(--radius-md); padding: 14px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
              <div>
                <strong style="font-size: 16px; color: var(--text-dark);">${r.name}</strong>
                <div style="font-size: 13px; color: var(--text-muted);">${r.location} • ${r.distance_text || (r.distance_km + ' km')}</div>
                <span class="badge-tag badge-recommended" style="margin-top: 4px;">${r.recommendation_label}</span>
              </div>
              <button type="button" class="btn btn-primary" onclick="voiceAssistant.acceptDiscoveryOption('${r.id}', '${r.name}', '${srvCode}', '${srvName}')">
                Book at This Hospital →
              </button>
            </div>
          `).join("");
        }

        const speakText = this.languageMode === "ta"
          ? `${top.name} ${top.distance_text || (top.distance_km + ' கிலோமீட்டர்')} தொலைவில் உறுதி செய்யப்பட்டுள்ளது. இந்த மருத்துவமனையில் பதிவு செய்ய விரும்புகிறீர்களா? 'ஆம்' என்று கூறவும்.`
          : `${top.name} is verified available at ${top.distance_text || (top.distance_km + ' kilometers')}. Would you like to book an appointment here? Please say 'Yes' or 'No'.`;

        this.speakText(speakText, () => {
          this.listen();
        });
      } else {
        if (summary) summary.textContent = "No verified facility currently found for this service.";
        const speakText = this.languageMode === "ta"
          ? "மன்னிக்கவும். தற்போது இந்த சேவைக்கு அருகிலுள்ள மருத்துவமனை கிடைக்கவில்லை."
          : "Sorry, no verified facility is currently available for this service.";
        this.speakText(speakText);
      }
    } catch (e) {
      console.warn("Discovery fetch exception:", e);
    }
  }

  acceptDiscoveryOption(facId, facName, srvCode, srvName) {
    const srvInput = document.getElementById("voice-review-service_query");
    if (srvInput) srvInput.value = `${srvName} (${facName})`;

    this.pendingDiscoveryBooking = null;
    const confirmPrompt = this.languageMode === "ta"
      ? `${facName} மருத்துவமனைக்கு பதிவு செய்ய தொடங்குகிறோம். நோயாளியின் பெயரை கூறவும்.`
      : `Starting booking for ${facName}. What is the patient's full name?`;

    this.setVoiceFlow("booking");
    this.speakText(confirmPrompt, () => {
      this.currentStepIndex = 0;
      this.askCurrentQuestion();
    });
  }

  // =========================================================================
  // CARE JOURNEY TRACKING EXECUTOR
  // =========================================================================
  async submitVoiceTrackSearch(searchKey) {
    const input = document.getElementById("voice-track-id-input");
    const query = (searchKey || (input && input.value) || "").trim();
    if (!query) {
      const prompt = this.languageMode === "ta"
        ? "தயவுசெய்து உங்கள் 10 இலக்க மொபைல் எண் அல்லது பயண எண்ணைக் கூறவும்."
        : "Please speak your 10-digit mobile number or Care Journey ID.";
      this.speakText(prompt, () => this.listen());
      return;
    }

    if (input) input.value = query;

    const explanationEl = document.getElementById("voice-track-explanation");
    const detailsEl = document.getElementById("voice-track-details");
    const badgeEl = document.getElementById("voice-track-status-badge");
    const panel = document.getElementById("voice-tracking-panel");
    if (panel) panel.classList.remove("hidden");

    try {
      let url = "";
      if (query.toUpperCase().startsWith("RC-")) {
        url = `/api/journeys/${query.toUpperCase()}`;
      } else {
        const digits = query.replace(/\D/g, "");
        const phone = digits.length >= 10 ? digits.slice(-10) : digits;
        url = `/api/journeys?contact_number=${phone}`;
      }

      const res = await fetch(url);
      const data = await res.json();
      let journey = null;
      let readiness = null;

      if (data.journey) {
        journey = data.journey;
        readiness = data.readiness;
      } else if (data.journeys && data.journeys.length > 0) {
        journey = data.journeys[0];
        readiness = journey.departure_readiness_check;
      }

      if (journey) {
        if (explanationEl) {
          explanationEl.textContent = readiness ? readiness.explanation : journey.status;
        }
        if (badgeEl) {
          badgeEl.className = "status-pill " + (readiness && readiness.is_ready_for_departure ? "status-confirmed" : "status-pending");
          badgeEl.textContent = journey.status;
        }
        if (detailsEl) {
          detailsEl.innerHTML = `
            <div><strong>Patient:</strong> ${journey.patient_name} (${journey.village})</div>
            <div><strong>Hospital:</strong> ${journey.facility_name}</div>
            <div><strong>Service:</strong> ${journey.preferred_service_name}</div>
            <div><strong>Transport:</strong> ${journey.transport_required ? "Requested" : "Self"}</div>
          `;
        }

        const speakText = this.languageMode === "ta"
          ? `நோயாளி ${journey.patient_name} அவர்களின் பயண நிலை: ${journey.status === 'COORDINATION_CONFIRMED' ? 'பயணிக்க தயாராக உள்ளது. மருத்துவமனை மற்றும் ஓட்டுநர் உறுதி செய்யப்பட்டுள்ளனர்.' : 'தற்போது பயணிக்க தயாராகவில்லை. ' + (readiness ? readiness.explanation : '')}`
          : `Patient ${journey.patient_name}, Care Journey status: ${journey.status === 'COORDINATION_CONFIRMED' ? 'Confirmed ready to travel. Hospital and driver are confirmed.' : 'Not ready to travel yet. ' + (readiness ? readiness.explanation : '')}`;

        this.speakText(speakText);
      } else {
        if (explanationEl) explanationEl.textContent = "No care journey found matching " + query;
        const notFoundText = this.languageMode === "ta"
          ? "மன்னிக்கவும். இந்த எண்ணில் எந்த மருத்துவப் பயணப் பதிவும் கிடைக்கவில்லை."
          : "Sorry, no care journey was found matching this number.";
        this.speakText(notFoundText);
      }
    } catch (e) {
      console.warn("Track voice search exception:", e);
    }
  }

  // =========================================================================
  // ANSWER PARSING & EDITABLE VERIFICATION
  // =========================================================================
  parseAnswer(fieldId, rawText) {
    if (!rawText) return "";
    const clean = rawText.trim();

    if (fieldId === "age") {
      const match = clean.match(/\d+/);
      if (match) return parseInt(match[0], 10);

      let ageSum = 0;
      for (const [word, num] of Object.entries(TAMIL_NUMBER_WORDS)) {
        if (clean.includes(word)) {
          ageSum += num;
        }
      }
      if (ageSum > 0 && ageSum <= 120) return ageSum;
      return clean;
    }

    if (fieldId === "contact_number") {
      const digits = clean.replace(/\D/g, "");
      return digits.length >= 10 ? digits.slice(-10) : digits || clean;
    }

    if (fieldId === "transport_required") {
      const lower = clean.toLowerCase();
      const yesWords = [
        "ஆம்", "ஆமா", "ஆமாங்க", "வேணும்", "வேண்டும்", "ஆட்டோ வேணும்", "வண்டி வேணும்",
        "கண்டிப்பா", "தேவை", "yes", "yeah", "yep", "sure", "need", "transport"
      ];
      for (const y of yesWords) {
        if (lower.includes(y)) return true;
      }
      return false;
    }

    return clean;
  }

  updateLiveField(fieldId, value, rawSpeech) {
    const input = document.getElementById(`voice-review-${fieldId}`);
    if (input) {
      if (input.type === "checkbox") {
        input.checked = !!value;
      } else {
        input.value = value;
      }
    }
  }

  finishVoiceWorkflow() {
    this.setMicrophoneState("TRANSCRIPTION READY");
    this.isAwaitingFinalConfirmation = true;

    const isTamil = this.languageMode === "ta";
    const name = this.extractedData.patient_name || document.getElementById("voice-review-patient_name")?.value || "";
    const age = this.extractedData.age || document.getElementById("voice-review-age")?.value || "";
    const service = this.extractedData.service_query || document.getElementById("voice-review-service_query")?.value || "General Medicine";
    const transport = (this.extractedData.transport_required || document.getElementById("voice-review-transport_required")?.checked)
      ? (isTamil ? "தேவை (Yes)" : "Requested")
      : (isTamil ? "தேவையில்லை (No)" : "Not required");

    // Show Read-Back Verification Banner
    const readbackBanner = document.getElementById("voice-readback-banner");
    const readbackText = document.getElementById("voice-readback-text");
    if (readbackBanner) readbackBanner.classList.remove("hidden");
    if (readbackText) {
      readbackText.textContent = isTamil
        ? `நோயாளி: ${name}, வயது: ${age}, மருத்துவ சேவை: ${service}, வாகன உதவி: ${transport}.`
        : `Patient: ${name}, Age: ${age}, Service: ${service}, Transport: ${transport}.`;
    }

    const reviewBox = document.getElementById("voice-final-review");
    if (reviewBox) reviewBox.classList.remove("hidden");

    // Read back all important details aloud
    const readbackSpoken = isTamil
      ? `உங்கள் விவரங்கள் சரிபார்க்கப்படுகின்றன: நோயாளி ${name}, வயது ${age}, சேவை ${service}, வாகன உதவி ${transport}. இந்த விவரங்கள் சரியானவையா? பதிவு செய்ய 'ஆம்' அல்லது 'சரி' என்று கூறவும்.`
      : `Please confirm your details: Patient ${name}, Age ${age}, Service ${service}, Transport ${transport}. Are these details correct? Please say 'Yes' to register.`;

    this.speakText(readbackSpoken, () => {
      this.listen();
    });
  }

  showFallbackMessage(msg) {
    const el = document.getElementById("voice-fallback-alert");
    if (el) {
      el.innerHTML = `
        <div style="font-weight: 600; margin-bottom: 6px;">${msg}</div>
        <div>
          <button type="button" class="btn btn-secondary" style="padding: 6px 14px; font-size: 13px;" onclick="app.startSelfServiceTouch()">
            👉 ${this.languageMode === 'ta' ? 'தொடுதிரை படிவத்திற்கு மாறவும்' : 'Switch to Touchscreen Form'}
          </button>
        </div>
      `;
      el.classList.remove("hidden");
    }
  }
}

// Global Voice Assistant singleton
window.voiceAssistant = new VoiceAssistant();
