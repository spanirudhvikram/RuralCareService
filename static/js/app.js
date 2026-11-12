/**
 * RuralCare - Main Application Controller
 */

class RuralCareApp {
  constructor() {
    this.currentView = "method-select";
    this.registrationMode = "SELF_SERVICE"; // or ASSISTED_SERVICE
    this.registrationChannel = "TOUCH_BASED"; // or VOICE_ASSISTED
    this.operatorData = { id: null, name: null };
    this.wizardStep = 1;
    this.totalSteps = 4;
    this.villages = [];
    this.services = [];
    this.facilities = [];
    this.selectedFacility = null;
    this.activeRole = "citizen";
    this.activeFacilityId = "FAC-01"; // Pollachi GH default
    this.activeDriverId = "DRV-01"; // Murugan S. default
    this.activeJourneyId = null;
    this.activePatientPhone = null;
    this.trackPollInterval = null;
    this.kioskIdleTimeout = null;

    this.init();
  }

  async init() {
    // 0. Restore accessibility settings (scale & contrast)
    this.initAccessibility();

    // 1. Language check: FIRST SCREEN must be language selection if not chosen
    if (!window.i18n.hasSelectedLanguage()) {
      this.openLanguageModal(false); // cannot close until chosen
    } else {
      window.i18n.setLanguage(window.i18n.getCurrentLanguage());
      this.updateLangDisplayLabel();
    }

    // 2. Load directory metadata (villages, services, facilities)
    await this.loadInitialMetadata();

    // 3. Setup event listeners & kiosk idle monitor
    this.setupEventListeners();
    this.setupKioskIdleMonitor();

    // 4. Check initial view or URL hash
    const hash = window.location.hash.replace("#", "");
    if (hash && ["method-select", "directory", "track", "facility-portal", "driver-portal", "admin-portal"].includes(hash)) {
      this.showView(hash);
    } else {
      this.showView("method-select");
    }
  }

  setupEventListeners() {
    window.addEventListener("ruralcare:languageChanged", () => {
      this.updateLangDisplayLabel();
      this.renderVillagesSelect();
      this.renderServicesSelect();
      this.loadDirectory();
    });

    window.addEventListener("ruralcare:syncCompleted", (event) => {
      console.log("[App] Sync completed notification received:", event.detail);
      if (this.currentView === "track") {
        this.searchJourney();
      } else if (this.currentView === "facility-portal") {
        this.loadStaffRequests();
      } else if (this.currentView === "admin-portal") {
        this.loadAdminOverview();
      }
    });

    window.addEventListener("ruralcare:serverStateUpdated", (event) => {
      console.log("[App] Server state updated notification received:", event.detail);
      if (this.currentView === "track") {
        this.searchJourney();
      }
    });
  }

  setupKioskIdleMonitor() {
    const resetIdleTimer = () => {
      if (this.kioskIdleTimeout) clearTimeout(this.kioskIdleTimeout);
      // Auto-clear kiosk after 90 seconds of inactivity on registration forms
      if (this.currentView === "touch-register" || this.currentView === "voice-register") {
        this.kioskIdleTimeout = setTimeout(() => {
          console.log("[Kiosk Security] Auto-clearing kiosk session due to inactivity.");
          this.clearKioskPatientData();
          this.showView("method-select");
        }, 90000);
      }
    };

    window.addEventListener("mousemove", resetIdleTimer);
    window.addEventListener("touchstart", resetIdleTimer);
    window.addEventListener("keydown", resetIdleTimer);
  }

