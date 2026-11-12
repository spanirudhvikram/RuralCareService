/**
 * RuralCare Internationalization (i18n) Engine
 */

class I18nManager {
  constructor() {
    this.supportedLanguages = ["ta", "en", "te", "ml", "kn", "hi"];
    this.currentLang = localStorage.getItem("ruralcare_lang") || null;
  }

  getCurrentLanguage() {
    return this.currentLang;
  }

  hasSelectedLanguage() {
    return !!this.currentLang && this.supportedLanguages.includes(this.currentLang);
  }

  setLanguage(langCode) {
    if (!this.supportedLanguages.includes(langCode)) {
      langCode = "ta"; // Default to Tamil
    }
    this.currentLang = langCode;
    localStorage.setItem("ruralcare_lang", langCode);
    document.documentElement.lang = langCode;

    this.applyTranslations();
    window.dispatchEvent(new CustomEvent("ruralcare:languageChanged", { detail: { lang: langCode } }));
  }

  t(key, fallback = "") {
    const langDict = TRANSLATIONS[this.currentLang] || TRANSLATIONS["en"];
    if (langDict && langDict[key] !== undefined) {
      return langDict[key];
    }
    // Fallback to English if missing
    if (TRANSLATIONS["en"] && TRANSLATIONS["en"][key] !== undefined) {
      return TRANSLATIONS["en"][key];
    }
    return fallback || key;
  }

  applyTranslations() {
    const elements = document.querySelectorAll("[data-i18n]");
    elements.forEach((el) => {
      const key = el.getAttribute("data-i18n");
      const translation = this.t(key);
      if (translation) {
        if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
          el.value = translation;
        } else {
          el.textContent = translation;
        }
      }
    });

    const placeholders = document.querySelectorAll("[data-i18n-placeholder]");
    placeholders.forEach((el) => {
      const key = el.getAttribute("data-i18n-placeholder");
      const translation = this.t(key);
      if (translation) {
        el.setAttribute("placeholder", translation);
      }
    });

    // Update active state in language pickers
    const langSelectors = document.querySelectorAll(".lang-pill-btn");
    langSelectors.forEach((btn) => {
      const code = btn.getAttribute("data-lang-code");
      if (code === this.currentLang) {
        btn.classList.add("active-lang");
      } else {
        btn.classList.remove("active-lang");
      }
    });
  }
}

window.i18n = new I18nManager();
