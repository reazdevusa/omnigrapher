export type Citation = {
  page: number;
  chunk_id: string;
  source: string;
  document_id?: string;
  file_name?: string;
  page_number?: number;
  file_url?: string;
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  mode?: "document" | "assistant";
  model?: string;
  feedback_rating?: "up" | "down" | null;
  citations?: Citation[];
};

export type ChatSession = {
  id: string;
  title: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
};

const STORAGE_KEY = "kb_sessions";

export function loadSessions(): ChatSession[] {
  if (typeof window === "undefined") return [];
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return [];
  try {
    return JSON.parse(raw);
  } catch {
    return [];
  }
}

export function saveSessions(sessions: ChatSession[]) {
  if (typeof window === "undefined") return;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
}

export function newSessionId() {
  return `sess_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

export function createSession(title = "New Chat"): ChatSession {
  const now = Date.now();
  return {
    id: newSessionId(),
    title,
    messages: [],
    createdAt: now,
    updatedAt: now,
  };
}

export function autoTitle(query: string): string {
  const trimmed = query.trim();
  if (!trimmed) return "New Chat";
  return trimmed.length > 40 ? trimmed.slice(0, 40) + "…" : trimmed;
}
