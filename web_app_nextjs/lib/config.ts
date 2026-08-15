// Centralized frontend app settings and persistence helpers.

export const APP_CONFIG = {
  defaultModel: "ollama-llama3.2",
  storage: {
    selectedModel: "selected_llm_model",
    chatMode: "chat_mode",
    ragScope: "rag_scope",
  },
  rag: {
    defaultTopK: 5,
    ollamaTopK: 3,
    maxChunkChars: 1200,
  },
} as const;

export function loadSetting(key: string, defaultValue: string | null = null): string | null {
  if (typeof window === "undefined") return defaultValue;
  try {
    const saved = window.localStorage.getItem(key);
    return saved ?? defaultValue;
  } catch {
    return defaultValue;
  }
}

export function saveSetting(key: string, value: string | null) {
  if (typeof window === "undefined") return;
  try {
    if (value == null) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, value);
    }
  } catch {
    // Ignore storage errors (e.g. private mode)
  }
}
