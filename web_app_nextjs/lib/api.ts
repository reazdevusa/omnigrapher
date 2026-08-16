const BACKEND_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "";
const BACKEND_DISPLAY = BACKEND_URL || "the backend";
const MAX_RETRIES = 3;
const REQUEST_TIMEOUT_MS = 10000;

async function fetchWithTimeout(url: string, options: RequestInit = {}, timeoutMs = REQUEST_TIMEOUT_MS): Promise<Response> {
  if (timeoutMs <= 0) {
    return fetch(url, options);
  }
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...options, credentials: "include", signal: controller.signal });
    clearTimeout(timeoutId);
    return res;
  } catch (e) {
    clearTimeout(timeoutId);
    if ((e as any)?.name === "AbortError") {
      const abortErr = new Error("Request timed out");
      abortErr.name = "AbortError";
      throw abortErr;
    }
    throw new Error(`Request to ${url} timed out or failed. ${e instanceof Error ? e.message : ""}`);
  }
}

function getHeaders() {
  return {
    "Content-Type": "application/json",
  };
}

async function fetchJson(path: string, options: RequestInit = {}, _token?: string | null) {
  let res: Response | undefined;

  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      res = await fetch(`${BACKEND_URL}${path}`, {
        ...options,
        credentials: "include",
        signal: controller.signal,
        headers: {
          ...getHeaders(),
          ...(options.headers || {}),
        },
      });
      clearTimeout(timeoutId);

      if (res.ok) break;

      // Retry on transient 5xx server errors, otherwise surface the HTTP failure immediately.
      if (res.status >= 500 && attempt < MAX_RETRIES) {
        await new Promise((resolve) => setTimeout(resolve, 500 * attempt));
        continue;
      }

      break;
    } catch (e) {
      clearTimeout(timeoutId);
      res = undefined;
      if (attempt === MAX_RETRIES) break;
      await new Promise((resolve) => setTimeout(resolve, 500 * attempt));
    }
  }

  if (!res) {
    throw new Error(`Backend is unreachable at ${BACKEND_DISPLAY} after ${MAX_RETRIES} attempts. Please start the API server.`);
  }

  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed.detail)) {
        detail = parsed.detail
          .map((err: any) => (err.msg ? `${err.loc?.slice(1)?.join('.') || 'field'}: ${err.msg}` : String(err)))
          .join("; ");
      } else if (parsed.detail && typeof parsed.detail === "object") {
        detail = JSON.stringify(parsed.detail);
      } else if (parsed.detail) {
        detail = String(parsed.detail);
      }
    } catch {}
    throw new Error(`HTTP ${res.status}: ${detail}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export type User = {
  username: string;
  role: "user" | "admin";
  email?: string;
  phone?: string;
  display_name?: string;
};

export type LoginResult = {
  access_token: string;
  refresh_token: string;
  username: string;
  role: string;
  email?: string;
  phone?: string;
  display_name?: string;
};

export async function login(username: string, password: string): Promise<LoginResult> {
  return fetchJson("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export type RegisterPayload = {
  username: string;
  email: string;
  phone: string;
  display_name?: string;
  password: string;
  confirm_password: string;
};

export async function register(payload: RegisterPayload): Promise<{ success: boolean; username: string; role: string; email?: string; phone?: string; display_name?: string }> {
  return fetchJson("/auth/register", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function checkUsernameAvailable(
  username: string
): Promise<{ available: boolean; reason: string }> {
  return fetchJson(`/auth/username-available?username=${encodeURIComponent(username)}`);
}

export async function checkEmailAvailable(
  email: string
): Promise<{ available: boolean; reason: string }> {
  return fetchJson(`/auth/email-available?email=${encodeURIComponent(email)}`);
}

export async function refreshToken(refreshToken?: string | null): Promise<LoginResult> {
  const options: RequestInit = {
    method: "POST",
    credentials: "include",
  };
  if (refreshToken) {
    options.body = JSON.stringify({ refresh_token: refreshToken });
  }
  return fetchJson("/auth/refresh", options);
}

export async function getMe(token?: string | null): Promise<User> {
  return fetchJson("/auth/me", {}, token);
}

export async function getProfile(token?: string | null) {
  return fetchJson("/auth/profile", {}, token);
}

export async function updateProfile(token: string, payload: Record<string, unknown>) {
  return fetchJson("/auth/profile", {
    method: "PUT",
    body: JSON.stringify(payload),
  }, token);
}

export type DocumentItem = {
  id: number;
  filename: string;
  status: "pending" | "processing" | "indexed" | "completed" | "failed" | string;
  chunks: number;
  error?: string;
  error_code?: string;
  attempt_count: number;
  processing_started_at?: string;
  processing_completed_at?: string;
  allowed_actions?: string[];
};

export async function listDocuments(token: string): Promise<{ documents: DocumentItem[]; count: number }> {
  return fetchJson("/api/documents", {}, token);
}

export async function uploadDocuments(token: string, files: FileList): Promise<{ uploaded: string[]; skipped: string[]; job_id?: string; message: string }> {
  const formData = new FormData();
  Array.from(files).forEach((file) => formData.append("files", file));
  let res: Response;
  try {
    res = await fetchWithTimeout(`${BACKEND_URL}/api/upload`, {
      method: "POST",
      credentials: "include",
      body: formData,
    }, REQUEST_TIMEOUT_MS * 3);
  } catch (e) {
    throw new Error(`Backend is unreachable at ${BACKEND_DISPLAY}. Please start the API server.`);
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "Upload failed");
    throw new Error(text);
  }
  return res.json();
}

export async function deleteDocument(token: string, filename: string) {
  return fetchJson(`/api/documents/${encodeURIComponent(filename)}`, {
    method: "DELETE",
  }, token);
}

export async function retryDocument(token: string, filename: string) {
  return fetchJson(`/api/documents/${encodeURIComponent(filename)}/retry`, {
    method: "POST",
  }, token);
}

export async function reindexDocument(token: string, documentId: number) {
  return fetchJson(`/api/documents/${documentId}/reindex`, {
    method: "POST",
  }, token);
}

export async function renameDocument(token: string, oldName: string, newName: string) {
  return fetchJson(`/api/documents/${encodeURIComponent(oldName)}/rename`, {
    method: "PUT",
    body: JSON.stringify({ new_name: newName }),
  }, token);
}

export async function getDocumentContent(token: string, filename: string) {
  return fetchJson(`/api/documents/${encodeURIComponent(filename)}/content`, {}, token);
}

export async function getDocumentChunks(token: string, filename: string) {
  return fetchJson(`/api/documents/${encodeURIComponent(filename)}/chunks`, {}, token);
}

export function getDocumentRawUrl(_token: string, filename: string): string {
  return `${BACKEND_URL}/api/documents/${encodeURIComponent(filename)}/raw`;
}

export async function getDocumentRaw(_token: string, filename: string): Promise<Response> {
  const res = await fetch(`${BACKEND_URL}/api/documents/${encodeURIComponent(filename)}/raw`, {
    credentials: "include",
    headers: getHeaders(),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return res;
}

export type ChatMessage = { role: "user" | "assistant"; content: string };

export type ChatHistoryMessage = {
  role: "user" | "assistant";
  content: string;
  mode?: "document" | "assistant";
  citations?: { page: number; chunk_id: string; source: string }[];
};

export type ChatHistorySession = {
  id: string;
  title?: string;
  document?: string;
  model?: string;
  mode?: string;
  messages: ChatHistoryMessage[];
  created_at?: string;
  updated_at?: string;
};

export async function getChatHistory(token: string, sessionId: string): Promise<ChatHistorySession> {
  return fetchJson(`/api/chat/history/${encodeURIComponent(sessionId)}`, {}, token);
}

export async function saveChatHistory(
  token: string,
  sessionId: string,
  payload: Omit<ChatHistorySession, "id" | "created_at" | "updated_at">
): Promise<{ id: string; status: string }> {
  return fetchJson(`/api/chat/history/${encodeURIComponent(sessionId)}`, {
    method: "POST",
    body: JSON.stringify(payload),
  }, token);
}

export type StreamEvent =
  | { type: "token"; token: string }
  | { type: "citation"; page: number; chunk_id: string; source: string; document_id?: string; file_name?: string; page_number?: number; file_url?: string }
  | { type: "fallback"; reason: string; message: string; model: string }
  | { type: "error"; error: string }
  | { type: "done" };

export async function* streamQuery(
  token: string,
  query: string,
  mode: "document" | "assistant" = "document",
  history: ChatMessage[] = [],
  source?: string,
  model: string = "default",
  scope: "single" | "knowledge_base" = "single"
): AsyncGenerator<StreamEvent, void, unknown> {
  let res: Response;
  const backendMode = mode === "assistant" ? "ask_ai_freely" : mode;
  const body: any = { query, mode: backendMode, history, model, scope };
  if (source) body.source = source;
  try {
    res = await fetchWithTimeout(`${BACKEND_URL}/api/query/stream`, {
      method: "POST",
      credentials: "include",
      headers: getHeaders(),
      body: JSON.stringify(body),
    }, 0);
  } catch (e) {
    throw new Error(`Backend is unreachable at ${BACKEND_DISPLAY}. Please start the API server.`);
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "Stream failed");
    throw new Error(text);
  }
  if (!res.body) throw new Error("No response body");

  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("text/event-stream")) {
    throw new Error(`Unexpected stream content type: ${contentType || "missing"}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let receivedToken = false;
  let streamFinished = false;

  try {
    while (!streamFinished) {
      const { done, value } = await reader.read();
      pending += value ? decoder.decode(value, { stream: !done }) : decoder.decode();
      pending = pending.replace(/\r\n/g, "\n");

      const frames = pending.split("\n\n");
      pending = frames.pop() || "";
      if (done && pending.trim()) {
        frames.push(pending);
        pending = "";
      }

      for (const frame of frames) {
        const dataStr = frame
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n")
          .trim();
        if (!dataStr) continue;
        if (dataStr === "[DONE]") {
          streamFinished = true;
          break;
        }

        let payload: { type?: string; token?: string; error?: string; [key: string]: any };
        try {
          payload = JSON.parse(dataStr);
        } catch {
          throw new Error("Backend returned a malformed streaming event");
        }
        if (payload.type === "error" || payload.error) {
          throw new Error(payload.error || "The backend stream failed");
        }
        if (payload.type === "done") {
          streamFinished = true;
          break;
        }
        if (typeof payload.token === "string" && payload.token.length > 0) {
          receivedToken = true;
          yield payload as StreamEvent;
        } else if (payload.type === "citation" || payload.type === "fallback") {
          if (payload.type === "fallback") receivedToken = true;
          yield payload as StreamEvent;
        }
      }

      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }

  if (!receivedToken) {
    throw new Error("The AI completed the stream without returning any text");
  }
}

export async function submitFeedback(token: string, payload: {
  query: string;
  response: string;
  mode: string;
  rating: "up" | "down";
  comment?: string;
  session_id?: string;
  message_id?: string;
}) {
  return fetchJson("/api/feedback", {
    method: "POST",
    body: JSON.stringify(payload),
  }, token);
}

export async function submitSyncJob(token: string) {
  return fetchJson("/api/jobs/sync-index", { method: "POST" }, token);
}

export async function submitRebuildJob(token: string) {
  return fetchJson("/api/jobs/rebuild-index", { method: "POST" }, token);
}

export async function listJobs(token: string) {
  return fetchJson("/api/jobs", {}, token);
}

export async function getJobStatus(token: string, jobId: string) {
  return fetchJson(`/api/jobs/${jobId}`, {}, token);
}

export async function adminListUsers(token: string) {
  return fetchJson("/api/admin/users", {}, token);
}

export async function adminDeleteUser(token: string, username: string) {
  return fetchJson(`/api/admin/users/${encodeURIComponent(username)}`, { method: "DELETE" }, token);
}

export async function adminSetRole(token: string, username: string, role: string) {
  return fetchJson(`/api/admin/users/${encodeURIComponent(username)}/role`, {
    method: "PUT",
    body: JSON.stringify({ role }),
  }, token);
}

export async function adminHealthStatus(token: string) {
  return fetchJson("/api/admin/health", {}, token);
}

export async function adminWidgetConfig(token: string) {
  return fetchJson("/api/admin/widget-config", {}, token);
}

export async function adminListFeedback(token: string) {
  return fetchJson("/api/admin/feedback", {}, token);
}

export type ModelInfo = {
  id: string;
  provider: string;
  tier: string;
  cost_input_1k: number;
  cost_output_1k: number;
  default: boolean;
  capabilities: string[];
  allowed: boolean;
  downloaded?: boolean;
};

export async function getModels(token: string): Promise<ModelInfo[]> {
  return fetchJson("/api/ai/models", {}, token);
}

export type ChatResponse = {
  text: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  price_usd: number;
  remaining_credits: number;
};

export async function* chat(
  token: string,
  model: string,
  messages: ChatMessage[],
  mode: "document" | "assistant" = "assistant"
): AsyncGenerator<StreamEvent, void, unknown> {
  const backendMode = mode === "assistant" ? "ask_ai_freely" : mode;

  // Enforce a 60-second time-to-first-token (TTFT) timeout. The timer is only
  // cleared once the first actual chunk of the streaming body is received.
  const controller = new AbortController();
  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  let ttftReject: (err: Error) => void = () => {};
  const ttftPromise = new Promise<never>((_, reject) => {
    ttftReject = reject;
  });
  const timeoutId = setTimeout(() => {
    const err = new Error("TTFT_TIMEOUT");
    err.name = "AbortError";
    reader?.cancel().catch(() => {});
    ttftReject(err);
    controller.abort("TTFT_TIMEOUT");
  }, 60000);

  let res: Response;
  try {
    res = await fetch(`${BACKEND_URL}/api/ai/chat`, {
      method: "POST",
      credentials: "include",
      headers: getHeaders(),
      body: JSON.stringify({
        model,
        messages,
        temperature: 0.7,
        max_tokens: 1024,
        mode: backendMode,
        stream: true,
      }),
      signal: controller.signal,
    });
  } catch (e) {
    clearTimeout(timeoutId);
    throw e;
  }

  if (!res.ok) {
    clearTimeout(timeoutId);
    const text = await res.text().catch(() => "Chat failed");
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      if (parsed.detail) detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
    } catch {}
    throw new Error(`HTTP ${res.status}: ${detail}`);
  }
  if (!res.body) {
    clearTimeout(timeoutId);
    throw new Error("No response body");
  }

  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("text/event-stream")) {
    clearTimeout(timeoutId);
    throw new Error(`Unexpected stream content type: ${contentType || "missing"}`);
  }

  reader = res.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let receivedToken = false;
  let firstChunkRead = false;
  let streamFinished = false;

  try {
    while (!streamFinished) {
      // The first read has a strict 60-second deadline; subsequent chunks stream normally.
      const { done, value } = firstChunkRead
        ? await reader.read()
        : (await (Promise.race([reader.read(), ttftPromise]) as Promise<
            { done: boolean; value?: Uint8Array }
          >));
      if (!firstChunkRead) {
        firstChunkRead = true;
        clearTimeout(timeoutId);
      }
      pending += value ? decoder.decode(value, { stream: !done }) : decoder.decode();
      pending = pending.replace(/\r\n/g, "\n");

      const frames = pending.split("\n\n");
      pending = frames.pop() || "";
      if (done && pending.trim()) {
        frames.push(pending);
        pending = "";
      }

      for (const frame of frames) {
        const dataStr = frame
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n")
          .trim();
        if (!dataStr) continue;
        if (dataStr === "[DONE]") {
          streamFinished = true;
          break;
        }

        let payload: { type?: string; token?: string; error?: string; [key: string]: any };
        try {
          payload = JSON.parse(dataStr);
        } catch {
          throw new Error("Backend returned a malformed streaming event");
        }
        if (payload.type === "error" || payload.error) {
          throw new Error(payload.error || "The backend stream failed");
        }
        if (payload.type === "done") {
          streamFinished = true;
          break;
        }
        if (typeof payload.token === "string" && payload.token.length > 0) {
          receivedToken = true;
          yield payload as StreamEvent;
        } else if (payload.type === "citation" || payload.type === "fallback") {
          if (payload.type === "fallback") receivedToken = true;
          yield payload as StreamEvent;
        }
      }

      if (done) break;
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
    clearTimeout(timeoutId);
  }

  if (!receivedToken) {
    throw new Error("The AI completed the stream without returning any text");
  }
}