  clearKioskPatientData() {
    // Sanitize and clear all DOM input fields
    const fieldsToClear = [
      "touch-patient-name",
      "touch-patient-age",
      "touch-patient-phone",
      "touch-pickup-landmark",
      "voice-review-patient_name",
      "voice-review-age",
      "voice-review-contact_number",
      "voice-review-village",
      "voice-review-service_query"
    ];
    fieldsToClear.forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = "";
    });

    const villageSelect = document.getElementById("touch-patient-village");
    if (villageSelect) villageSelect.selectedIndex = 0;

    const serviceSelect = document.getElementById("touch-service-select");
    if (serviceSelect) serviceSelect.selectedIndex = 0;

    const transCheckbox = document.getElementById("voice-review-transport_required");
    if (transCheckbox) transCheckbox.checked = false;

    this.selectedFacility = null;
    this.wizardStep = 1;
  }

  // =========================================================================
  // Elderly Accessibility & Self-Service Kiosk Controls
  // =========================================================================

  initAccessibility() {
    this.fontScale = 1.0;
    const savedScale = sessionStorage.getItem("ruralcare_font_scale");
    if (savedScale) {
      this.setFontScale(parseFloat(savedScale), false);
    } else {
      this.setFontScale(1.0, false);
    }

    const savedContrast = sessionStorage.getItem("ruralcare_high_contrast");
    if (savedContrast === "true") {
      document.documentElement.classList.add("high-contrast");
      const btn = document.getElementById("btn-toggle-contrast");
      if (btn) {
        btn.classList.add("active");
        btn.setAttribute("aria-pressed", "true");
      }
    }
  }

  setFontScale(scale, save = true) {
    this.fontScale = scale;
    document.documentElement.style.setProperty("--kiosk-scale", scale);

    const b100 = document.getElementById("btn-scale-100");
    const b150 = document.getElementById("btn-scale-150");
    const b200 = document.getElementById("btn-scale-200");

    if (b100) {
      const active = Math.abs(scale - 1.0) < 0.05;
      b100.classList.toggle("active", active);
      b100.setAttribute("aria-pressed", active ? "true" : "false");
    }
    if (b150) {
      const active = Math.abs(scale - 1.5) < 0.05;
      b150.classList.toggle("active", active);
      b150.setAttribute("aria-pressed", active ? "true" : "false");
    }
    if (b200) {
      const active = Math.abs(scale - 2.0) < 0.05;
      b200.classList.toggle("active", active);
      b200.setAttribute("aria-pressed", active ? "true" : "false");
    }

    if (save) {
      sessionStorage.setItem("ruralcare_font_scale", scale);
    }
  }

  adjustFontSize(delta) {
    const scales = [1.0, 1.25, 1.5, 1.75, 2.0];
    let current = this.fontScale || 1.0;
    let idx = scales.findIndex(s => Math.abs(s - current) < 0.05);
    if (idx === -1) idx = 0;

    let nextIdx = idx;
    if (delta === 1 || delta === "increase") {
      nextIdx = Math.min(scales.length - 1, idx + 1);
    } else if (delta === -1 || delta === "decrease") {
      nextIdx = Math.max(0, idx - 1);
    }
    this.setFontScale(scales[nextIdx]);
  }

  toggleHighContrast() {
    const isHC = document.documentElement.classList.toggle("high-contrast");
    const btn = document.getElementById("btn-toggle-contrast");
    if (btn) {
      btn.classList.toggle("active", isHC);
      btn.setAttribute("aria-pressed", isHC ? "true" : "false");
    }
    sessionStorage.setItem("ruralcare_high_contrast", isHC ? "true" : "false");
  }

  kioskStartOver() {
    if (this.currentView === "touch-register" || this.currentView === "voice-register") {
      const nameInput = document.getElementById("touch-patient-name");
      const phoneInput = document.getElementById("touch-patient-phone");
      if ((nameInput && nameInput.value.trim()) || (phoneInput && phoneInput.value.trim())) {
        if (!confirm("Are you sure you want to clear this registration and start over?")) {
          return;
        }
      }
    }
    this.clearKioskPatientData();
    this.resetWizard();
    this.showView("method-select");
  }

  kioskSelectService(serviceType) {
    if (serviceType === "find_hospital") {
      this.showView("directory");
    } else if (serviceType === "book_appointment") {
      this.startSelfServiceTouch();
    } else if (serviceType === "arrange_transport") {
      this.startSelfServiceTouch();
      this.setTransportChoice(true);
    } else if (serviceType === "track_journey") {
      this.showView("track");
    } else if (serviceType === "follow_up") {
      this.showView("track");
      setTimeout(() => {
        const inp = document.getElementById("track-search-input");
        if (inp) {
          inp.placeholder = "Enter Care Journey ID or Phone for follow-up...";
          inp.focus();
        }
      }, 150);
    } else if (serviceType === "community_needs") {
      this.showAdminRadarTab();
    }
  }

  speakText(text, lang) {
    const currentLang = lang || (window.i18n && window.i18n.getCurrentLanguage()) || "ta";
    const langCode = currentLang === "ta" ? "ta-IN" : "en-IN";

    if (window.voiceAssistant && typeof window.voiceAssistant.speakText === "function") {
      window.voiceAssistant.speakText(text, { lang: langCode });
      return;
    }

    if (!("speechSynthesis" in window)) {
      console.warn("[Speech] Web Speech API (SpeechSynthesis) not supported in this browser.");
      return;
    }

    try {
      window.speechSynthesis.cancel();
      if (!text || !String(text).trim()) return;

      const voices = window.speechSynthesis.getVoices() || [];
      let voice = null;
      if (langCode.startsWith("ta")) {
        voice = voices.find(v => 
          (v.lang && (v.lang === "ta-IN" || v.lang === "ta_IN" || v.lang.startsWith("ta"))) ||
          (v.name && (v.name.toLowerCase().includes("tamil") || v.name.toLowerCase().includes("vani")))
        );
        if (!voice) {
          console.warn("[Speech] No native Tamil TTS voice found. Refusing to speak Tamil with English voice.");
          return;
        }
      } else {
        voice = voices.find(v => v.lang && v.lang.startsWith("en")) || voices[0] || null;
      }

      // Clean punctuation to pauses
      let clean = String(text)
        .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE00}-\u{FE0F}]/gu, " ")
        .replace(/[•·*#✓—–~|►▶️🔊🎙️📢ℹ️⚠️🚨]/g, " ")
        .replace(/[()[\]{}"'“”‘’`]/g, " ")
        .replace(/-/g, " ")
        .replace(/\//g, " ")
        .replace(/!+/g, ", ")
        .replace(/[:;]+/g, ", ")
        .replace(/\?\s+(?=[^\s])/g, ", ")
        .replace(/\?+/g, ".")
        .replace(/\s+/g, " ")
        .trim();

      const utterance = new SpeechSynthesisUtterance(clean);
      utterance.lang = langCode;
      utterance.rate = 0.86;
      if (voice) utterance.voice = voice;
      window.speechSynthesis.speak(utterance);
    } catch (err) {
      console.warn("[Speech] Could not speak text:", err);
    }
  }

  testTamilTTS(num) {
    if (window.voiceAssistant && typeof window.voiceAssistant.testTamilSentence === "function") {
      window.voiceAssistant.testTamilSentence(num);
    }
  }

  readAloudCurrentScreen() {
    let textToRead = "";
    const activeViewEl = document.querySelector(".app-view.active");
    if (activeViewEl && activeViewEl.id === "view-method-select") {
      // Warm, natural kiosk welcome dialogue per Part 3
      textToRead = window.i18n.t("welcome_spoken_dialogue", "வணக்கம்! RuralCare-க்கு உங்களை வரவேற்கிறோம். உங்களுக்கு என்ன உதவி வேண்டும்? மருத்துவமனை தேட வேண்டுமா? மருத்துவரைச் சந்திக்க நேரம் பதிவு செய்ய வேண்டுமா? அல்லது போக்குவரத்து உதவி வேண்டுமா?");
    } else if (activeViewEl && activeViewEl.id === "view-voice-register") {
      textToRead = window.i18n.t("voice_reg_intro_dialogue", "வணக்கம்! உங்கள் விவரங்களை குரல் மூலம் பதிவு செய்ய உதவுகிறேன். ஒவ்வொரு கேள்விக்கும் தெளிவாகப் பேசவும்.");
    } else if (activeViewEl) {
      const titleEl = activeViewEl.querySelector(".page-title, h1, h2");
      const subEl = activeViewEl.querySelector(".page-subtitle, p");
      if (titleEl && titleEl.innerText) textToRead += titleEl.innerText + ". ";
      if (subEl && subEl.innerText) textToRead += subEl.innerText;
    }
    if (!textToRead.trim()) {
      textToRead = window.i18n.t("welcome_spoken_dialogue");
    }
    this.speakText(textToRead);
  }

  updateLangDisplayLabel() {
    const cur = window.i18n.getCurrentLanguage() || "ta";
    const labelEl = document.getElementById("current-lang-label");
    if (labelEl) {
      if (cur === "ta") labelEl.textContent = "தமிழ்";
      else if (cur === "en") labelEl.textContent = "English";
      else if (cur === "te") labelEl.textContent = "తెలుగు";
      else if (cur === "ml") labelEl.textContent = "മലയാളം";
      else if (cur === "kn") labelEl.textContent = "ಕನ್ನಡ";
      else if (cur === "hi") labelEl.textContent = "हिन्दी";
    }
  }

  openLanguageModal(canClose = true) {
    const modal = document.getElementById("modal-language");
    if (modal) {
      modal.classList.remove("hidden");
      const cur = window.i18n.getCurrentLanguage() || "ta";
      document.querySelectorAll(".lang-card-btn").forEach(btn => {
        if (btn.getAttribute("data-lang-code") === cur) btn.classList.add("active");
        else btn.classList.remove("active");
      });
    }
  }

  selectLanguage(langCode) {
    if (window.voiceAssistant) {
      window.voiceAssistant.stopSpeech();
      window.voiceAssistant.stopListening();
      if (langCode === "ta" || langCode === "en") {
        window.voiceAssistant.languageMode = langCode;
        const modes = ["ta", "en", "mixed"];
        modes.forEach(m => {
          const btn = document.getElementById(`btn-voice-mode-${m}`);
          if (btn) {
            if (m === langCode) btn.classList.add("active");
            else btn.classList.remove("active");
          }
        });
      }
    }
    window.i18n.setLanguage(langCode);
    document.querySelectorAll(".lang-card-btn").forEach(btn => {
      if (btn.getAttribute("data-lang-code") === langCode) btn.classList.add("active");
      else btn.classList.remove("active");
    });
    this.closeLanguageModal();
  }

  closeLanguageModal() {
    const modal = document.getElementById("modal-language");
    if (modal) modal.classList.add("hidden");
  }

  openRoleModal() {
    const modal = document.getElementById("modal-role");
    if (modal) modal.classList.remove("hidden");
  }

  closeRoleModal() {
    const modal = document.getElementById("modal-role");
    if (modal) modal.classList.add("hidden");
  }

  openMobileMoreMenu() {
    const modal = document.getElementById("modal-mobile-more");
    if (modal) modal.classList.remove("hidden");
  }

  closeMobileMoreMenu() {
    const modal = document.getElementById("modal-mobile-more");
    if (modal) modal.classList.add("hidden");
  }

  async switchRolePersona(roleKey) {
    this.activeRole = roleKey;
    this.closeRoleModal();

    if (roleKey === "citizen") {
      await fetch("/api/auth/logout", { method: "POST" });
      this.showView("method-select");
    } else if (roleKey === "facility_pollachi") {
      this.activeFacilityId = "FAC-01";
      await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: "dr.revathi@pollachigh.tn.gov.in", password: "staff" })
      });
      this.showView("facility-portal");
    } else if (roleKey === "driver_murugan") {
      this.activeDriverId = "DRV-01";
      await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: "9842177101", password: "driver" })
      });
      this.showView("driver-portal");
    } else if (roleKey === "admin") {
      await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: "admin@ruralcare.gov.in", password: "admin" })
      });
      this.showView("admin-portal");
    }
  }

  showView(viewName) {
    if (["facility-portal", "driver-portal", "admin-portal"].includes(viewName) && this.activeRole === "citizen") {
      alert("Authorized healthcare staff or administration login is required to access this portal. Please select your authorized role.");
      this.openRoleModal();
      return;
    }

    this.currentView = viewName;
    window.location.hash = viewName;

    // Clear tracking polling when leaving track view
    if (viewName !== "track" && this.trackPollInterval) {
      clearInterval(this.trackPollInterval);
      this.trackPollInterval = null;
    }

    document.querySelectorAll(".app-view").forEach(v => v.classList.remove("active"));
    const target = document.getElementById(`view-${viewName}`);
    if (target) target.classList.add("active");

    // Update nav tab highlight
    document.querySelectorAll(".nav-tab-btn").forEach(btn => {
      if (btn.getAttribute("data-target-view") === viewName) {
        btn.classList.add("btn-primary");
        btn.classList.remove("btn-secondary");
      } else {
        btn.classList.remove("btn-primary");
        btn.classList.add("btn-secondary");
      }
    });

    // Update mobile bottom nav highlight
    document.querySelectorAll(".mobile-nav-btn").forEach(btn => {
      if (btn.getAttribute("data-target-view") === viewName) {
        btn.classList.add("active");
      } else {
        btn.classList.remove("active");
      }
    });
    this.closeMobileMoreMenu();

    window.scrollTo({ top: 0, behavior: "smooth" });

    // View specific initializers
    if (viewName === "directory") this.loadDirectory();
    else if (viewName === "track") {
      this.searchJourney();
      // Start reactive polling every 5s on track view so expired verification or driver cancellation triggers live
      if (!this.trackPollInterval) {
        this.trackPollInterval = setInterval(() => {
          if (this.currentView === "track" && this.activeJourneyId) {
            this.searchJourney(true); // background silent poll
          }
        }, 5000);
      }
    }
    else if (viewName === "facility-portal") this.loadStaffRequests();
    else if (viewName === "driver-portal") this.loadDriverRequests();
    else if (viewName === "admin-portal") this.loadAdminOverview();
  }

  async loadInitialMetadata() {
    try {
      const vRes = await fetch("/api/facilities/villages");
      const vData = await vRes.json();
      if (vData.success) {
        this.villages = vData.villages;
        await offlineDB.cacheDirectory("villages", this.villages);
      }
    } catch (e) {
      this.villages = (await offlineDB.getCachedDirectory("villages")) || [];
    }
    this.renderVillagesSelect();

    try {
      const sRes = await fetch("/api/facilities/services");
      const sData = await sRes.json();
      if (sData.success) {
        this.services = sData.services;
        await offlineDB.cacheDirectory("services", this.services);
      }
    } catch (e) {
      this.services = (await offlineDB.getCachedDirectory("services")) || [];
    }
    this.renderServicesSelect();

    try {
      const fRes = await fetch("/api/facilities");
      const fData = await fRes.json();
      if (fData.success) {
        this.facilities = fData.facilities;
        await offlineDB.cacheDirectory("facilities", this.facilities);
      }
    } catch (e) {
      this.facilities = (await offlineDB.getCachedDirectory("facilities")) || [];
    }
  }

  renderVillagesSelect() {
    const sel = document.getElementById("touch-patient-village");
    if (!sel) return;
    const curLang = window.i18n.getCurrentLanguage() || "ta";
    const currentVal = sel.value;

    sel.innerHTML = `<option value="">-- ${window.i18n.t("select_village_placeholder", "Select your village")} --</option>`;
    this.villages.forEach(v => {
      const name = curLang === "ta" && v.name_ta ? v.name_ta : v.name;
      const opt = document.createElement("option");
      opt.value = v.name;
      opt.textContent = `${name} (${v.district} District)`;
      sel.appendChild(opt);
    });
    if (currentVal) sel.value = currentVal;
  }

  renderServicesSelect() {
    const sel = document.getElementById("touch-service-select");
    const dirFilter = document.getElementById("directory-service-filter");
    const curLang = window.i18n.getCurrentLanguage() || "ta";

    if (sel) {
      const curVal = sel.value;
      sel.innerHTML = `<option value="">-- ${window.i18n.t("select_service_label", "Select Required Medical Service")} --</option>`;
      this.services.forEach(s => {
        const name = curLang === "ta" && s.name_ta ? s.name_ta : s.name;
        const opt = document.createElement("option");
        opt.value = s.id;
        opt.textContent = `${name} - ${s.description}`;
        sel.appendChild(opt);
      });
      if (curVal) sel.value = curVal;
    }

    if (dirFilter) {
      dirFilter.innerHTML = `<option value="">${window.i18n.t("filter_service_all", "All Medical Services")}</option>`;
      this.services.forEach(s => {
        const name = curLang === "ta" && s.name_ta ? s.name_ta : s.name;
        const opt = document.createElement("option");
        opt.value = s.id;
        opt.textContent = name;
        dirFilter.appendChild(opt);
      });
    }
  }

  // =========================================================================
  // Registration Flow (Assisted vs Self-Service)
  // =========================================================================
  startAssistedRegistration() {
    this.registrationMode = "ASSISTED_SERVICE";
    this.registrationChannel = "TOUCH_BASED";

    const opId = document.getElementById("assisted-operator-id")?.value.trim() || "KIOSK-OPERATOR-CP01";
    const opName = document.getElementById("assisted-operator-name")?.value.trim() || "Selvam";
    this.operatorData = { id: opId, name: opName };

    const badge = document.getElementById("reg-mode-badge");
    if (badge) {
      badge.textContent = `ASSISTED_SERVICE (Operator: ${opId})`;
      badge.className = "badge-tag badge-recommended";
    }

    this.clearKioskPatientData();
    this.resetWizard();
    this.showView("touch-register");
  }

  startSelfServiceTouch() {
    this.registrationMode = "SELF_SERVICE";
    this.registrationChannel = "TOUCH_BASED";
    this.operatorData = { id: null, name: null };

    const badge = document.getElementById("reg-mode-badge");
    if (badge) {
      badge.textContent = "SELF_SERVICE (Touchscreen)";
      badge.className = "badge-tag badge-self";
    }

    this.clearKioskPatientData();
    this.resetWizard();
    this.showView("touch-register");
  }

  startSelfServiceVoice() {
    this.startVoiceFirstMode("menu");
  }

  startVoiceFirstMode(initialFlow = "menu") {
    this.registrationMode = "SELF_SERVICE";
    this.registrationChannel = "VOICE_ASSISTED";
    this.operatorData = { id: null, name: null };

    this.clearKioskPatientData();
    this.showView("voice-register");
    if (window.voiceAssistant) {
      window.voiceAssistant.setVoiceFlow(initialFlow);
    }
  }

  openTouchVendingKiosk() {
    const panel = document.getElementById("kiosk-touch-vending-panel");
    if (panel) {
      panel.scrollIntoView({ behavior: "smooth" });
      panel.classList.add("highlight-pulse");
      setTimeout(() => panel.classList.remove("highlight-pulse"), 1500);
    }
  }

  toggleVoiceMic() {
    if (window.voiceAssistant.isListening) {
      window.voiceAssistant.stopListening();
    } else {
      window.voiceAssistant.listen();
    }
  }

  resetWizard() {
    this.wizardStep = 1;
    this.updateWizardUI();
    const today = new Date().toISOString().split("T")[0];
    const dateInput = document.getElementById("touch-preferred-date");
    if (dateInput && !dateInput.value) {
      dateInput.value = today;
      dateInput.min = today;
    }
  }

  updateWizardUI() {
    for (let i = 1; i <= this.totalSteps; i++) {
      const stepEl = document.getElementById(`wizard-step-${i}`);
      const nodeEl = document.getElementById(`node-${i}`);
      if (stepEl) {
        if (i === this.wizardStep) stepEl.classList.remove("hidden");
        else stepEl.classList.add("hidden");
      }
      if (nodeEl) {
        if (i < this.wizardStep) {
          nodeEl.className = "step-node completed";
        } else if (i === this.wizardStep) {
          nodeEl.className = "step-node active";
        } else {
          nodeEl.className = "step-node";
        }
      }
    }

    const fill = document.getElementById("wizard-progress-fill");
    if (fill) {
      const pct = (this.wizardStep / this.totalSteps) * 100;
      fill.style.width = `${pct}%`;
    }

    const counter = document.getElementById("wizard-step-counter");
    if (counter) counter.textContent = `Step ${this.wizardStep} of ${this.totalSteps}`;

    const nextBtn = document.getElementById("btn-wizard-next");
    const backBtn = document.getElementById("btn-wizard-back");

    if (this.wizardStep === 1) {
      if (backBtn) backBtn.textContent = window.i18n.t("btn_cancel", "Cancel");
    } else {
      if (backBtn) backBtn.textContent = window.i18n.t("btn_back", "Back");
    }

    if (this.wizardStep === this.totalSteps) {
      if (nextBtn) {
        nextBtn.textContent = window.i18n.t("btn_confirm_submit", "Confirm & Book Care Journey");
        nextBtn.className = "btn btn-success btn-lg";
      }
      this.populateReviewStep();
    } else {
      if (nextBtn) {
        nextBtn.textContent = window.i18n.t("btn_continue", "Continue");
        nextBtn.className = "btn btn-primary btn-lg";
      }
    }
  }

  wizardPrevStep() {
    if (this.wizardStep > 1) {
      this.wizardStep--;
      this.updateWizardUI();
    } else {
      this.showView("method-select");
    }
  }

  wizardNextStep() {
    if (this.wizardStep === 1) {
      const name = document.getElementById("touch-patient-name")?.value.trim();
      const age = document.getElementById("touch-patient-age")?.value.trim();
      const phone = document.getElementById("touch-patient-phone")?.value.trim();
      const village = document.getElementById("touch-patient-village")?.value;

      if (!name || name.length < 2) { alert("Please enter the patient's full name (minimum 2 letters)."); return; }
      if (!age || parseInt(age) < 1 || parseInt(age) > 125) { alert("Please enter a valid age between 1 and 125."); return; }
      if (!phone || !/^\d{10}$/.test(phone)) { alert("Please enter a valid 10-digit mobile number."); return; }
      if (!village) { alert("Please select your village."); return; }

      this.wizardStep++;
      this.updateWizardUI();
      this.filterFacilitiesForService();
    } else if (this.wizardStep === 2) {
      const serviceId = document.getElementById("touch-service-select")?.value;
      const visitDate = document.getElementById("touch-preferred-date")?.value;
      if (!serviceId) { alert("Please select the required healthcare service."); return; }
      if (!visitDate) { alert("Please select a preferred visit date."); return; }
      if (!this.selectedFacility) { alert("Please choose a participating healthcare facility from the list."); return; }

      this.wizardStep++;
      this.updateWizardUI();
    } else if (this.wizardStep === 3) {
      this.wizardStep++;
      this.updateWizardUI();
    } else if (this.wizardStep === 4) {
      this.submitTouchRegistration();
    }
  }

  filterFacilitiesForService() {
    const serviceId = document.getElementById("touch-service-select")?.value;
    const container = document.getElementById("touch-facilities-list");
    if (!container) return;

    container.innerHTML = "";
    const matched = this.facilities.filter(f => !serviceId || (f.available_services && f.available_services.includes(serviceId)));

    const listToRender = matched.length > 0 ? matched : this.facilities;
    listToRender.forEach(fac => {
      const card = document.createElement("div");
      const isSelected = this.selectedFacility && this.selectedFacility.id === fac.id;
      card.className = `form-radio-card ${isSelected ? 'selected' : ''}`;
      
      const vStatus = fac.verification_status || "UNVERIFIED";
      let statusBadge = `<span class="status-pill unverified">UNVERIFIED</span>`;
      if (vStatus === "VERIFIED") statusBadge = `<span class="status-pill verified">VERIFIED</span>`;
      else if (vStatus === "EXPIRED") statusBadge = `<span class="status-pill expired">EXPIRED</span>`;

      card.innerHTML = `
        <input type="radio" name="fac-choice" ${isSelected ? 'checked' : ''}>
        <div style="flex: 1;">
          <div style="display: flex; align-items: center; justify-content: space-between;">
            <strong>${fac.name}</strong>
            <div style="display: flex; gap: 6px;">
              <span class="badge-tag" style="background:#E2E8F0; color:#334155; font-size:10px;">DEMO</span>
              ${statusBadge}
            </div>
          </div>
          <div style="font-size: 13px; color: var(--text-muted);">${fac.facility_type} • ${fac.location}</div>
          <div style="font-size: 12px; color: var(--text-light); margin-top: 4px;">Verified by: ${fac.verified_by_staff || "Staff Verification Pending"}</div>
        </div>
      `;

      card.onclick = () => {
        this.selectedFacility = fac;
        document.querySelectorAll("#touch-facilities-list .form-radio-card").forEach(c => c.classList.remove("selected"));
        card.classList.add("selected");
        const radio = card.querySelector("input[type='radio']");
        if (radio) radio.checked = true;

        const isUnconfirmed = (fac.verification_status !== "VERIFIED") || 
                              (!fac.available_services || !fac.available_services.includes(serviceId));
        if (isUnconfirmed) {
          this.loadWizardRecommendations(fac.id);
        } else {
          const recBox = document.getElementById("wizard-alt-recommendations");
          if (recBox) recBox.classList.add("hidden");
        }
      };

      container.appendChild(card);
    });

    if (!this.selectedFacility && listToRender.length > 0) {
      const firstVerified = listToRender.find(f => f.verification_status === "VERIFIED") || listToRender[0];
      this.selectedFacility = firstVerified;
      const firstEl = container.firstElementChild;
      if (firstEl) firstEl.click();
    }
  }

  setTransportChoice(needed) {
    const yesRadio = document.getElementById("touch-trans-yes");
    const noRadio = document.getElementById("touch-trans-no");
    const landmarkBox = document.getElementById("touch-landmark-group");

    if (yesRadio) yesRadio.checked = needed;
    if (noRadio) noRadio.checked = !needed;
    if (landmarkBox) {
      if (needed) landmarkBox.classList.remove("hidden");
      else landmarkBox.classList.add("hidden");
    }
  }

  populateReviewStep() {
    const name = document.getElementById("touch-patient-name")?.value;
    const age = document.getElementById("touch-patient-age")?.value;
    const phone = document.getElementById("touch-patient-phone")?.value;
    const village = document.getElementById("touch-patient-village")?.value;
    const serviceSel = document.getElementById("touch-service-select");
    const serviceName = serviceSel ? serviceSel.options[serviceSel.selectedIndex]?.text : "-";
    const date = document.getElementById("touch-preferred-date")?.value;
    const transportNeeded = document.getElementById("touch-trans-yes")?.checked;
    const landmark = document.getElementById("touch-pickup-landmark")?.value || (village + " Center");

    document.getElementById("rev-patient-name").textContent = name;
    document.getElementById("rev-patient-age-phone").textContent = `${age} yrs • +91 ${phone}`;
    document.getElementById("rev-village").textContent = village;
    document.getElementById("rev-service").textContent = serviceName;
    document.getElementById("rev-date").textContent = date;
    document.getElementById("rev-facility").textContent = this.selectedFacility ? this.selectedFacility.name : "-";
    document.getElementById("rev-transport").textContent = transportNeeded ? "YES (Transport Requested)" : "NO (Self Travel)";
    document.getElementById("rev-landmark").textContent = transportNeeded ? landmark : "N/A";
  }

  async submitTouchRegistration() {
    const submitBtn = document.getElementById("btn-wizard-next");
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = "Creating Care Journey..."; }

    const transportNeeded = document.getElementById("touch-trans-yes")?.checked;
    const serviceId = document.getElementById("touch-service-select")?.value;
    const serviceObj = this.services.find(s => s.id === serviceId);
    const phoneVal = document.getElementById("touch-patient-phone")?.value.trim();

    const payload = {
      patient_name: document.getElementById("touch-patient-name")?.value.trim(),
      age: parseInt(document.getElementById("touch-patient-age")?.value),
      contact_number: phoneVal,
      village: document.getElementById("touch-patient-village")?.value,
      preferred_service_id: serviceId,
      preferred_service_name: serviceObj ? serviceObj.name : "General Medicine",
      preferred_date: document.getElementById("touch-preferred-date")?.value,
      facility_id: this.selectedFacility ? this.selectedFacility.id : "FAC-01",
      facility_name: this.selectedFacility ? this.selectedFacility.name : "Pollachi GH",
      registration_method: this.registrationMode,
      registration_channel: this.registrationChannel,
      operator_id: this.operatorData.id,
      operator_name: this.operatorData.name,
      transport_required: transportNeeded,
      pickup_landmark: transportNeeded ? (document.getElementById("touch-pickup-landmark")?.value.trim() || "Panchayat Office") : null,
      client_uuid: "reg_" + Date.now() + "_" + Math.random().toString(36).substring(2, 8)
    };

    if (!navigator.onLine) {
      const queued = await offlineDB.queueRequest(payload);
      this.clearKioskPatientData();
      this.activeJourneyId = queued.client_uuid;
      this.activePatientPhone = phoneVal;
      const trackInput = document.getElementById("track-search-input");
      if (trackInput) trackInput.value = queued.client_uuid;
      this.showView("track");
      this.renderOfflineJourneyCard(queued);
      if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = "Confirm & Book Care Journey"; }
      return;
    }

    try {
      const res = await fetch("/api/journeys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || "Journey creation failed");

      const created = data.journey;
      this.activeJourneyId = created.id;
      this.activePatientPhone = phoneVal;

      document.getElementById("track-search-input").value = created.id;
      // Clear sensitive form data from DOM kiosk inputs
      this.clearKioskPatientData();
      this.showView("track");
    } catch (err) {
      console.warn("Server submission error, storing offline:", err);
      const queued = await offlineDB.queueRequest(payload);
      this.clearKioskPatientData();
      this.activeJourneyId = queued.client_uuid;
      this.activePatientPhone = phoneVal;
      const trackInput = document.getElementById("track-search-input");
      if (trackInput) trackInput.value = queued.client_uuid;
      this.showView("track");
      this.renderOfflineJourneyCard(queued);
    } finally {
      if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = "Confirm & Book Care Journey"; }
    }
  }

  async submitVoiceRegistration() {
    const name = document.getElementById("voice-review-patient_name")?.value.trim();
    const age = document.getElementById("voice-review-age")?.value.trim();
    const phone = document.getElementById("voice-review-contact_number")?.value.trim();
    const village = document.getElementById("voice-review-village")?.value.trim() || "Chettipalayam";
    const transportNeeded = document.getElementById("voice-review-transport_required")?.checked;

    if (!name || !phone || !/^\d{10}$/.test(phone)) {
      alert("Please ensure a valid Patient Name and 10-digit Contact Phone are provided.");
      return;
    }

    const serviceQuery = (document.getElementById("voice-review-service_query")?.value || "").trim().toLowerCase();
    let srvId = "SRV-GEN";
    let srvName = "General Medicine";
    let facId = "FAC-01";
    let facName = "Pollachi Government Taluk Hospital";

    if (serviceQuery.includes("eye") || serviceQuery.includes("கண்")) {
      srvId = "SRV-EYE";
      srvName = "Eye Care / Ophthalmology";
      facId = "FAC-06";
      facName = "Coimbatore Medical College Hospital";
    } else if (serviceQuery.includes("dial") || serviceQuery.includes("டையா")) {
      srvId = "SRV-DIA";
      srvName = "Dialysis & Nephrology";
      facId = "FAC-04";
      facName = "Udumalaipettai Government Hospital";
    } else if (serviceQuery.includes("mater") || serviceQuery.includes("தாய்") || serviceQuery.includes("ante") || serviceQuery.includes("கர்ப்ப")) {
      srvId = "SRV-ANC";
      srvName = "Antenatal & Maternity Care";
      facId = "FAC-02";
      facName = "Negamam Upgraded Primary Health Centre";
    }

    const payload = {
      patient_name: name,
      age: parseInt(age) || 50,
      contact_number: phone,
      village: village,
      preferred_service_id: srvId,
      preferred_service_name: srvName,
      patient_reported_symptoms: serviceQuery || srvName,
      preferred_date: new Date().toISOString().split("T")[0],
      facility_id: facId,
      facility_name: facName,
      registration_method: "SELF_SERVICE",
      registration_channel: "VOICE_ASSISTED",
      transport_required: !!transportNeeded,
      pickup_choice: "HOME_ADDRESS",
      pickup_landmark: `${village} Home / Center`,
      consent_given: true,
      client_uuid: "voice_" + Date.now() + "_" + Math.random().toString(36).substring(2, 8)
    };

    if (!navigator.onLine) {
      const queued = await offlineDB.queueRequest(payload);
      this.clearKioskPatientData();
      this.activeJourneyId = queued.client_uuid;
      this.activePatientPhone = phone;
      const trackInput = document.getElementById("track-search-input");
      if (trackInput) trackInput.value = queued.client_uuid;
      this.showView("track");
      this.renderOfflineJourneyCard(queued);
      return;
    }

    try {
      const res = await fetch("/api/journeys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.success) {
        this.activeJourneyId = data.journey.id;
        this.activePatientPhone = phone;
        if (data.journey.patient_id) {
          this.activePatient = { id: data.journey.patient_id, name: name, contact_number: phone, village: village };
          this.updateCitizenSessionBanner(this.activePatient, data.journey);
        }
        document.getElementById("track-search-input").value = data.journey.id;
        this.clearKioskPatientData();
        this.showView("track");
        this.searchJourney();
        if (window.voiceAssistant) {
          window.voiceAssistant.speakText("உங்கள் விண்ணப்பம் வெற்றிகரமாக பதிவு செய்யப்பட்டது.");
        }
      }
    } catch (e) {
      const queued = await offlineDB.queueRequest(payload);
      this.clearKioskPatientData();
      this.activeJourneyId = queued.client_uuid;
      this.activePatientPhone = phone;
      const trackInput = document.getElementById("track-search-input");
      if (trackInput) trackInput.value = queued.client_uuid;
      this.showView("track");
      this.renderOfflineJourneyCard(queued);
    }
  }

  // =========================================================================
  // Facility Directory
  // =========================================================================
  async loadDirectory() {
    const serviceFilter = document.getElementById("directory-service-filter")?.value;
    const districtFilter = document.getElementById("directory-district-filter")?.value;
    const container = document.getElementById("directory-grid-container");
    if (!container) return;

    container.innerHTML = `<div style="padding: 20px; text-align: center;">${window.i18n.t("loading")}</div>`;

    try {
      let url = "/api/facilities?";
      if (serviceFilter) url += `service=${encodeURIComponent(serviceFilter)}&`;
      if (districtFilter) url += `district=${encodeURIComponent(districtFilter)}&`;

      const res = await fetch(url);
      const data = await res.json();
      const facilities = (data.success && data.facilities) ? data.facilities : this.facilities;
      this.renderDirectoryCards(facilities);
    } catch (e) {
      this.renderDirectoryCards(this.facilities);
    }
  }

  renderDirectoryCards(facilities) {
    const container = document.getElementById("directory-grid-container");
    if (!container) return;
    container.innerHTML = "";

    facilities.forEach(fac => {
      const card = document.createElement("div");
      card.className = "facility-card";

      const vStatus = fac.verification_status || "UNVERIFIED";
      let statusBadge = `<span class="status-pill unverified">UNVERIFIED</span>`;
      if (vStatus === "VERIFIED") statusBadge = `<span class="status-pill verified">VERIFIED</span>`;
      else if (vStatus === "EXPIRED") statusBadge = `<span class="status-pill expired">EXPIRED</span>`;

      const servicesTags = (fac.available_services || []).map(sid => {
        const sObj = this.services.find(s => s.id === sid);
        const name = sObj ? sObj.name : sid;
        return `<span class="service-tag">${name}</span>`;
      }).join("");

      card.innerHTML = `
        <div>
          <div class="flex-between mb-16">
            <span style="font-size: 12px; font-weight: 700; color: var(--text-muted);">${fac.id}</span>
            <div style="display: flex; gap: 6px;">
              <span class="badge-tag" style="background:#E2E8F0; color:#334155; font-size:10px;">DEMO RECORD</span>
              ${statusBadge}
            </div>
          </div>
          <h3 class="facility-name">${fac.name}</h3>
          <div class="facility-type">${fac.facility_type} • ${fac.district} District</div>
          <div style="font-size: 14px; color: var(--text-muted); margin: 8px 0;">📍 ${fac.location}</div>
          <div style="margin: 10px 0;">${servicesTags}</div>
          <div style="font-size: 13px; color: var(--text-light); margin-top: 10px;">
            🕒 ${fac.operating_hours || "08:00 AM - 04:00 PM"}
          </div>
          <div style="font-size: 12px; color: var(--text-light); margin-top: 4px;">
            📞 <a href="tel:${fac.contact_phone}" style="color: var(--primary-teal);">${fac.contact_phone}</a>
          </div>
          <div style="font-size: 12px; color: var(--text-light); margin-top: 6px;">
            Last Verified: <strong>${fac.last_verified_at ? new Date(fac.last_verified_at).toLocaleDateString() : 'Pending'}</strong>
          </div>
        </div>
        <div style="margin-top: 14px;">
          <button class="btn btn-outline" style="width: 100%;" onclick="app.selectFacilityFromDirectory('${fac.id}')">
            ${window.i18n.t("select_this_facility", "Select This Facility")}
          </button>
        </div>
      `;
      container.appendChild(card);
    });
  }

  selectFacilityFromDirectory(facId) {
    const fac = this.facilities.find(f => f.id === facId);
    if (fac) {
      this.selectedFacility = fac;
      this.startSelfServiceTouch();
      setTimeout(() => {
        this.wizardStep = 2;
        this.updateWizardUI();
        this.filterFacilitiesForService();
      }, 50);
    }
  }

  // =========================================================================
  // Care Journey Tracking & Departure Readiness Engine
  // =========================================================================
  async searchJourney(silent = false) {
    const searchVal = document.getElementById("track-search-input")?.value.trim();
    const container = document.getElementById("track-result-container");
    if (!container) return;

    if (!searchVal) {
      if (!silent) container.innerHTML = `<div style="padding: 20px; color: var(--text-muted);">Please enter a Journey ID or mobile number.</div>`;
      return;
    }

    if (!silent) {
      container.innerHTML = `<div style="padding: 20px; text-align: center;">${window.i18n.t("loading")}</div>`;
    }

    try {
      let res, data;
      const verifyParam = this.activePatientPhone ? `?verify_phone=${encodeURIComponent(this.activePatientPhone)}` : "";

      if (searchVal.startsWith("RC-")) {
        this.activeJourneyId = searchVal;
        res = await fetch(`/api/journeys/${searchVal}${verifyParam}`);
        data = await res.json();
      } else {
        // Search by phone
        res = await fetch(`/api/journeys/search?contact=${searchVal}`);
        const sData = await res.json();
        if (sData.success && sData.journeys.length > 0) {
          this.activeJourneyId = sData.journeys[0].id;
          this.activePatientPhone = searchVal;
          data = { success: true, journey: sData.journeys[0] };
        } else {
          data = { success: false, error: "No active journey found for this mobile number." };
        }
      }

      if (!data.success || !data.journey) {
        // Fallback: Check local IndexedDB offline storage
        const offlineMatch = window.offlineDB ? await window.offlineDB.findOfflineRequest(searchVal) : null;
        if (offlineMatch) {
          this.renderOfflineJourneyCard(offlineMatch);
          return;
        }

        container.innerHTML = `
          <div style="padding: 24px; background: #FEF2F2; border: 1px solid #FCA5A5; border-radius: var(--radius-md); color: #991B1B;">
            <strong>Journey Not Found:</strong> No record matches '${searchVal}'. If captured offline on this device, check the <button onclick="offlineDB.openSyncManager()" class="btn btn-outline" style="padding: 2px 8px; font-size: 12px; margin-left: 6px;">Offline Queue</button>.
          </div>
        `;
        return;
      }

      this.renderJourneyDetails(data.journey, data.readiness, data.events, data.is_restricted_preview);
    } catch (e) {
      // Network drop: check local IndexedDB offline storage
      const offlineMatch = window.offlineDB ? await window.offlineDB.findOfflineRequest(searchVal) : null;
      if (offlineMatch) {
        this.renderOfflineJourneyCard(offlineMatch);
        return;
      }

      if (!silent) {
        container.innerHTML = `
          <div style="padding: 20px; background: var(--bg-subtle); border-radius: var(--radius-md);">
            Could not fetch journey details right now (network unavailable).
            <button onclick="offlineDB.openSyncManager()" class="btn btn-primary" style="margin-top: 10px; display: block; padding: 6px 14px; font-size: 13px;">
              📡 Check Local Offline Queue
            </button>
          </div>
        `;
      }
    }
  }

  renderOfflineJourneyCard(item) {
    const container = document.getElementById("track-result-container");
    if (!container) return;

    const maskedPhone = item.contact_number ? (item.contact_number.substring(0, 5) + "XXXXX") : "-";
    const status = item.status || "SAVED OFFLINE";
    const reqType = item.request_type || "APPOINTMENT";
    const dateStr = item.created_at ? new Date(item.created_at).toLocaleString() : "Recently";

    container.innerHTML = `
      <div class="card" style="border: 2px solid #F59E0B; background: #FFFDF5; margin-bottom: 24px; box-shadow: 0 4px 12px rgba(245, 158, 11, 0.1);">
        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #FDE68A; padding-bottom: 14px; margin-bottom: 16px; flex-wrap: wrap; gap: 10px;">
          <div>
            <span style="font-size: 11px; font-weight: 800; background: #FEF3C7; color: #92400E; padding: 3px 10px; border-radius: 4px; text-transform: uppercase;">
              OFFLINE REQUEST (${reqType})
            </span>
            <h2 style="font-size: 20px; font-weight: 800; font-family: monospace; color: #1E293B; margin: 6px 0 0 0;">
              ${item.client_uuid}
            </h2>
          </div>
          <div style="text-align: right;">
            <span style="display: inline-block; padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 800; background: #FEF3C7; color: #B45309; border: 1px solid #FCD34D;">
              ⏳ ${status}
            </span>
            <div style="font-size: 11px; color: #64748B; margin-top: 4px;">Stored locally on this device</div>
          </div>
        </div>

        <div style="background: #FEF3C7; border: 1px solid #FDE68A; border-radius: var(--radius-md); padding: 14px; margin-bottom: 18px; color: #78350F; font-size: 14px; line-height: 1.5;">
          <strong>📡 Awaiting Automatic Network Synchronization:</strong>
          <p style="margin: 4px 0 0 0;">
            This request was captured offline and is preserved securely in your browser's IndexedDB.
            It will automatically upload to the central hospital network when internet connectivity is restored.
          </p>
        </div>

        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: var(--radius-md); padding: 16px; margin-bottom: 20px;">
          <div>
            <div style="font-size: 11px; font-weight: 700; color: #64748B; text-transform: uppercase;">Patient Name</div>
            <div style="font-size: 16px; font-weight: 700; color: #0F172A;">${item.patient_name || "-"} (${item.age || "-"} yrs)</div>
          </div>
          <div>
            <div style="font-size: 11px; font-weight: 700; color: #64748B; text-transform: uppercase;">Contact (Masked)</div>
            <div style="font-size: 16px; font-weight: 700; color: #0F172A;">+91 ${maskedPhone}</div>
          </div>
          <div>
            <div style="font-size: 11px; font-weight: 700; color: #64748B; text-transform: uppercase;">Village / Panchayat</div>
            <div style="font-size: 16px; font-weight: 700; color: #0F172A;">📍 ${item.village || "-"}</div>
          </div>
          <div>
            <div style="font-size: 11px; font-weight: 700; color: #64748B; text-transform: uppercase;">Service Requested</div>
            <div style="font-size: 16px; font-weight: 700; color: #0F172A;">🏥 ${item.preferred_service_name || "-"}</div>
          </div>
        </div>

        <!-- 7-Point Departure Readiness Checklist: Explicitly Unverified While Offline -->
        <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: var(--radius-md); padding: 16px; margin-bottom: 20px;">
          <h4 style="font-size: 14px; font-weight: 800; color: #475569; text-transform: uppercase; margin-bottom: 12px; letter-spacing: 0.5px;">
            Departure Readiness Checkpoint Status (Offline Verification Gate)
          </h4>
          <ul class="checkpoint-list" style="margin-bottom: 0;">
            <li class="checkpoint-item">
              <span class="chk-icon failed">⏳</span>
              <span style="color: #64748B;">1. Requested Healthcare Service: <strong>PENDING SERVER SYNC</strong></span>
            </li>
            <li class="checkpoint-item">
              <span class="chk-icon failed">⏳</span>
              <span style="color: #64748B;">2. Facility Verification Freshness: <strong>PENDING SERVER SYNC</strong></span>
            </li>
            <li class="checkpoint-item">
              <span class="chk-icon failed">⏳</span>
              <span style="color: #64748B;">3. Hospital Appointment Acceptance: <strong>PENDING SERVER SYNC</strong></span>
            </li>
            <li class="checkpoint-item">
              <span class="chk-icon failed">⏳</span>
              <span style="color: #64748B;">4. Local Transport Acceptance: <strong>${item.transport_required ? "PENDING SERVER SYNC" : "NOT REQUESTED"}</strong></span>
            </li>
            <li class="checkpoint-item">
              <span class="chk-icon failed">⏳</span>
              <span style="color: #64748B;">5. Pickup & Destination Coordinates: <strong>PENDING SERVER SYNC</strong></span>
            </li>
            <li class="checkpoint-item">
              <span class="chk-icon failed">⏳</span>
              <span style="color: #64748B;">6. Upfront Fare Agreed & Disclosed: <strong>PENDING SERVER SYNC</strong></span>
            </li>
            <li class="checkpoint-item">
              <span class="chk-icon failed">⏳</span>
              <span style="color: #64748B;">7. Patient Departure Readiness: <strong>NOT CONFIRMED (OFFLINE)</strong></span>
            </li>
          </ul>

          <div style="margin-top: 14px; padding: 10px 14px; background: #FEF2F2; border: 1px solid #FCA5A5; border-radius: 6px; color: #991B1B; font-size: 13px; font-weight: 700;">
            ⚠️ DEPARTURE NOT AUTHORIZED: Offline requests cannot be marked COORDINATION_CONFIRMED until server sync, hospital acceptance, and driver confirmation are completed.
          </div>
        </div>

        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
          <div style="font-size: 12px; color: #64748B;">
            Recorded at: ${dateStr} • Consent: <strong style="color: #059669;">Confirmed</strong>
          </div>
          <div style="display: flex; gap: 10px;">
            <button class="btn btn-outline" onclick="offlineDB.openSyncManager()">
              📡 View Offline Queue
            </button>
            <button class="btn btn-primary" onclick="offlineDB.syncPendingRequests().then(() => app.searchJourney())">
              🔄 Sync Now
            </button>
          </div>
        </div>
      </div>
    `;
  }

  async recordSampleOfflineRequest(type = "MEDICINE") {
    let payload;
    const now = Date.now();
    if (type === "APPOINTMENT") {
      payload = {
        request_type: "APPOINTMENT",
        patient_name: "Meenakshi Ammal",
        age: 64,
        contact_number: "98421" + Math.floor(10000 + Math.random() * 90000),
        village: "Chettipalayam",
        preferred_service_id: "SRV-EYE",
        preferred_service_name: "Ophthalmology / Eye Care",
        preferred_date: new Date().toISOString().split("T")[0],
        facility_id: "FAC-01",
        facility_name: "Pollachi Government Taluk Hospital",
        transport_required: true,
        pickup_landmark: "Panchayat Office",
        consent_given: true,
        client_uuid: `offline_apt_${now}_${Math.random().toString(36).substring(2, 7)}`
      };
    } else if (type === "MEDICINE") {
      payload = {
        request_type: "MEDICINE",
        patient_name: "Kuppusamy M.",
        age: 58,
        contact_number: "98421" + Math.floor(10000 + Math.random() * 90000),
        village: "Chettipalayam",
        medicines_requested: "Amlodipine 5mg & Metformin 500mg (Monthly Refill)",
        preferred_service_name: "Chronic Disease Pharmacy Refill",
        consent_given: true,
        client_uuid: `med_${now}_${Math.random().toString(36).substring(2, 7)}`
      };
    } else if (type === "TRANSPORT") {
      payload = {
        request_type: "TRANSPORT",
        patient_name: "Palaniammal R.",
        contact_number: "98421" + Math.floor(10000 + Math.random() * 90000),
        village: "Melur Rural",
        pickup_landmark: "Melur Bus Stop",
        facility_id: "FAC-03",
        facility_name: "Madurai District Referral Medical College Hospital",
        transport_only: true,
        consent_given: true,
        client_uuid: `tr_${now}_${Math.random().toString(36).substring(2, 7)}`
      };
    } else if (type === "COMMUNITY_DEMAND") {
      payload = {
        request_type: "COMMUNITY_DEMAND",
        village: "Valparai Hills",
        preferred_service_id: "SRV-EYE",
        preferred_service_name: "Ophthalmology / Eye Care Camp",
        notes: "Multiple elderly plantation workers requesting vision screening camp.",
        consent_given: true,
        client_uuid: `demand_${now}_${Math.random().toString(36).substring(2, 7)}`
      };
    }

    if (payload && window.offlineDB) {
      await window.offlineDB.queueRequest(payload);
      await window.offlineDB.renderSyncModalList();
    }
  }

  renderJourneyDetails(journey, readiness, events = [], isRestricted = false) {
    this.lastTrackedJourney = journey;
    const container = document.getElementById("track-result-container");
    if (!container) return;

    const status = journey.status || "REQUESTED";
    let statusClass = "pending";
    if (status === "COORDINATION_CONFIRMED") statusClass = "confirmed";
    else if (status === "RECHECK_REQUIRED" || status === "TRANSPORT_FAILED") statusClass = "recheck";

    const chk = (readiness && readiness.checks) || {};
    const isReady = readiness ? readiness.is_ready_for_departure : false;

    // Checkpoints HTML
    const checkpoints = [
      { key: "service_verified", text: window.i18n.t("chk_service_verified", "1. Requested Healthcare Service Verified"), passed: chk.service_verified },
      { key: "verification_unexpired", text: window.i18n.t("chk_validity", "2. Facility Verification Unexpired & Active"), passed: chk.verification_unexpired },
      { key: "appointment_accepted", text: window.i18n.t("chk_appointment", "3. Hospital Appointment Accepted & Slot Confirmed"), passed: chk.appointment_accepted },
      { key: "transport_accepted", text: window.i18n.t("chk_transport", "4. Driver Accepted Transport Request"), passed: chk.transport_accepted },
      { key: "pickup_destination_confirmed", text: window.i18n.t("chk_pickup_drop", "5. Pickup & Destination Confirmed"), passed: chk.pickup_destination_confirmed },
      { key: "fare_disclosed_and_accepted", text: window.i18n.t("chk_fare", "6. Fare Disclosed & Accepted by Patient"), passed: chk.fare_disclosed_and_accepted },
      { key: "patient_readiness_confirmed", text: window.i18n.t("chk_patient_ready", "7. Patient Readiness Confirmed"), passed: chk.patient_readiness_confirmed }
    ];

    const checksHtml = checkpoints.map(c => `
      <li class="checkpoint-item">
        <span class="chk-icon ${c.passed ? 'passed' : 'failed'}">${c.passed ? '✓' : '—'}</span>
        <span>${c.text}</span>
      </li>
    `).join("");

    // Timeline HTML
    const timelineHtml = (events || []).map(ev => `
      <li class="timeline-event">
        <div class="timeline-title">${ev.event_type}</div>
        <div class="timeline-meta">${new Date(ev.timestamp).toLocaleString()} • ${ev.actor}</div>
        <div class="timeline-desc">${ev.description}</div>
      </li>
    `).join("");

    // Verification prompt if restricted
    let verificationPromptHtml = "";
    if (isRestricted) {
      verificationPromptHtml = `
        <div style="background: #EFF6FF; border: 1px solid #BFDBFE; border-radius: var(--radius-md); padding: 14px; margin-bottom: 16px;">
          <div style="font-size: 14px; font-weight: 700; color: #1E40AF; margin-bottom: 6px;">
            🔒 Patient Privacy Verification Notice
          </div>
          <div style="font-size: 13px; color: #1E3A8A; margin-bottom: 10px;">
            Full patient identity details are protected. To unlock full patient information and manage journey confirmation, please verify the registered mobile number:
          </div>
          <div style="display: flex; gap: 8px; max-width: 400px;">
            <input type="tel" id="verify-phone-input" class="form-input" style="min-height: 40px; font-size: 14px;" placeholder="Enter 10-digit mobile number">
            <button class="btn btn-primary" style="min-height: 40px; padding: 6px 14px; font-size: 13px;" onclick="app.verifyAndUnlockJourney('${journey.id}')">
              Verify
            </button>
          </div>
        </div>
      `;
    }

    container.innerHTML = `
      <div style="background: var(--bg-card); border: 1px solid var(--border-light); border-radius: var(--radius-lg); padding: 24px; box-shadow: var(--shadow-sm); margin-bottom: 24px;">
        ${verificationPromptHtml}
        <div class="flex-between mb-16 flex-wrap">
          <div>
            <span style="font-size: 13px; font-weight: 700; color: var(--text-muted);">${journey.registration_method || 'CARE_JOURNEY'}</span>
            <h3 style="font-size: 24px; font-weight: 800; color: var(--primary-teal-dark);">${journey.id}</h3>
          </div>
          <div>
            <span class="status-pill ${statusClass}" style="font-size: 15px; padding: 6px 16px;">
              <span class="dot"></span> ${status}
            </span>
          </div>
        </div>

        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; font-size: 15px; background: var(--bg-subtle); padding: 18px; border-radius: var(--radius-md);">
          <div><strong>Patient:</strong> ${journey.patient_name} (${journey.age} yrs) <span style="font-size:11px; font-weight:800; background:#E0F2FE; color:#0369A1; padding:2px 7px; border-radius:4px; font-family:monospace; margin-left:4px;">${journey.patient_id || 'PID-CITIZEN'}</span></div>
          <div><strong>Contact:</strong> ${journey.contact_number.startsWith('XXXX') ? journey.contact_number : '+91 ' + journey.contact_number}</div>
          <div><strong>Village:</strong> ${journey.village}</div>
          <div><strong>Medical Service:</strong> ${journey.preferred_service_name}</div>
          <div><strong>Scheduled Date:</strong> ${journey.preferred_date}</div>
          <div><strong>Facility:</strong> ${journey.facility_name}</div>
          <div><strong>Appointment Slot:</strong> ${journey.appointment_slot || 'Pending Hospital Approval'} ${journey.patient_slot_confirmed ? '<span style="color:#059669; font-weight:800; font-size:12px;">(CONFIRMED)</span>' : ''}</div>
          <div><strong>Transport:</strong> ${journey.transport_required ? (journey.driver_name ? `${journey.driver_name} (${journey.vehicle_reg}) • ₹${journey.disclosed_fare}` : 'Awaiting Driver') : 'Not Required (Self)'}</div>
          ${journey.transport_required ? `
            <div style="grid-column: 1 / -1; font-size: 13px; color: #1E293B; background: #FFFFFF; border: 1px solid var(--border-light); border-radius: 6px; padding: 10px 14px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
              <div>
                📍 <strong>Pickup Choice:</strong> ${journey.pickup_choice === 'RURALCARE_CENTRE' ? 'RuralCare Self-Service Centre' : 'Home Doorstep'} • Landmark: <em>${journey.pickup_landmark || journey.village}</em>
              </div>
              <div>
                <button class="btn btn-outline" style="padding: 3px 10px; font-size: 11px; min-height: 28px;" onclick="app.promptPickupChoice('${journey.transport_request_id}', '${journey.village}')">
                  📍 Change Pickup Choice
                </button>
              </div>
            </div>
          ` : ''}

          <!-- Patient-Reported Need vs Doctor Clinical Assessment -->
          <div style="grid-column: 1 / -1; background: #FFFFFF; border: 1px solid var(--border-light); border-radius: 6px; padding: 10px 14px;">
            <div style="font-size: 11px; font-weight: 800; color: #64748B; text-transform: uppercase;">
              🗣️ Patient-Reported Health Problem / Symptoms (Pre-Clinical):
            </div>
            <div style="font-size: 14px; font-weight: 600; color: #0F172A; margin-top: 3px;">
              ${journey.patient_reported_symptoms || journey.preferred_service_name}
            </div>
          </div>

          ${journey.doctor_assessment ? `
            <div style="grid-column: 1 / -1; background: #F0FDF4; border: 1.5px solid #22C55E; border-radius: 6px; padding: 12px 14px;">
              <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px; margin-bottom: 6px;">
                <span style="font-size: 12px; font-weight: 800; color: #166534; text-transform: uppercase;">
                  👨‍⚕️ Attending Doctor Assessment (${journey.doctor_assessment.doctor_name || 'Medical Officer'}):
                </span>
                <span style="font-size: 11px; font-weight: 800; padding: 2px 8px; border-radius: 4px; ${journey.clinical_urgency === 'URGENT' ? 'background: #FEF3C7; color: #B45309;' : 'background: #DCFCE7; color: #166534;'}">
                  Urgency: ${journey.clinical_urgency || 'ROUTINE'} (Physician Determined)
                </span>
              </div>
              <div style="font-size: 13px; color: #14532D; line-height: 1.4;">
                <strong>Doctor Clinical Notes:</strong> ${journey.doctor_assessment.doctor_notes || 'Patient clinically evaluated.'}
              </div>
              ${journey.appointment_slot ? `
                <div style="font-size: 13px; font-weight: 700; color: #047857; margin-top: 6px;">
                  Assigned Slot: ${journey.appointment_slot} ${journey.patient_slot_confirmed ? '✓ Confirmed by Patient' : '⏳ Awaiting Patient Confirmation'}
                </div>
              ` : ''}
            </div>
          ` : ''}

          <!-- Prescription & Pharmacy Access Block -->
          ${journey.prescription_id || journey.prescription_uploaded ? `
            <div style="grid-column: 1 / -1; background: #FFFBEB; border: 1.5px solid #F59E0B; border-radius: 6px; padding: 12px 14px;">
              <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 6px;">
                <span style="font-size: 12px; font-weight: 800; color: #92400E; text-transform: uppercase;">
                  💊 Verified Digital Prescription (${journey.prescription_id || 'Active'})
                </span>
                <button class="btn btn-primary" onclick="app.openPharmacyOrderModal('${journey.prescription_id}')" style="padding: 4px 12px; font-size: 12px; min-height: 30px;">
                  Check Stock & Order Medicines →
                </button>
              </div>
              <div style="font-size: 13px; color: #78350F;">
                ${journey.medicine_order_id ? `
                  <strong style="color: #047857;">✓ Pharmacy Order Confirmed: ${journey.medicine_order_id} (${journey.medicine_order_status})</strong>
                ` : 'Doctor-authenticated prescription recorded. Verified rural pharmacy inventory ready for pickup or home delivery.'}
              </div>
            </div>
          ` : ''}

          ${journey.linked_original_journey_id ? `
            <div style="grid-column: 1 / -1; font-size: 14px; font-weight: 700; color: #0284C7; padding-top: 6px; border-top: 1px dashed var(--border-light);">
              🔗 Transferred alternative request linked to original journey: 
              <a href="javascript:void(0)" onclick="document.getElementById('track-search-input').value='${journey.linked_original_journey_id}'; app.searchJourney();" style="text-decoration: underline; color: #0369A1;">${journey.linked_original_journey_id}</a>
            </div>
          ` : ''}
          ${journey.rebooked_to_journey_id ? `
            <div style="grid-column: 1 / -1; font-size: 14px; font-weight: 700; color: #059669; padding-top: 6px; border-top: 1px dashed var(--border-light);">
              ➡️ Transferred to alternative request: 
              <a href="javascript:void(0)" onclick="document.getElementById('track-search-input').value='${journey.rebooked_to_journey_id}'; app.searchJourney();" style="text-decoration: underline; color: #047857;">${journey.rebooked_to_journey_id}</a>
            </div>
          ` : ''}
        </div>

        <!-- MAIN DIFFERENTIATING FEATURE: DEPARTURE READINESS ENGINE WIDGET -->
        <div class="readiness-widget ${isReady ? 'ready' : (status === 'RECHECK_REQUIRED' ? 'recheck' : 'pending')}">
          <div class="readiness-header">
            <div>
              <div class="readiness-title" data-i18n="departure_readiness_title">Departure Readiness Check</div>
              <div style="font-size: 14px; margin-top: 4px;">
                ${isReady ? '<strong style="color: var(--healthcare-green-dark);">' + window.i18n.t("readiness_confirmed_msg") + '</strong>' : '<strong style="color: var(--warning-amber);">' + (readiness ? readiness.explanation : window.i18n.t("readiness_pending_msg")) + '</strong>'}
              </div>
            </div>
            <div>
              <span class="status-pill ${isReady ? 'confirmed' : 'pending'}" style="font-size: 14px;">
                ${isReady ? 'SAFE FOR DEPARTURE' : 'NOT READY TO TRAVEL'}
              </span>
            </div>
          </div>

          <ul class="checkpoints-list">
            ${checksHtml}
          </ul>

          <!-- Action buttons for patient / kiosk -->
          <div style="display: flex; gap: 12px; margin-top: 18px; flex-wrap: wrap;">
            ${!isRestricted && journey.appointment_slot && !journey.patient_slot_confirmed ? `
              <button class="btn btn-success btn-lg" onclick="app.confirmPatientSlot('${journey.id}')">
                ✓ <span data-i18n="btn_confirm_slot">Confirm Doctor-Assigned Slot (${journey.appointment_slot})</span>
              </button>
            ` : ''}

            ${status === 'COORDINATION_CONFIRMED' ? `
              <button type="button" class="btn btn-primary btn-lg" onclick="app.openJourneyPassModal('${journey.id}')">
                🖨️ <span data-i18n="btn_print_pass">Print Journey Pass</span>
              </button>
            ` : ''}

            ${!isRestricted && !journey.patient_readiness_confirmed ? `
              <button class="btn btn-success" onclick="app.confirmPatientReadiness('${journey.id}')">
                ✓ <span data-i18n="btn_patient_mark_ready">Confirm I Am Ready to Travel</span>
              </button>
            ` : (journey.patient_readiness_confirmed ? `
              <span class="status-pill verified">Patient Readiness Acknowledged</span>
            ` : '')}

            ${!isRestricted && journey.transport_required && journey.disclosed_fare && !journey.patient_fare_accepted ? `
              <button class="btn btn-primary" onclick="app.acceptDriverFare('${journey.id}')">
                ₹ Accept Fare of ₹${journey.disclosed_fare}
              </button>
            ` : ''}
          </div>

          <div class="readiness-disclaimer">
            ${(readiness && readiness.disclaimer) || 'Coordination confirmation verifies scheduled readiness with participating facilities and drivers. It does not guarantee doctor attendance, treatment, waiting time or medical fitness to travel.'}
          </div>
        </div>

        <!-- Feature 3: Alternative Hospital Recommendations Container for Track View -->
        <div id="track-alt-recommendations"></div>

        <!-- Timeline of journey events -->
        <div style="margin-top: 24px;">
          <h4 style="font-size: 17px; font-weight: 700; margin-bottom: 14px;">Care Journey Timeline</h4>
          <ul class="journey-timeline">
            ${timelineHtml || '<li style="color: var(--text-muted); font-size: 14px;">Events will appear as actions occur.</li>'}
          </ul>
        </div>
      </div>
    `;

    // Trigger alternative recommendations if appointment was rejected or verification expired
    const isRejected = (journey.status === "CANCELLED" && ((journey.cancellation_reason || "").toLowerCase().includes("unavailable") || (journey.cancellation_reason || "").toLowerCase().includes("reject") || (journey.cancellation_reason || "").toLowerCase().includes("doctor")));
    const hasRejectionEvent = (events || []).some(e => e.event_type === "APPOINTMENT_REJECTED");
    const isExpiredVerification = (readiness && readiness.checks && readiness.checks.verification_unexpired === false);
    const showAltRecs = isRejected || hasRejectionEvent || isExpiredVerification || (status === "RECHECK_REQUIRED");

    if (showAltRecs) {
      setTimeout(() => {
        this.loadTrackRecommendations(journey);
      }, 60);
    }
  }

  // =========================================================================
  // Feature 3: Intelligent Alternative Hospital Recommendations
  // =========================================================================

  async loadWizardRecommendations(excludedFacilityId) {
    const serviceId = document.getElementById("touch-service-select")?.value;
    const villageVal = document.getElementById("touch-patient-village")?.value;
    const container = document.getElementById("wizard-alt-recommendations");
    if (!container || !serviceId) return;

    container.innerHTML = `<div style="padding: 16px; text-align: center; color: var(--text-muted);">${window.i18n.t("loading", "Searching for verified alternative hospitals...")}</div>`;
    container.classList.remove("hidden");

    try {
      const q = new URLSearchParams({
        service: serviceId,
        exclude_facility: excludedFacilityId || "",
        village: villageVal || ""
      });
      const res = await fetch(`/api/facilities/recommendations?${q.toString()}`);
      const data = await res.json();

      if (!data.success || !data.recommendations || data.recommendations.length === 0) {
        container.innerHTML = `
          <div class="alt-recommendations-panel warning-theme">
            <div class="alt-rec-header">
              <div>
                <div class="alt-rec-title" data-i18n="alt_recommendations_title">Alternative Hospital Recommendations</div>
                <div class="alt-rec-subtitle" data-i18n="alt_no_verified_found">
                  ${window.i18n.t("alt_no_verified_found", "No currently verified alternative facility was found. Please contact a health worker for assistance.")}
                </div>
              </div>
              <button type="button" class="btn-speaker" onclick="app.speakText(window.i18n.t('alt_no_verified_found', 'No currently verified alternative facility was found. Please contact a health worker for assistance.'))" aria-label="Read aloud">🔊</button>
            </div>
          </div>
        `;
        return;
      }

      this.renderRecommendationCards(container, data.recommendations, "wizard");
    } catch (err) {
      console.warn("Could not load wizard recommendations:", err);
      container.classList.add("hidden");
    }
  }

  async loadTrackRecommendations(journey) {
    const container = document.getElementById("track-alt-recommendations");
    if (!container) return;

    container.innerHTML = `<div style="padding: 16px; text-align: center; color: var(--text-muted);">${window.i18n.t("loading", "Searching for verified alternative hospitals...")}</div>`;

    try {
      const q = new URLSearchParams({
        service: journey.preferred_service_id || "",
        exclude_facility: journey.facility_id || "",
        village: journey.village || ""
      });
      const res = await fetch(`/api/facilities/recommendations?${q.toString()}`);
      const data = await res.json();

      if (!data.success || !data.recommendations || data.recommendations.length === 0) {
        container.innerHTML = `
          <div class="alt-recommendations-panel warning-theme">
            <div class="alt-rec-header">
              <div>
                <div class="alt-rec-title" data-i18n="alt_recommendations_title">Alternative Hospital Recommendations</div>
                <div class="alt-rec-subtitle" data-i18n="alt_no_verified_found">
                  ${window.i18n.t("alt_no_verified_found", "No currently verified alternative facility was found. Please contact a health worker for assistance.")}
                </div>
              </div>
              <button type="button" class="btn-speaker" onclick="app.speakText(window.i18n.t('alt_no_verified_found', 'No currently verified alternative facility was found. Please contact a health worker for assistance.'))" aria-label="Read aloud">🔊</button>
            </div>
          </div>
        `;
        return;
      }

      this.renderRecommendationCards(container, data.recommendations, "track", journey);
    } catch (err) {
      console.warn("Could not load track recommendations:", err);
      container.innerHTML = "";
    }
  }

  renderRecommendationCards(containerEl, recommendations, context = "wizard", originalJourney = null) {
    const cardsHtml = recommendations.map(rec => {
      let badgeClass = "alt-badge-verified";
      let badgeLabel = window.i18n.t("badge_verified_alternative", "VERIFIED ALTERNATIVE");
      let cardBorderClass = "verified-alt";

      if (rec.recommendation_label === "SCHEDULED OPTION") {
        badgeClass = "alt-badge-scheduled";
        badgeLabel = window.i18n.t("badge_scheduled_option", "SCHEDULED OPTION");
        cardBorderClass = "scheduled-alt";
      } else if (rec.recommendation_label === "UNVERIFIED OPTION") {
        badgeClass = "alt-badge-unverified";
        badgeLabel = window.i18n.t("badge_unverified_option", "UNVERIFIED OPTION");
        cardBorderClass = "unverified-alt";
      }

      const distHtml = rec.distance_text ? `
        <span class="alt-distance-pill">📍 ${rec.distance_text}</span>
      ` : (rec.is_same_district ? `<span class="alt-distance-pill" style="background:#E0E7FF; color:#4338CA;">📍 Same District (${rec.district})</span>` : '');

      const chooseAction = context === "wizard" 
        ? `app.chooseAlternativeFacilityInWizard('${rec.id}')`
        : `app.confirmAndRebookAlternative('${originalJourney ? originalJourney.id : ''}', '${rec.id}', '${(rec.name || '').replace(/'/g, "\\'")}')`;

      const safeName = (rec.name || '').replace(/'/g, "\\'");
      const speakCardText = `${safeName}. ${rec.facility_type}. ${rec.availability_info}.`;

      return `
        <div class="alt-rec-card ${cardBorderClass}">
          <div class="alt-rec-card-info">
            <div style="display: flex; align-items: center; flex-wrap: wrap; gap: 8px;">
              <span class="alt-badge-tag ${badgeClass}">${badgeLabel}</span>
              ${distHtml}
              <button type="button" class="btn-speaker" onclick="event.stopPropagation(); app.speakText('${speakCardText}')" aria-label="Listen to alternative">🔊</button>
            </div>
            <div class="alt-rec-name">${rec.name}</div>
            <div class="alt-rec-meta">${rec.facility_type} • ${rec.location}</div>
            <div style="font-size: 14px; color: var(--text-dark); margin-top: 4px;">
              <strong>Availability:</strong> ${rec.availability_info}
            </div>
            <div style="font-size: 13px; color: var(--text-muted); margin-top: 2px;">
              Verified by: ${rec.verified_by_staff || 'Staff Record'} • Source: ${rec.availability_source}
            </div>
          </div>
          <div>
            <button type="button" class="btn btn-primary btn-lg" onclick="${chooseAction}" data-i18n="btn_choose_facility">
              ✓ ${window.i18n.t("btn_choose_facility", "Choose This Facility")}
            </button>
          </div>
        </div>
      `;
    }).join("");

    containerEl.innerHTML = `
      <div class="alt-recommendations-panel">
        <div class="alt-rec-header">
          <div>
            <div class="alt-rec-title" data-i18n="alt_recommendations_title">Alternative Hospital Recommendations</div>
            <div class="alt-rec-subtitle" data-i18n="alt_facility_cannot_confirm">
              ${window.i18n.t("alt_facility_cannot_confirm", "Your selected facility cannot currently confirm this service. RuralCare found these alternatives.")}
            </div>
          </div>
          <button type="button" class="btn-speaker" onclick="app.speakText(window.i18n.t('alt_facility_cannot_confirm', 'Your selected facility cannot currently confirm this service. RuralCare found these alternatives.'))" aria-label="Read aloud">🔊</button>
        </div>
        <div class="alt-rec-cards-list">
          ${cardsHtml}
        </div>
      </div>
    `;
  }

  chooseAlternativeFacilityInWizard(facilityId) {
    const fac = this.facilities.find(f => f.id === facilityId);
    if (!fac) {
      fetch(`/api/facilities/${facilityId}`)
        .then(r => r.json())
        .then(data => {
          if (data.success && data.facility) {
            this.facilities.push(data.facility);
            this.applyAlternativeChoiceInWizard(data.facility);
          }
        })
        .catch(err => console.error("Could not fetch facility:", err));
    } else {
      this.applyAlternativeChoiceInWizard(fac);
    }
  }

  applyAlternativeChoiceInWizard(fac) {
    this.selectedFacility = fac;
    this.filterFacilitiesForService();
    const container = document.getElementById("wizard-alt-recommendations");
    if (container) {
      container.innerHTML = `
        <div style="background: #ECFDF5; border: 2px solid #059669; border-radius: var(--radius-md); padding: 14px; margin-top: 14px; color: #065F46; font-weight: 700;">
          ✓ Successfully selected verified alternative: ${fac.name}. You may continue.
        </div>
      `;
    }
  }

  async confirmAndRebookAlternative(originalJourneyId, altFacilityId, altFacilityName) {
    const ok = confirm(`Do you want to request an outpatient appointment at ${altFacilityName}?\n\nYour original journey (${originalJourneyId}) history will be preserved.`);
    if (!ok) return;

    try {
      const res = await fetch(`/api/journeys/${originalJourneyId}/rebook-alternative`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alternative_facility_id: altFacilityId })
      });
      const data = await res.json();
      if (data.success && data.new_journey) {
        alert(`New appointment request created at ${altFacilityName}!\nNew Care Journey ID: ${data.new_journey.id}`);
        this.activeJourneyId = data.new_journey.id;
        const searchInput = document.getElementById("track-search-input");
        if (searchInput) searchInput.value = data.new_journey.id;
        this.searchJourney();
      } else {
        alert(data.error || "Could not rebook alternative facility.");
      }
    } catch (err) {
      console.error("Rebook error:", err);
      alert("Error submitting alternative request. Please try again.");
    }
  }

  // =========================================================================
  // Printable Journey Pass Modal
  // =========================================================================

  openJourneyPassModal(journeyId) {
    const modal = document.getElementById("modal-journey-pass");
    if (!modal) return;

    const populateAndShow = (journey) => {
      this.populateJourneyPass(journey);
      modal.classList.remove("hidden");
    };

    if (this.lastTrackedJourney && this.lastTrackedJourney.id === journeyId) {
      populateAndShow(this.lastTrackedJourney);
    } else {
      const verifyParam = this.activePatientPhone ? `?verify_phone=${encodeURIComponent(this.activePatientPhone)}` : "";
      fetch(`/api/journeys/${journeyId}${verifyParam}`)
        .then(r => r.json())
        .then(data => {
          if (data.success && data.journey) {
            populateAndShow(data.journey);
          } else {
            alert("Could not load journey pass details.");
          }
        })
        .catch(err => {
          console.error("Error loading journey for pass:", err);
        });
    }
  }

  populateJourneyPass(journey) {
    const setText = (id, text) => {
      const el = document.getElementById(id);
      if (el) el.innerText = text || "-";
    };

    setText("jp-journey-id", journey.id);
    setText("jp-patient-info", `${journey.patient_name || 'Patient'} (${journey.age || '-'} yrs) • ${journey.village || '-'}`);
    setText("jp-facility-name", journey.facility_name || "Pollachi Government Hospital");
    setText("jp-service", journey.preferred_service_name || journey.requested_service || "General Outpatient (OP)");
    setText("jp-date", journey.preferred_date || "-");
    setText("jp-slot", journey.appointment_slot || "Morning OP Slot (09:30 AM - 12:30 PM)");

    const transBox = document.getElementById("jp-transport-box");
    if (journey.transport_required) {
      if (transBox) transBox.style.display = "block";
      setText("jp-driver-name", journey.driver_name || "Murugan S.");
      setText("jp-driver-reg", journey.vehicle_reg || "TN-38-AX-4412 (Auto)");
      setText("jp-pickup-landmark", journey.pickup_landmark || "Village Panchayat Shelter");
      setText("jp-fare", `₹${journey.disclosed_fare || 120} (Upfront Fixed)`);
    } else {
      if (transBox) {
        transBox.style.display = "block";
        setText("jp-driver-name", "Self-Arranged Transport");
        setText("jp-driver-reg", "Patient Independent Travel");
        setText("jp-pickup-landmark", "Direct arrival at hospital");
        setText("jp-fare", "₹0 (No Driver Requested)");
      }
    }

    const ts = journey.updated_at ? new Date(journey.updated_at).toLocaleString() : new Date().toLocaleString();
    setText("jp-timestamp", ts);
  }

  closeJourneyPassModal() {
    const modal = document.getElementById("modal-journey-pass");
    if (modal) modal.classList.add("hidden");
  }

  verifyAndUnlockJourney(journeyId) {
    const input = document.getElementById("verify-phone-input")?.value.trim();
    if (!input || input.length < 4) {
      alert("Please enter the patient's registered mobile number or last 4 digits.");
      return;
    }
    this.activePatientPhone = input;
    this.searchJourney();
  }

  async confirmPatientReadiness(journeyId) {
    try {
      const verifyQuery = this.activePatientPhone ? `?verify_phone=${encodeURIComponent(this.activePatientPhone)}` : "";
      const res = await fetch(`/api/journeys/${journeyId}/patient-readiness${verifyQuery}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ patient_readiness_confirmed: true })
      });
      const data = await res.json();
      if (data.success) {
        this.searchJourney();
      } else {
        alert(data.error || "Could not confirm readiness");
      }
    } catch (e) {
      alert("Error confirming readiness");
    }
  }

  async acceptDriverFare(journeyId) {
    try {
      const res = await fetch(`/api/journeys/${journeyId}/patient-accept-fare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accepted: true })
      });
      const data = await res.json();
      if (data.success) {
        this.searchJourney();
      } else {
        alert(data.error || "Could not accept fare");
      }
    } catch (e) {
      alert("Error accepting fare");
    }
  }

  async promptPickupChoice(trId, defaultVillage) {
    if (!trId) {
      alert("Transport request reference not found.");
      return;
    }
    const isCentre = confirm("Select Transport Pickup Location:\n\n• Click OK for RuralCare Self-Service Centre in village (Convenient pickup point)\n• Click Cancel for Patient Home Doorstep Address");
    const choice = isCentre ? "RURALCARE_CENTRE" : "HOME_ADDRESS";
    let landmark = "";
    if (choice === "HOME_ADDRESS") {
      landmark = prompt("Enter your Home Doorstep address or landmark (e.g. Near Mariamman Temple):", `Patient Residence, ${defaultVillage || 'Village'}`);
      if (!landmark) return;
    }
    await this.selectPickupLocation(trId, choice, landmark);
  }

  async selectPickupLocation(trId, choice, landmark = "") {
    try {
      const res = await fetch(`/api/transport/requests/${trId}/pickup-location`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pickup_choice: choice,
          pickup_landmark: landmark
        })
      });
      const data = await res.json();
      if (data.success) {
        alert(`✓ Pickup location confirmed: ${data.pickup_landmark || choice}`);
        this.searchJourney();
      } else {
        alert(data.error || "Failed to update pickup location");
      }
    } catch (e) {
      alert("Error updating pickup location");
    }
  }

  // =========================================================================
  // Facility Staff & Doctor Portal (Phase 5 & 6)
  // =========================================================================
  async loadStaffRequests() {
    const tbody = document.getElementById("staff-requests-tbody");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="8" class="text-center">${window.i18n.t("loading")}</td></tr>`;

    try {
      const curFac = this.facilities.find(f => f.id === this.activeFacilityId) || this.facilities[0];
      if (curFac) {
        const titleEl = document.getElementById("staff-portal-title");
        const subEl = document.getElementById("staff-portal-subtitle");
        if (titleEl) titleEl.textContent = `${curFac.name} (Doctor & Staff Portal)`;
        if (subEl) subEl.textContent = `Active Doctor: Dr. K. Revathi (CMO) • Status: ${curFac.verification_status} • Valid until: ${curFac.verification_valid_until ? new Date(curFac.verification_valid_until).toLocaleString() : 'N/A'}`;
      }

      const res = await fetch(`/api/facilities/${this.activeFacilityId}/assigned-journeys`);
      const data = await res.json();
      const journeys = data.success ? data.journeys : [];

      tbody.innerHTML = "";
      if (journeys.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center" style="padding: 24px; color: var(--text-muted);">No incoming requests for this facility.</td></tr>`;
        return;
      }

      journeys.forEach(j => {
        const tr = document.createElement("tr");
        const urgency = j.clinical_urgency || "PENDING";
        let urgencyBadge = `<span style="font-size: 11px; padding: 2px 7px; border-radius: 4px; background: #F1F5F9; color: #475569; font-weight: 700;">PENDING REVIEW</span>`;
        if (urgency === "URGENT") {
          urgencyBadge = `<span style="font-size: 11px; padding: 2px 7px; border-radius: 4px; background: #FEF3C7; color: #B45309; font-weight: 800;">URGENT</span>`;
        } else if (urgency === "ROUTINE") {
          urgencyBadge = `<span style="font-size: 11px; padding: 2px 7px; border-radius: 4px; background: #DCFCE7; color: #166534; font-weight: 700;">ROUTINE</span>`;
        } else if (urgency === "MONITORING") {
          urgencyBadge = `<span style="font-size: 11px; padding: 2px 7px; border-radius: 4px; background: #EFF6FF; color: #1D4ED8; font-weight: 700;">MONITORING</span>`;
        }

        tr.innerHTML = `
          <td>
            <strong>${j.id}</strong>
            <div style="font-size: 11px; font-family: monospace; color: #0284C7;">${j.patient_id}</div>
          </td>
          <td>
            <strong>${j.patient_name}</strong> (${j.age} yrs)
            <div style="font-size: 11px; color: #64748B;">📞 ${j.patient_phone}</div>
          </td>
          <td>${j.village}</td>
          <td>
            <div style="font-weight: 700; color: #0F172A;">${j.preferred_service_name}</div>
            <div style="font-size: 11px; color: #475569;"><em>Need: ${j.patient_reported_symptoms || '-'}</em></div>
          </td>
          <td>${urgencyBadge}</td>
          <td>
            <div style="font-weight: 600;">${j.appointment_slot || 'Pending'}</div>
            <div style="font-size: 11px; color: #64748B;">${j.preferred_date}</div>
          </td>
          <td><span class="status-pill ${j.status === 'COORDINATION_CONFIRMED' || j.status === 'APPOINTMENT_ACCEPTED' ? 'confirmed' : 'pending'}">${j.status}</span></td>
          <td>
            <div style="display: flex; gap: 6px; flex-wrap: wrap;">
              <button class="btn btn-primary" style="padding: 4px 10px; min-height: 32px; font-size: 12px; font-weight: 700;" onclick="app.openDoctorReviewModal('${j.id}')">
                👨‍⚕️ Review
              </button>
              ${j.status !== 'CANCELLED' ? `
                <button class="btn btn-secondary" style="padding: 4px 10px; min-height: 32px; font-size: 12px;" onclick="app.staffRejectAppointment('${j.id}')">
                  Reject
                </button>
              ` : ''}
            </div>
          </td>
        `;
        tbody.appendChild(tr);
      });
    } catch (e) {
      console.error(e);
      tbody.innerHTML = `<tr><td colspan="8" class="text-center">Failed to load assigned requests.</td></tr>`;
    }
  }

  async openDoctorReviewModal(journeyId) {
    this.currentReviewJourneyId = journeyId;
    try {
      const res = await fetch(`/api/journeys/${journeyId}`);
      const data = await res.json();
      if (!data.success || !data.journey) {
        alert("Journey record not found");
        return;
      }
      const j = data.journey;
      document.getElementById("doc-review-jid").textContent = j.id;
      document.getElementById("doc-review-pid").textContent = j.patient_id || "PID-CITIZEN";
      document.getElementById("doc-review-pname").textContent = `${j.patient_name} (${j.age} yrs)`;
      document.getElementById("doc-review-phone").textContent = j.contact_number.startsWith("XXXX") ? j.contact_number : ("XXXXXX" + j.contact_number.slice(-4));
      document.getElementById("doc-review-village").textContent = j.village;
      document.getElementById("doc-review-date").textContent = j.preferred_date;
      document.getElementById("doc-review-symptoms").textContent = j.patient_reported_symptoms || j.preferred_service_name || "Outpatient Consultation";

      if (j.appointment_slot) {
        document.getElementById("doc-slot-select").value = j.appointment_slot;
      }
      if (j.clinical_urgency) {
        const r = document.querySelector(`input[name="doc-urgency"][value="${j.clinical_urgency}"]`);
        if (r) r.checked = true;
      }
      document.getElementById("doc-clinical-notes").value = (j.doctor_assessment && j.doctor_assessment.doctor_notes) || "";
      document.getElementById("doc-prescription-notes").value = "";

      document.getElementById("modal-doctor-review").classList.remove("hidden");
    } catch (e) {
      alert("Could not load journey details for review");
    }
  }

  closeDoctorReviewModal() {
    document.getElementById("modal-doctor-review").classList.add("hidden");
    this.currentReviewJourneyId = null;
  }

  toggleDoctorActionFields(isAccept) {
    const slotContainer = document.getElementById("doc-slot-container");
    const rxContainer = document.getElementById("doc-rx-container");
    if (slotContainer) slotContainer.style.display = isAccept ? "block" : "none";
    if (rxContainer) rxContainer.style.display = isAccept ? "block" : "none";
  }

  async submitDoctorReview() {
    if (!this.currentReviewJourneyId) return;
    const actionEl = document.querySelector('input[name="doc-action"]:checked');
    const urgencyEl = document.querySelector('input[name="doc-urgency"]:checked');
    const slot = document.getElementById("doc-slot-select").value;
    const doctorNotes = document.getElementById("doc-clinical-notes").value;
    const prescriptionNotes = document.getElementById("doc-prescription-notes").value;

    const action = actionEl ? actionEl.value : "accept";
    const clinicalUrgency = urgencyEl ? urgencyEl.value : "ROUTINE";

    try {
      const res = await fetch("/api/facilities/doctor/review", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-User-Username": "dr.revathi@pollachigh.tn.gov.in"
        },
        body: JSON.stringify({
          journey_id: this.currentReviewJourneyId,
          doctor_name: "Dr. K. Revathi (CMO)",
          action: action,
          clinical_urgency: clinicalUrgency,
          appointment_slot: slot,
          doctor_notes: doctorNotes,
          prescription_notes: prescriptionNotes
        })
      });

      const data = await res.json();
      if (!data.success) {
        if (data.code === "SLOT_CONFLICT") {
          alert(`⚠️ Slot Double-Booking Conflict: ${data.error}`);
        } else {
          alert(`Review failed: ${data.error || 'Server error'}`);
        }
        return;
      }

      alert(`✓ Doctor review completed successfully. Urgency set to ${clinicalUrgency}. Slot: ${slot || 'N/A'}`);
      this.closeDoctorReviewModal();
      this.loadStaffRequests();
      if (this.currentView === "track") {
        this.searchJourney();
      }
    } catch (e) {
      alert("Network error submitting doctor review");
    }
  }

  async confirmPatientSlot(journeyId) {
    try {
      const res = await fetch(`/api/journeys/${journeyId}/patient-confirm-slot`, {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      });
      const data = await res.json();
      if (data.success) {
        alert("✓ Appointment slot confirmed by patient. Departure readiness evaluated.");
        this.searchJourney();
      } else {
        alert(data.error || "Slot confirmation failed");
      }
    } catch (e) {
      alert("Error confirming slot");
    }
  }

  // =========================================================================
  // Citizen Identity & Patient Portal (Phase 2)
  // =========================================================================
  async openPatientAuthModal() {
    const modal = document.getElementById("modal-patient-auth");
    if (!modal) return;

    try {
      const res = await fetch("/api/patients/me");
      const data = await res.json();
      if (data.success && data.patient) {
        this.renderPatientProfileModal(data.patient, data.active_journey, data.journeys || []);
      } else {
        document.getElementById("patient-auth-tabs-container").classList.remove("hidden");
        document.getElementById("patient-profile-view").classList.add("hidden");
      }
    } catch (e) {
      document.getElementById("patient-auth-tabs-container").classList.remove("hidden");
      document.getElementById("patient-profile-view").classList.add("hidden");
    }

    modal.classList.remove("hidden");
  }

  closePatientAuthModal() {
    const modal = document.getElementById("modal-patient-auth");
    if (modal) modal.classList.add("hidden");
  }

  switchPatientAuthTab(tab) {
    const loginBtn = document.getElementById("tab-patient-login-btn");
    const regBtn = document.getElementById("tab-patient-register-btn");
    const loginForm = document.getElementById("form-patient-login");
    const regForm = document.getElementById("form-patient-register");

    if (tab === "login") {
      loginBtn.classList.add("active");
      regBtn.classList.remove("active");
      loginForm.classList.remove("hidden");
      regForm.classList.add("hidden");
    } else {
      regBtn.classList.add("active");
      loginBtn.classList.remove("active");
      regForm.classList.remove("hidden");
      loginForm.classList.add("hidden");
    }
  }

  async loginPatient() {
    const idVal = document.getElementById("patient-login-id").value.trim();
    const pinVal = document.getElementById("patient-login-pin").value.trim();

    if (!idVal) {
      alert("Please provide your Patient ID or 10-digit mobile number.");
      return;
    }

    try {
      const res = await fetch("/api/patients/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          patient_id: idVal.toUpperCase().startsWith("PID") ? idVal : "",
          contact_number: !idVal.toUpperCase().startsWith("PID") ? idVal : "",
          phone_verification: pinVal
        })
      });

      const data = await res.json();
      if (!data.success) {
        alert(data.error || "Citizen login failed");
        return;
      }

      this.activePatient = data.patient;
      this.updateCitizenSessionBanner(data.patient, data.active_journey);
      this.renderPatientProfileModal(data.patient, data.active_journey, data.journeys, data.prescriptions);
      alert(`Welcome back, ${data.patient.name}. Connected to ${data.journeys_count || 0} care journey records.`);
    } catch (e) {
      alert("Citizen login network error");
    }
  }

  async registerPatient() {
    const name = document.getElementById("patient-reg-name").value.trim();
    const age = document.getElementById("patient-reg-age").value.trim();
    const phone = document.getElementById("patient-reg-phone").value.trim();
    const village = document.getElementById("patient-reg-village").value;
    const consent = document.getElementById("patient-reg-consent").checked;

    if (!name || !age || !phone) {
      alert("Please complete all required fields (Name, Age, Phone).");
      return;
    }

    if (!consent) {
      alert("Informed patient consent is required to register.");
      return;
    }

    if (!navigator.onLine) {
      const offlinePatient = {
        id: "PID-OFFLINE-" + Math.floor(1000 + Math.random() * 9000),
        name: name,
        age: parseInt(age),
        contact_number: phone,
        village: village,
        consent_given: true,
        offline_captured: true
      };
      if (window.offlineDB) await window.offlineDB.queuePatientRegistration(offlinePatient);
      this.activePatient = offlinePatient;
      this.updateCitizenSessionBanner(offlinePatient, null);
      this.renderPatientProfileModal(offlinePatient, null, []);
      alert(`✓ Citizen registration saved locally on this device!\nTemporary Offline ID: ${offlinePatient.id}\nIt will automatically synchronize with the central database when connected.`);
      return;
    }

    try {
      const res = await fetch("/api/patients/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name,
          age: parseInt(age),
          contact_number: phone,
          village: village,
          consent_given: true
        })
      });

      const data = await res.json();
      if (!data.success) {
        alert(data.error || "Registration failed");
        return;
      }

      this.activePatient = data.patient;
      this.updateCitizenSessionBanner(data.patient, null);
      this.renderPatientProfileModal(data.patient, null, []);
      alert(`✓ Citizen registration completed successfully!\nYour unique Patient ID is: ${data.patient.id}`);
    } catch (e) {
      console.warn("Network error during registration, saving offline:", e);
      const offlinePatient = {
        id: "PID-OFFLINE-" + Math.floor(1000 + Math.random() * 9000),
        name: name,
        age: parseInt(age),
        contact_number: phone,
        village: village,
        consent_given: true,
        offline_captured: true
      };
      if (window.offlineDB) await window.offlineDB.queuePatientRegistration(offlinePatient);
      this.activePatient = offlinePatient;
      this.updateCitizenSessionBanner(offlinePatient, null);
      this.renderPatientProfileModal(offlinePatient, null, []);
      alert(`✓ Citizen registration saved locally on this device!\nTemporary Offline ID: ${offlinePatient.id}\nIt will automatically synchronize with the central database when connected.`);
    }
  }

  async logoutPatient() {
    try {
      await fetch("/api/patients/logout", { method: "POST" });
      this.activePatient = null;
      const banner = document.getElementById("citizen-account-banner");
      if (banner) banner.classList.add("hidden");
      const label = document.getElementById("header-patient-label");
      if (label) label.textContent = "Citizen Login";
      this.closePatientAuthModal();
      alert("Logged out of citizen session.");
    } catch (e) {
      this.closePatientAuthModal();
    }
  }

  updateCitizenSessionBanner(patient, activeJourney) {
    const banner = document.getElementById("citizen-account-banner");
    const label = document.getElementById("header-patient-label");
    if (!banner || !patient) return;

    document.getElementById("banner-patient-name").textContent = patient.name;
    document.getElementById("banner-patient-id").textContent = patient.id;
    document.getElementById("banner-patient-village").textContent = patient.village;

    const jText = document.getElementById("banner-active-journey-text");
    if (activeJourney) {
      jText.textContent = `Active Request: ${activeJourney.id} (${activeJourney.preferred_service_name} • ${activeJourney.status})`;
      this.activePatientJourneyId = activeJourney.id;
    } else {
      jText.textContent = "No pending journey requests. Ready to request appointments.";
      this.activePatientJourneyId = null;
    }

    banner.classList.remove("hidden");
    if (label) label.textContent = patient.id;
  }

  renderPatientProfileModal(patient, activeJourney, journeys = [], prescriptions = []) {
    document.getElementById("patient-auth-tabs-container").classList.add("hidden");
    const profileView = document.getElementById("patient-profile-view");
    profileView.classList.remove("hidden");

    document.getElementById("prof-patient-name").textContent = patient.name;
    document.getElementById("prof-patient-id").textContent = patient.id;
    document.getElementById("prof-patient-age").textContent = patient.age;
    document.getElementById("prof-patient-village").textContent = patient.village;

    // Active Journey Card
    const actCard = document.getElementById("prof-active-journey-card");
    if (activeJourney) {
      actCard.classList.remove("hidden");
      document.getElementById("prof-active-status-badge").textContent = activeJourney.status;
      document.getElementById("prof-active-journey-details").innerHTML = `
        <strong>${activeJourney.id}:</strong> ${activeJourney.preferred_service_name} at <strong>${activeJourney.facility_name}</strong><br>
        Date: ${activeJourney.preferred_date} • Transport: ${activeJourney.transport_required ? 'Required' : 'Self'}
      `;
      this.activePatientJourneyId = activeJourney.id;
    } else {
      actCard.classList.add("hidden");
    }

    // Prescriptions List
    const rxList = document.getElementById("prof-prescriptions-list");
    rxList.innerHTML = "";
    if (!prescriptions || prescriptions.length === 0) {
      rxList.innerHTML = `<div style="font-size: 13px; color: var(--text-muted);">No active prescriptions recorded yet.</div>`;
    } else {
      prescriptions.forEach(rx => {
        const div = document.createElement("div");
        div.style.cssText = "background: #FFFDF5; border: 1px solid #FDE68A; border-radius: 6px; padding: 10px 14px; font-size: 13px;";
        const medNames = (rx.medicines || []).map(m => m.name).join(", ");
        div.innerHTML = `
          <div class="flex-between mb-4">
            <strong style="color: #92400E;">💊 ${rx.id} • ${rx.doctor_name || 'Medical Officer'}</strong>
            <button class="btn btn-primary" onclick="app.openPharmacyOrderModal('${rx.id}')" style="padding: 2px 8px; font-size: 11px; min-height: 24px;">Order Medicines →</button>
          </div>
          <div style="color: #78350F;">${medNames || 'Prescribed medicines'}</div>
          <div style="font-size: 11px; color: #B45309; margin-top: 4px;">Valid until: ${rx.valid_until || '30 days'} • Refill reminder: ${rx.refill_reminder_scheduled ? 'Active' : 'Pending'}</div>
        `;
        rxList.appendChild(div);
      });
    }

    // Past Journeys List
    const jHist = document.getElementById("prof-journeys-history");
    jHist.innerHTML = "";
    if (!journeys || journeys.length === 0) {
      jHist.innerHTML = `<div style="font-size: 13px; color: var(--text-muted);">No past journey history.</div>`;
    } else {
      journeys.forEach(j => {
        const item = document.createElement("div");
        item.style.cssText = "border-bottom: 1px solid var(--border-light); padding: 8px 0; font-size: 13px; display: flex; justify-content: space-between; align-items: center;";
        item.innerHTML = `
          <div>
            <strong>${j.id}</strong> • ${j.preferred_service_name} • <span style="color: var(--text-muted);">${j.preferred_date}</span>
          </div>
          <button class="btn btn-outline" style="padding: 2px 8px; font-size: 11px; min-height: 24px;" onclick="app.closePatientAuthModal(); document.getElementById('track-search-input').value='${j.id}'; app.showView('track'); app.searchJourney();">
            View
          </button>
        `;
        jHist.appendChild(item);
      });
    }
  }

  viewPatientActiveJourney() {
    if (this.activePatientJourneyId) {
      this.closePatientAuthModal();
      document.getElementById("track-search-input").value = this.activePatientJourneyId;
      this.showView("track");
      this.searchJourney();
    } else {
      alert("No active journey found. Please request an appointment first.");
    }
  }

  async promptUploadPrescription() {
    const medText = prompt("Enter Prescribed Medications from your Doctor's Prescription (e.g. Paracetamol 650mg, Amoxicillin 500mg):", "Paracetamol 650mg, 1 tablet as needed, 5 days\nAmoxicillin 500mg, 1 tablet twice daily, 5 days");
    if (!medText) return;
    const docName = prompt("Enter Prescribing Doctor or Clinic Name:", "Dr. K. Revathi (General Physician)");

    const rxPayload = {
      patient_id: this.activePatient ? this.activePatient.id : null,
      patient_name: this.activePatient ? this.activePatient.name : "Citizen Patient",
      doctor_name: docName || "Attending Doctor",
      medicines_text: medText,
      valid_days: 30
    };

    if (!navigator.onLine) {
      const offlineRx = {
        ...rxPayload,
        id: "RX-OFFLINE-" + Math.floor(1000 + Math.random() * 9000),
        offline_captured: true
      };
      if (window.offlineDB) await window.offlineDB.queuePrescriptionUpload(offlineRx);
      alert(`✓ Prescription saved locally in offline storage!\nTemporary ID: ${offlineRx.id}\nIt will automatically synchronize when internet connectivity returns.`);
      return;
    }

    try {
      const res = await fetch("/api/prescriptions/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(rxPayload)
      });
      const data = await res.json();
      if (data.success) {
        alert(`✓ Prescription ${data.prescription.id} uploaded and verified!\nYou can now order medicines from participating rural pharmacies.`);
        this.openPatientAuthModal();
      } else {
        alert(data.error || "Prescription upload failed");
      }
    } catch (e) {
      console.warn("Prescription upload network failure, saving offline:", e);
      const offlineRx = {
        ...rxPayload,
        id: "RX-OFFLINE-" + Math.floor(1000 + Math.random() * 9000),
        offline_captured: true
      };
      if (window.offlineDB) await window.offlineDB.queuePrescriptionUpload(offlineRx);
      alert(`✓ Prescription saved locally in offline storage!\nTemporary ID: ${offlineRx.id}\nIt will automatically synchronize when internet connectivity returns.`);
    }
  }

  // =========================================================================
  // Pharmacy Medicine Order & Stock Verification (Phase 8)
  // =========================================================================
  async openPharmacyOrderModal(rxId) {
    this.currentRxId = rxId;
    const modal = document.getElementById("modal-pharmacy-order");
    if (!modal) return;

    try {
      const rxRes = await fetch(`/api/prescriptions/${rxId}`);
      const rxData = await rxRes.json();
      if (!rxData.success || !rxData.prescription) {
        alert("Prescription not found");
        return;
      }
      this.currentRx = rxData.prescription;

      const summaryEl = document.getElementById("pharm-rx-summary");
      const meds = this.currentRx.medicines || [];
      summaryEl.innerHTML = `
        <div class="flex-between mb-6">
          <strong style="color: #0F172A; font-size: 15px;">Prescription: ${this.currentRx.id}</strong>
          <span style="font-size: 11px; font-weight: 800; background: #DCFCE7; color: #166534; padding: 2px 8px; border-radius: 4px;">VERIFIED</span>
        </div>
        <div style="font-size: 13px; color: #475569;">
          Doctor: <strong>${this.currentRx.doctor_name || 'Attending Physician'}</strong> • Facility: ${this.currentRx.facility_name || 'Taluk Hospital'}<br>
          Prescribed Medicines: <strong>${meds.map(m => m.name).join(", ")}</strong>
        </div>
      `;

      await this.onPharmacySelected();
      modal.classList.remove("hidden");
    } catch (e) {
      alert("Could not load prescription for pharmacy ordering");
    }
  }

  closePharmacyOrderModal() {
    const modal = document.getElementById("modal-pharmacy-order");
    if (modal) modal.classList.add("hidden");
    this.currentRxId = null;
    this.currentRx = null;
  }

  async onPharmacySelected() {
    const pharmId = document.getElementById("pharm-select").value;
    const previewEl = document.getElementById("pharm-stock-preview");
    if (!previewEl || !this.currentRx) return;

    previewEl.innerHTML = `<div style="font-size: 13px; color: var(--text-muted);">Checking pharmacy inventory...</div>`;
    try {
      const res = await fetch("/api/prescriptions/pharmacies");
      const data = await res.json();
      const pharm = (data.pharmacies || []).find(p => p.id === pharmId);

      if (!pharm) {
        previewEl.innerHTML = `<div style="font-size: 13px; color: #DC2626;">Pharmacy stock data unavailable.</div>`;
        return;
      }

      const pharmStockMap = {};
      (pharm.stock || []).forEach(s => {
        pharmStockMap[s.name.toLowerCase()] = s;
      });

      const meds = this.currentRx.medicines || [];
      let allAvailable = true;
      let stockItemsHtml = meds.map(m => {
        const mLower = m.name.toLowerCase();
        let matched = null;
        for (const k in pharmStockMap) {
          if (k.includes(mLower) || mLower.includes(k)) {
            matched = pharmStockMap[k];
            break;
          }
        }
        const status = matched ? matched.status : "IN_STOCK";
        if (status === "OUT_OF_STOCK") allAvailable = false;
        const color = status === "IN_STOCK" ? "#059669" : (status === "OUT_OF_STOCK" ? "#DC2626" : "#D97706");
        return `
          <div style="display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px dashed #E2E8F0; font-size: 13px;">
            <span>💊 ${m.name} (${m.dosage || 'Standard dosage'})</span>
            <strong style="color: ${color};">${status}</strong>
          </div>
        `;
      }).join("");

      previewEl.innerHTML = `
        <div style="font-size: 12px; font-weight: 800; color: #475569; text-transform: uppercase; margin-bottom: 8px;">
          Live Pharmacy Stock Verification (${pharm.name})
        </div>
        ${stockItemsHtml}
        <div style="margin-top: 10px; font-size: 12px; color: ${allAvailable ? '#059669' : '#DC2626'}; font-weight: 700;">
          ${allAvailable ? '✓ All prescribed medicines confirmed in stock for immediate dispense.' : '⚠️ Some items out of stock; shortage will be automatically aggregated into Village Health Demand Radar.'}
        </div>
      `;
    } catch (e) {
      previewEl.innerHTML = `<div style="font-size: 13px;">Stock verification failed</div>`;
    }
  }

  async submitPharmacyOrder() {
    if (!this.currentRxId) return;
    const pharmId = document.getElementById("pharm-select").value;
    const fulfillmentEl = document.querySelector('input[name="pharm-fulfillment"]:checked');
    const fulfillment = fulfillmentEl ? fulfillmentEl.value : "PICKUP";
    const address = document.getElementById("pharm-delivery-address").value;
    const refillReminder = document.getElementById("pharm-refill-checkbox").checked;

    const orderPayload = {
      rx_id: this.currentRxId,
      pharmacy_id: pharmId,
      fulfillment_type: fulfillment,
      delivery_address: address,
      patient_name: this.activePatient ? this.activePatient.name : "Citizen Patient",
      contact_number: this.activePatient ? this.activePatient.contact_number : "9842100000",
      village: this.activePatient ? this.activePatient.village : "Chettipalayam",
      refill_reminder: refillReminder
    };

    if (!navigator.onLine) {
      const offlineMed = {
        ...orderPayload,
        id: "MED-OFFLINE-" + Math.floor(1000 + Math.random() * 9000),
        offline_captured: true
      };
      if (window.offlineDB) await window.offlineDB.queueMedicineRequest(offlineMed);
      alert(`✓ Medicine request queued locally!\nTemporary ID: ${offlineMed.id}\nIt will automatically transmit to the pharmacy when connected.`);
      this.closePharmacyOrderModal();
      return;
    }

    try {
      const res = await fetch(`/api/prescriptions/${this.currentRxId}/order`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pharmacy_id: pharmId,
          fulfillment_type: fulfillment,
          delivery_address: address
        })
      });

      const data = await res.json();
      if (!data.success) {
        alert(data.error || "Failed to place medicine order");
        return;
      }

      if (refillReminder) {
        await fetch(`/api/prescriptions/${this.currentRxId}/refill-reminder`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reminder_days: 30 })
        });
      }

      if (data.has_stock_shortage) {
        alert(`Medicine order ${data.order.id} registered.\n⚠️ Note: Stock shortage identified. Logged to Village Health Demand Radar for supply replenishment.`);
      } else {
        alert(`✓ Medicine order ${data.order.id} confirmed!\nFulfillment: ${fulfillment}\nTotal: ₹${data.order.total_amount}`);
      }

      this.closePharmacyOrderModal();
      if (this.currentView === "track") {
        this.searchJourney();
      }
    } catch (e) {
      console.warn("Medicine order network failure, saving offline:", e);
      const offlineMed = {
        ...orderPayload,
        id: "MED-OFFLINE-" + Math.floor(1000 + Math.random() * 9000),
        offline_captured: true
      };
      if (window.offlineDB) await window.offlineDB.queueMedicineRequest(offlineMed);
      alert(`✓ Medicine request queued locally!\nTemporary ID: ${offlineMed.id}\nIt will automatically transmit to the pharmacy when connected.`);
      this.closePharmacyOrderModal();
    }
  }

  async verifyCurrentFacilityService() {
    try {
      const res = await fetch(`/api/facilities/${this.activeFacilityId}/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          verification_status: "VERIFIED",
          valid_hours: 48,
          verified_by: "Dr. K. Revathi (CMO)"
        })
      });
      const data = await res.json();
      if (data.success) {
        alert("Facility service schedule verified successfully for 48 hours. Readiness check updated.");
        await this.loadInitialMetadata();
        this.loadStaffRequests();
      } else {
        alert(data.error || "Verification failed");
      }
    } catch (e) {
      alert("Verification update failed");
    }
  }

  async staffAcceptAppointment(journeyId) {
    const slot = prompt("Confirm Outpatient Slot Time / Room (e.g., 10:30 AM Room 4):", "10:30 AM Outpatient");
    if (!slot) return;

    try {
      const res = await fetch(`/api/journeys/${journeyId}/appointment-action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "accept",
          staff_name: "Dr. K. Revathi (CMO)",
          appointment_slot: slot,
          notes: "Patient OPD slot locked."
        })
      });
      const data = await res.json();
      if (data.success) {
        alert(`Appointment confirmed for ${slot}. Departure readiness evaluated.`);
        this.loadStaffRequests();
      } else {
        alert(data.error || "Failed to confirm appointment");
      }
    } catch (e) {
      alert("Action failed");
    }
  }

  async staffRejectAppointment(journeyId) {
    const reason = prompt("Enter rejection reason (e.g., Doctor on emergency duty):", "Specialist unavailable on requested date.");
    if (!reason) return;

    try {
      const res = await fetch(`/api/journeys/${journeyId}/appointment-action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "reject",
          staff_name: "Dr. K. Revathi",
          notes: reason
        })
      });
      const data = await res.json();
      if (data.success) {
        alert("Appointment rejected and journey updated.");
        this.loadStaffRequests();
      } else {
        alert(data.error || "Failed to reject appointment");
      }
    } catch (e) {
      alert("Action failed");
    }
  }

  // =========================================================================
  // Driver Coordination Portal
  // =========================================================================
  async loadDriverRequests() {
    const container = document.getElementById("driver-requests-container");
    if (!container) return;
    container.innerHTML = `<div style="padding: 20px; text-align: center;">${window.i18n.t("loading")}</div>`;

    try {
      const res = await fetch("/api/transport/requests");
      const data = await res.json();
      const requests = (data.success && data.requests) ? data.requests : [];

      container.innerHTML = "";
      if (requests.length === 0) {
        container.innerHTML = `<div style="padding: 20px; color: var(--text-muted);">No pending transport requests in this area.</div>`;
        return;
      }

      requests.forEach(req => {
        const card = document.createElement("div");
        card.className = "card-panel";
        card.style.padding = "20px";
        card.style.marginBottom = "14px";

        const isAccepted = req.status === "ACCEPTED";
        card.innerHTML = `
          <div class="flex-between mb-16">
            <div>
              <span style="font-size: 13px; font-weight: 700; color: var(--text-muted);">${req.id}</span>
              <h4 style="font-size: 18px; font-weight: 700;">Ride for ${req.patient_name}</h4>
            </div>
            <div>
              <span class="status-pill ${isAccepted ? 'confirmed' : 'pending'}">${req.status}</span>
            </div>
          </div>

          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; font-size: 14px; margin-bottom: 16px;">
            <div><strong>Pickup Point:</strong> ${req.pickup_landmark || req.pickup_village}</div>
            <div><strong>Destination:</strong> ${req.destination_facility_name}</div>
            <div><strong>Patient Contact:</strong> <span style="color: var(--text-muted);">${req.patient_phone}</span></div>
            <div><strong>Disclosed Fare:</strong> ${req.fare_amount ? `₹${req.fare_amount} (${req.patient_fare_accepted ? 'Accepted by patient' : 'Awaiting patient acceptance'})` : 'Fare not yet quoted'}</div>
          </div>

          <div style="display: flex; gap: 10px; flex-wrap: wrap;">
            ${!isAccepted ? `
              <button class="btn btn-primary" onclick="app.driverAcceptRide('${req.id}')">
                Accept Ride & Quote Fare
              </button>
            ` : `
              <button class="btn btn-success" onclick="app.driverUpdateRideStatus('${req.id}', 'COMPLETED')">
                Complete Ride
              </button>
              <button class="btn btn-secondary" onclick="app.driverConfirmLocations('${req.id}')">
                Re-confirm Locations
              </button>
              <button class="btn btn-outline" style="color: var(--danger-red); border-color: var(--danger-red);" onclick="app.driverCancelRide('${req.id}')">
                Cancel Ride
              </button>
            `}
          </div>
        `;
        container.appendChild(card);
      });
    } catch (e) {
      container.innerHTML = `<div style="padding: 20px;">Could not load driver requests.</div>`;
    }
  }

  async driverAcceptRide(requestId) {
    const fare = prompt("Disclose upfront estimated fare in INR (e.g. 180):", "180");
    if (!fare || isNaN(fare)) return;

    try {
      const res = await fetch(`/api/transport/requests/${requestId}/accept`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          driver_id: this.activeDriverId,
          driver_name: "Murugan S.",
          driver_phone: "+91 98421 77101",
          vehicle_reg: "TN-38-AX-4412",
          fare_amount: parseFloat(fare)
        })
      });
      const data = await res.json();
      if (data.success) {
        alert(`Ride accepted. Fare of ₹${fare} disclosed.`);
        this.loadDriverRequests();
      } else {
        alert(data.error || "Driver acceptance failed");
      }
    } catch (e) {
      alert("Acceptance failed");
    }
  }

  async driverConfirmLocations(requestId) {
    try {
      await fetch(`/api/transport/requests/${requestId}/confirm-locations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pickup_confirmed: true, destination_confirmed: true })
      });
      alert("Pickup & Destination confirmed.");
      this.loadDriverRequests();
    } catch (e) {
      alert("Location confirmation failed");
    }
  }

  async driverUpdateRideStatus(requestId, status) {
    try {
      const res = await fetch(`/api/transport/requests/${requestId}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: status })
      });
      const data = await res.json();
      if (data.success) {
        alert(`Status updated to ${status}.`);
        this.loadDriverRequests();
      } else {
        alert(data.error || "Status update failed");
      }
    } catch (e) {
      alert("Status update failed");
    }
  }

  async driverCancelRide(requestId) {
    const reason = prompt("Enter cancellation reason (mandatory for driver accountability):", "Emergency vehicle mechanical issue");
    if (!reason) return;

    try {
      const res = await fetch(`/api/transport/requests/${requestId}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: reason, driver_name: "Murugan S." })
      });
      const data = await res.json();
      if (data.success) {
        alert("Ride cancelled. Departure readiness re-evaluated.");
        this.loadDriverRequests();
      } else {
        alert(data.error || "Cancellation failed");
      }
    } catch (e) {
      alert("Cancel failed");
    }
  }

  // =========================================================================
  // Administration Dashboard
  // =========================================================================
  async loadAdminOverview() {
    try {
      const res = await fetch("/api/admin/overview");
      const data = await res.json();
      if (!data.success) {
        if (data.code === "UNAUTHENTICATED" || data.code === "FORBIDDEN") {
          console.warn("Admin portal requires admin login session.");
        }
        return;
      }

      const m = data.metrics || {};
      document.getElementById("admin-metric-total").textContent = m.total_journeys || 0;
      document.getElementById("admin-metric-confirmed").textContent = m.confirmed_ready || 0;
      document.getElementById("admin-metric-pending").textContent = m.pending_coordination || 0;
      document.getElementById("admin-metric-cancelled").textContent = (m.recheck_required || 0) + (m.cancelled_or_failed || 0);
      document.getElementById("admin-metric-facilities").textContent = `${m.verified_facilities || 0} / ${m.total_facilities || 0}`;

      const fTbody = document.getElementById("admin-facilities-tbody");
      if (fTbody) {
        fTbody.innerHTML = "";
        this.facilities.forEach(fac => {
          const tr = document.createElement("tr");
          const isVerified = fac.verification_status === "VERIFIED";
          tr.innerHTML = `
            <td><strong>${fac.id}</strong> - ${fac.name}</td>
            <td>${fac.district}</td>
            <td><span class="status-pill ${isVerified ? 'verified' : (fac.verification_status === 'EXPIRED' ? 'expired' : 'unverified')}">${fac.verification_status}</span></td>
            <td>${fac.last_verified_at ? new Date(fac.last_verified_at).toLocaleDateString() : 'Never'}</td>
            <td>${fac.verification_valid_until ? new Date(fac.verification_valid_until).toLocaleDateString() : 'N/A'}</td>
            <td>
              <button class="btn btn-outline" style="padding: 4px 8px; font-size: 12px; min-height: 28px;" onclick="app.adminToggleFacilityVerification('${fac.id}')">
                ${isVerified ? 'Set Expired' : 'Set Verified'}
              </button>
            </td>
          `;
          fTbody.appendChild(tr);
        });
      }

      const evRes = await fetch("/api/admin/events");
      const evData = await evRes.json();
      const eTbody = document.getElementById("admin-events-tbody");
      if (eTbody && evData.success) {
        eTbody.innerHTML = "";
        evData.events.forEach(ev => {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td>${new Date(ev.timestamp).toLocaleTimeString()}</td>
            <td><strong>${ev.journey_id || '-'}</strong></td>
            <td><span class="status-pill verified" style="font-size: 11px;">${ev.event_type}</span></td>
            <td>${ev.actor}</td>
            <td>${ev.description}</td>
          `;
          eTbody.appendChild(tr);
        });
      }

      // Also refresh demand radar if active
      this.loadDemandRadar();
    } catch (e) {
      console.warn("Failed to load admin overview:", e);
    }
  }

  switchAdminSubTab(subTabName) {
    const tabs = ["radar", "facilities", "events"];
    tabs.forEach(t => {
      const btn = document.getElementById(`admin-tab-${t}-btn`);
      const subview = document.getElementById(`admin-subview-${t}`);
      if (btn && subview) {
        if (t === subTabName) {
          btn.className = "btn btn-primary";
          subview.classList.remove("hidden");
        } else {
          btn.className = "btn btn-secondary";
          subview.classList.add("hidden");
        }
      }
    });

    if (subTabName === "radar") {
      this.loadDemandRadar();
    } else if (subTabName === "facilities" || subTabName === "events") {
      this.loadAdminOverview();
    }
  }

  showAdminRadarTab() {
    if (this.activeRole === "citizen") {
      alert("District Health Officer or Coordinator access is required to review Village Demand Signals. Please switch to the District Health Officer role.");
      this.openRoleModal();
      return;
    }
    this.showView("admin-portal");
    this.switchAdminSubTab("radar");
  }

  async loadDemandRadar() {
    const periodSelect = document.getElementById("radar-period-select");
    const period = periodSelect ? periodSelect.value : "30";

    try {
      const res = await fetch(`/api/radar/demand-clusters?period_days=${period}`);
      const data = await res.json();
      if (data.success) {
        this.renderDemandRadar(data);
      }
      // Also load existing outreach proposals
      this.loadOutreachProposals();
    } catch (e) {
      console.warn("Error loading demand radar:", e);
    }
  }

  renderDemandRadar(data) {
    const s = data.summary || {};
    const setT = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val !== undefined ? val : 0;
    };

    setT("radar-metric-total", s.total_distinct_requests);
    setT("radar-metric-unmet", s.total_unmet_demand);
    setT("radar-metric-facility-failures", s.total_facility_failures);
    setT("radar-metric-transport-failures", s.total_transport_failures);
    setT("radar-metric-old-unresolved", s.total_old_unresolved);
    setT("radar-metric-voluntary-excluded", s.voluntary_cancellations_excluded);

    // Top Services List
    const sList = document.getElementById("radar-top-services-list");
    if (sList) {
      sList.innerHTML = "";
      const maxCount = Math.max(...(data.top_services || []).map(x => x.request_count), 1);
      (data.top_services || []).forEach(svc => {
        const pct = Math.round((svc.request_count / maxCount) * 100);
        const item = document.createElement("div");
        item.innerHTML = `
          <div style="display: flex; justify-content: space-between; font-size: 13px; font-weight: 600; margin-bottom: 4px;">
            <span>${svc.service_name}</span>
            <span style="color: var(--primary-teal-dark);">${svc.request_count} requests</span>
          </div>
          <div style="background: var(--bg-subtle); height: 8px; border-radius: 4px; overflow: hidden;">
            <div style="background: var(--primary-teal); width: ${pct}%; height: 100%;"></div>
          </div>
        `;
        sList.appendChild(item);
      });
      if ((data.top_services || []).length === 0) {
        sList.innerHTML = `<div style="font-size: 13px; color: var(--text-muted);">No services recorded yet.</div>`;
      }
    }

    // Village Demand List
    const vList = document.getElementById("radar-village-demand-list");
    if (vList) {
      vList.innerHTML = "";
      (data.village_demand || []).forEach(vd => {
        const item = document.createElement("div");
        item.style.display = "flex";
        item.style.justifyContent = "space-between";
        item.style.alignItems = "center";
        item.style.padding = "8px 0";
        item.style.borderBottom = "1px solid var(--border-light)";
        item.innerHTML = `
          <div>
            <strong style="font-size: 14px;">${vd.village}</strong>
            <div style="font-size: 12px; color: var(--text-muted);">${vd.total_requests} total requests</div>
          </div>
          <div>
            <span class="status-pill ${vd.unmet_demand > 0 ? 'priority-high' : 'priority-low'}">
              ${vd.unmet_demand} unmet
            </span>
          </div>
        `;
        vList.appendChild(item);
      });
      if ((data.village_demand || []).length === 0) {
        vList.innerHTML = `<div style="font-size: 13px; color: var(--text-muted);">No village demand recorded yet.</div>`;
      }
    }

    // Clusters Table
    const cTbody = document.getElementById("radar-clusters-tbody");
    if (cTbody) {
      cTbody.innerHTML = "";
      (data.clusters || []).forEach(c => {
        const tr = document.createElement("tr");
        const priorityClass = c.priority === "HIGH" ? "priority-high" : (c.priority === "MEDIUM" ? "priority-medium" : "priority-low");
        tr.innerHTML = `
          <td><strong>${c.village}</strong></td>
          <td>${c.service_name}</td>
          <td>${c.total_distinct_requests}</td>
          <td><strong style="color: ${c.unmet_demand_count > 0 ? 'var(--danger-red)' : 'var(--text-dark)'}">${c.unmet_demand_count}</strong></td>
          <td>${c.facility_failures}</td>
          <td>${c.transport_failures}</td>
          <td><span style="font-size: 12px; font-weight: 600; color: var(--text-muted);">${c.primary_bottleneck}</span></td>
          <td><span class="status-pill ${priorityClass}">${c.priority}</span></td>
        `;
        cTbody.appendChild(tr);
      });
      if ((data.clusters || []).length === 0) {
        cTbody.innerHTML = `<tr><td colspan="8" class="text-center" style="padding: 20px;">No demand clusters found for selected period.</td></tr>`;
      }
    }
  }

  async loadOutreachProposals() {
    const container = document.getElementById("radar-proposals-container");
    if (!container) return;

    try {
      const res = await fetch("/api/radar/outreach-proposals");
      const data = await res.json();
      const proposals = data.success ? data.proposals : [];
      this.renderOutreachProposals(proposals);
    } catch (e) {
      console.warn("Error loading proposals:", e);
    }
  }

  renderOutreachProposals(proposals) {
    const container = document.getElementById("radar-proposals-container");
    if (!container) return;
    container.innerHTML = "";

    if (proposals.length === 0) {
      container.innerHTML = `
        <div style="grid-column: 1 / -1; padding: 24px; text-align: center; background: #FFFFFF; border-radius: var(--radius-md); border: 1px dashed var(--border-light);">
          <div style="font-size: 15px; font-weight: 600; color: var(--text-dark); margin-bottom: 6px;">No Active Outreach Proposals Yet</div>
          <div style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">Click 'Generate Proposals from Radar' above to produce data-driven outreach proposals from unmet village demand.</div>
        </div>
      `;
      return;
    }

    proposals.forEach(p => {
      const card = document.createElement("div");
      card.className = "card-panel";
      card.style.background = "#FFFFFF";
      card.style.border = "1px solid var(--border-light)";
      card.style.borderRadius = "var(--radius-md)";
      card.style.padding = "18px";
      card.style.display = "flex";
      card.style.flexDirection = "column";
      card.style.justifyContent = "space-between";

      const statusClass = p.status === "APPROVED" ? "approved" : (p.status === "PROPOSED" ? "proposed" : "rejected");

      card.innerHTML = `
        <div>
          <div class="flex-between mb-8">
            <span style="font-size: 12px; font-weight: 700; color: var(--text-muted);">${p.proposal_id}</span>
            <span class="status-pill ${statusClass}">${p.status}</span>
          </div>
          <h4 style="font-size: 16px; font-weight: 700; color: var(--primary-teal-dark); margin-bottom: 6px;">${p.title}</h4>
          <div style="font-size: 13px; font-weight: 600; color: var(--text-dark); margin-bottom: 8px;">
            Target: <span>${p.village}</span> • <span>${p.requested_service_name}</span>
          </div>
          <p style="font-size: 13px; color: var(--text-dark); background: var(--bg-subtle); padding: 10px; border-radius: var(--radius-sm); margin-bottom: 10px;">
            <strong>Action:</strong> ${p.suggested_action}
          </p>
          <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 12px;">
            <strong>Evidence:</strong> ${p.evidence_summary}
          </div>
          ${p.reviewed_by ? `
            <div style="font-size: 11px; color: var(--healthcare-green-dark); background: #F0FDF4; padding: 6px 10px; border-radius: 4px; margin-bottom: 12px;">
              ✓ Reviewed by: ${p.reviewed_by} on ${new Date(p.reviewed_at).toLocaleDateString()} (${p.review_notes || 'Endorsed'})
            </div>
          ` : ''}
        </div>

        <div style="display: flex; gap: 8px; margin-top: 10px; border-top: 1px solid var(--border-light); padding-top: 12px;">
          ${p.status === "PROPOSED" ? `
            <button class="btn btn-success" style="flex: 1; padding: 6px 12px; min-height: 36px; font-size: 13px;" onclick="app.approveOutreachProposal('${p.proposal_id}')">
              Approve (Admin)
            </button>
            <button class="btn btn-secondary" style="padding: 6px 12px; min-height: 36px; font-size: 13px; color: var(--danger-red);" onclick="app.rejectOutreachProposal('${p.proposal_id}')">
              Reject
            </button>
          ` : `
            <span style="font-size: 12px; color: var(--text-muted);">Proposal recorded as ${p.status}</span>
          `}
        </div>
      `;
      container.appendChild(card);
    });
  }

  async generateOutreachProposals() {
    try {
      const res = await fetch("/api/radar/outreach-proposals/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ unmet_threshold: 1, period_days: "30" })
      });
      const data = await res.json();
      if (data.success) {
        alert(`${data.message}`);
        this.loadOutreachProposals();
      } else {
        alert(data.error || "Failed to generate proposals");
      }
    } catch (e) {
      alert("Error generating outreach proposals");
    }
  }

  async approveOutreachProposal(propId) {
    const notes = prompt("Enter administrative approval notes (e.g., Scheduled for District Outreach Camp Van):", "Approved for district mobile healthcare outreach coordination.");
    if (notes === null) return;

    try {
      const res = await fetch(`/api/radar/outreach-proposals/${propId}/approve`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-User-Username": "admin@ruralcare.gov.in"
        },
        body: JSON.stringify({ notes: notes })
      });
      const data = await res.json();
      if (data.success) {
        alert(`${data.message}\n\nNotice: ${data.operational_notice}`);
        this.loadOutreachProposals();
      } else {
        alert(data.error || "Failed to approve proposal");
      }
    } catch (e) {
      alert("Error approving outreach proposal");
    }
  }

  async rejectOutreachProposal(propId) {
    const reason = prompt("Enter rejection reason for audit records:", "Adequate alternate primary health centre coverage in area.");
    if (!reason) return;

    try {
      const res = await fetch(`/api/radar/outreach-proposals/${propId}/reject`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-User-Username": "admin@ruralcare.gov.in"
        },
        body: JSON.stringify({ reason: reason })
      });
      const data = await res.json();
      if (data.success) {
        alert(`${data.message}`);
        this.loadOutreachProposals();
      } else {
        alert(data.error || "Failed to reject proposal");
      }
    } catch (e) {
      alert("Error rejecting outreach proposal");
    }
  }

  async adminToggleFacilityVerification(facId) {
    const fac = this.facilities.find(f => f.id === facId);
    if (!fac) return;
    const newStatus = fac.verification_status === "VERIFIED" ? "EXPIRED" : "VERIFIED";

    try {
      const res = await fetch(`/api/facilities/${facId}/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          verification_status: newStatus,
          valid_hours: newStatus === "VERIFIED" ? 48 : 0,
          verified_by: "District Health Administration"
        })
      });
      const data = await res.json();
      if (data.success) {
        await this.loadInitialMetadata();
        this.loadAdminOverview();
      } else {
        alert(data.error || "Admin toggle failed");
      }
    } catch (e) {
      alert("Toggle failed");
    }
  }
}

document.addEventListener("DOMContentLoaded", () => {
  window.app = new RuralCareApp();
});
