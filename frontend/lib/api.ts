const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface DocumentListItem {
  id: string;
  filename: string;
  chunk_count: number;
}

export interface ModelInfo {
  name: string;
  size?: string;
  modified_at?: string;
}

export interface ChatResponse {
  session_id: string;
  answer: string;
  sources: string[];
}

export async function uploadDocument(file: File): Promise<DocumentListItem & { message: string }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_URL}/api/documents/upload`, { method: "POST", body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Upload failed");
  }
  return res.json();
}

export async function listDocuments(): Promise<DocumentListItem[]> {
  const res = await fetch(`${API_URL}/api/documents/`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to list documents");
  return res.json();
}

export async function listModels(): Promise<ModelInfo[]> {
  const res = await fetch(`${API_URL}/api/models/`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to list models");
  return res.json();
}

export async function sendChat(
  session_id: string,
  message: string,
  model?: string
): Promise<ChatResponse> {
  const res = await fetch(`${API_URL}/api/chat/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id, message, model }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Chat failed");
  }
  return res.json();
}