export type CreditBalance = {
  tier: string;
  credits: number;
};

export async function getCredits(token: string): Promise<CreditBalance> {
  return fetchJson("/api/me/credits", {}, token);
}

export type TopUpResult = CreditBalance & { mode?: string; session_url?: string; error?: string };
export type VerifyTopUpResult = CreditBalance & { mode?: string; amount?: number; already_applied?: boolean };

export async function topUpCredits(token: string, amount: number, mode: "live" | "test" = "live"): Promise<TopUpResult> {
  return fetchJson("/api/me/credits/topup", {
    method: "POST",
    body: JSON.stringify({ amount, mode }),
  }, token);
}

export async function verifyTopUp(token: string, sessionId: string): Promise<VerifyTopUpResult> {
  return fetchJson("/api/me/credits/verify", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  }, token);
}

export async function setApiKey(token: string, provider: string, key: string): Promise<{ success: boolean }> {
  return fetchJson("/api/me/api-key", {
    method: "POST",
    body: JSON.stringify({ provider, key }),
  }, token);
}

export async function downloadOllamaModel(token: string, modelId: string): Promise<{ status: string; model: string }> {
  return fetchJson(`/api/ai/models/${encodeURIComponent(modelId)}/pull`, {
    method: "POST",
  }, token);
}

export async function checkBackendConnection() {
  try {
    const res = await fetch(`${BACKEND_URL || "/health"}`, { cache: "no-store", credentials: "include" });
    if (!res.ok) return { connected: false, ollama_ok: false };
    return res.json();
  } catch (e) {
    return { connected: false, ollama_ok: false };
  }
}
