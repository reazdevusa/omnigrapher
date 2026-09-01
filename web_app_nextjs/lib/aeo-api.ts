// AEO Studio API client (Phase 3).
// Talks to the backend endpoints mounted under /api/v1/aeo-studio.

// In the browser, default to a relative path so Next.js rewrites proxy requests
// to the backend. This avoids CORS and cross-origin issues. On the server,
// or when an explicit public API URL is set, use that absolute URL instead.
const BACKEND_URL =
  typeof window !== "undefined"
    ? process.env.NEXT_PUBLIC_API_URL || process.env.NEXT_PUBLIC_BACKEND_URL || ""
    : process.env.INTERNAL_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

const API_BASE = BACKEND_URL
  ? `${BACKEND_URL.replace(/\/$/, "")}/api/v1/aeo-studio`
  : "/api/v1/aeo-studio";

function getHeaders(apiKey: string, extra?: Record<string, string>) {
  return {
    "Content-Type": "application/json",
    "X-API-Key": apiKey,
    ...extra,
  };
}

export type GenerateRequest = {
  project_id?: number;
  project?: {
    name: string;
    business_name: string;
    business_type?: string;
    website_url?: string;
    location?: string;
    target_audience?: string;
    seed_keywords?: string[];
  };
  page_types?: string[];
  dry_run?: boolean;
};

export type GenerateResponse = {
  job_id: number;
  project_id: number;
  status: string;
  message: string;
  credits_deducted: number;
  page_outputs_created: number;
};

export type JobStatusResponse = {
  job_id: number;
  project_id: number;
  job_type: string;
  status: string;
  progress_percent: number;
  trace: { agent: string; step: string; timestamp: string; payload?: unknown }[];
  error_message?: string;
  started_at?: string;
  completed_at?: string;
};

export type JobResultResponse = {
  job_id: number;
  project_id: number;
  status: string;
  credits_deducted: number;
  pages: {
    page_id: number;
    page_type: string;
    slug: string;
    title: string;
    meta_description?: string;
    html: string;
    markdown: string;
    direct_answer_block?: string;
    aeo_score?: number;
    json_ld: unknown[];
  }[];
  audit_summary: { pages: { aeo_score?: number; page_id: number; page_type: string }[] };
  json_ld_schemas: unknown[];
};

export async function generateContent(
  apiKey: string,
  request: GenerateRequest
): Promise<GenerateResponse> {
  const res = await fetch(`${API_BASE}/generate`, {
    method: "POST",
    headers: getHeaders(apiKey),
    body: JSON.stringify(request),
  });
  const text = await res.text();
  if (!res.ok) {
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      detail = parsed.detail || text;
    } catch {}
    throw new Error(`HTTP ${res.status}: ${detail}`);
  }
  return JSON.parse(text);
}

export async function generateContentCsv(
  apiKey: string,
  file: File
): Promise<{ total_jobs: number; job_ids: number[]; credits_deducted: number; message: string }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/generate/csv`, {
    method: "POST",
    headers: { "X-API-Key": apiKey },
    body: form,
  });
  const text = await res.text();
  if (!res.ok) {
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      detail = parsed.detail || text;
    } catch {}
    throw new Error(`HTTP ${res.status}: ${detail}`);
  }
  return JSON.parse(text);
}

export async function fetchJobStatus(apiKey: string, jobId: number): Promise<JobStatusResponse> {
  const res = await fetch(`${API_BASE}/jobs/${jobId}/status`, {
    headers: getHeaders(apiKey),
  });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return JSON.parse(text);
}

export function subscribeJobStatus(
  apiKey: string,
  jobId: number,
  onStatus: (status: JobStatusResponse) => void,
  onError?: (err: Error) => void
): { close: () => void } {
  const url = `${API_BASE}/jobs/${jobId}/status/stream`;
  const eventSource = new EventSource(`${url}?api_key=${encodeURIComponent(apiKey)}`);
  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data && typeof data === "object") {
        onStatus(data as JobStatusResponse);
      }
      if (data?.status === "completed" || data?.status === "failed") {
        eventSource.close();
      }
    } catch (e) {
      // ignore non-json events
    }
  };
  eventSource.onerror = () => {
    eventSource.close();
    onError?.(new Error("SSE connection error"));
  };
  return { close: () => eventSource.close() };
}

export async function fetchJobResult(apiKey: string, jobId: number): Promise<JobResultResponse> {
  const res = await fetch(`${API_BASE}/jobs/${jobId}/result`, {
    headers: getHeaders(apiKey),
  });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return JSON.parse(text);
}

export async function topUpCredits(apiKey: string, amount: number): Promise<{ owner_id: number; credits_added: number; new_balance: number }> {
  const form = new URLSearchParams();
  form.append("amount", String(amount));
  const res = await fetch(`${API_BASE}/credits/top-up`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "X-API-Key": apiKey,
    },
    body: form.toString(),
  });
  const text = await res.text();
  if (!res.ok) {
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      detail = parsed.detail || text;
    } catch {}
    throw new Error(`HTTP ${res.status}: ${detail}`);
  }
  return JSON.parse(text);
}
