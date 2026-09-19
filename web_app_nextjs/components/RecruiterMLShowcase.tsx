"use client";

import * as React from "react";
import { toast } from "sonner";
import {
  Activity,
  BrainCircuit,
  Cpu,
  FileAudio,
  FileVideo,
  Gauge,
  History,
  Image as ImageIcon,
  Link as LinkIcon,
  Loader2,
  MessageSquare,
  Mic,
  ScanEye,
  Sparkles,
  Trash2,
  Upload,
  Zap,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/components/auth-provider";
import * as api from "@/lib/api";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

function DropZone({
  accept,
  label,
  icon: Icon,
  onFile,
  disabled,
}: {
  accept: string;
  label: string;
  icon: any;
  onFile: (f: File) => void;
  disabled?: boolean;
}) {
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = React.useState(false);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => !disabled && inputRef.current?.click()}
      onKeyDown={(e) => e.key === "Enter" && !disabled && inputRef.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const f = e.dataTransfer.files?.[0];
        if (f && !disabled) onFile(f);
      }}
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-8 text-center cursor-pointer transition-colors",
        dragging ? "border-primary bg-primary/5" : "border-border hover:border-primary/50",
        disabled && "opacity-50 cursor-not-allowed"
      )}
    >
      <Icon className="h-8 w-8 text-muted-foreground" />
      <p className="text-sm text-muted-foreground">{label}</p>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// History panel — persisted showcase results
// ---------------------------------------------------------------------------

const KIND_META: Record<string, { icon: any; label: string; tab: string }> = {
  transcript: { icon: Mic, label: "Speech & Video QA", tab: "speech" },
  visual: { icon: ScanEye, label: "Computer Vision", tab: "vision" },
  adapter_switch: { icon: BrainCircuit, label: "Adapter switch", tab: "ml" },
};

function historySummaryText(item: api.ShowcaseHistoryItem): string {
  const s = item.summary || {};
  if (item.kind === "transcript") {
    const dur = s.duration_seconds ? `${Math.round(s.duration_seconds)}s` : "";
    return [dur, `${s.segments ?? 0} segments`].filter(Boolean).join(" · ");
  }
  if (item.kind === "visual") {
    return `${s.ocr_blocks ?? 0} OCR blocks · ${s.objects ?? 0} regions`;
  }
  if (item.kind === "adapter_switch") {
    return s.switch_ms != null ? `${Number(s.switch_ms).toFixed(2)} ms` : "";
  }
  return "";
}

function HistoryPanel({
  refreshKey,
  onOpen,
}: {
  refreshKey: number;
  onOpen: (kind: string, detail: api.ShowcaseHistoryDetail) => void;
}) {
  const { token } = useAuth();
  const [items, setItems] = React.useState<api.ShowcaseHistoryItem[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [opening, setOpening] = React.useState<number | null>(null);

  const load = React.useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const r = await api.getShowcaseHistory(token);
      setItems(r.items);
    } catch {
      // history is best-effort; don't toast on every load failure
    } finally {
      setLoading(false);
    }
  }, [token]);

  React.useEffect(() => { load(); }, [load, refreshKey]);

  const openItem = async (item: api.ShowcaseHistoryItem) => {
    if (!token) return;
    setOpening(item.id);
    try {
      const detail = await api.getShowcaseHistoryItem(token, item.id);
      onOpen(item.kind, detail);
    } catch (e: any) {
      toast.error(e.message || "Could not load result");
    } finally {
      setOpening(null);
    }
  };

  const removeItem = async (e: React.MouseEvent, item: api.ShowcaseHistoryItem) => {
    e.stopPropagation();
    if (!token) return;
    try {
      await api.deleteShowcaseHistoryItem(token, item.id);
      setItems((prev) => prev.filter((i) => i.id !== item.id));
    } catch (err: any) {
      toast.error(err.message || "Delete failed");
    }
  };

  return (
    <Card className="h-fit">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <History className="h-4 w-4" /> Previous results
        </CardTitle>
        <CardDescription>Saved runs — click to reload</CardDescription>
      </CardHeader>
      <CardContent className="space-y-1 max-h-[70vh] overflow-y-auto">
        {loading && items.length === 0 && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
            <Loader2 className="h-3 w-3 animate-spin" /> Loading…
          </div>
        )}
        {!loading && items.length === 0 && (
          <p className="text-xs text-muted-foreground py-2">
            Nothing yet — transcribe media, analyze an image, or switch an adapter.
          </p>
        )}
        {items.map((item) => {
          const meta = KIND_META[item.kind] || KIND_META.transcript;
          const Icon = meta.icon;
          return (
            <button
              key={item.id}
              onClick={() => openItem(item)}
              className="w-full text-left rounded-md border border-transparent hover:border-border hover:bg-muted/50 p-2 group"
            >
              <div className="flex items-start gap-2">
                <Icon className="h-3.5 w-3.5 mt-0.5 shrink-0 text-muted-foreground" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium truncate">{item.title}</p>
                  <p className="text-[10px] text-muted-foreground truncate">
                    {meta.label}
                    {historySummaryText(item) ? ` · ${historySummaryText(item)}` : ""}
                  </p>
                  {item.created_at && (
                    <p className="text-[10px] text-muted-foreground/70">
                      {new Date(item.created_at).toLocaleString()}
                    </p>
                  )}
                </div>
                {opening === item.id ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />
                ) : (
                  <Trash2
                    className="h-3.5 w-3.5 shrink-0 text-muted-foreground/0 group-hover:text-muted-foreground hover:text-destructive"
                    onClick={(e) => removeItem(e, item)}
                  />
                )}
              </div>
            </button>
          );
        })}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Tab 1 — Speech
// ---------------------------------------------------------------------------

function SpeechTab({
  inject,
  onSaved,
}: {
  inject?: { id: number; payload: api.TranscriptResponse } | null;
  onSaved?: () => void;
}) {
  const { token } = useAuth();
  const [busy, setBusy] = React.useState(false);
  const [url, setUrl] = React.useState("");
  const [result, setResult] = React.useState<api.TranscriptResponse | null>(null);
  const [summary, setSummary] = React.useState("");
  const [summarizing, setSummarizing] = React.useState(false);
  const [question, setQuestion] = React.useState("");
  const [askResult, setAskResult] = React.useState<api.MediaAskResponse | null>(null);
  const [asking, setAsking] = React.useState(false);
  const videoRef = React.useRef<HTMLVideoElement | null>(null);

  // Reload a persisted result when a history item is selected
  React.useEffect(() => {
    if (inject?.payload) {
      setResult(inject.payload);
      setSummary("");
      setAskResult(null);
    }
  }, [inject]);

  const runUpload = async (file: File) => {
    if (!token) return toast.error("Please sign in first");
    setBusy(true);
    setResult(null);
    setSummary("");
    setAskResult(null);
    try {
      const r = await api.transcribeMediaUpload(token, file);
      setResult(r);
      onSaved?.();
      toast.success(`Transcribed ${r.segments.length} segments (${r.indexed_chunks} indexed${r.graph_entities ? `, ${r.graph_entities} graph entities` : ""})`);
    } catch (e: any) {
      toast.error(e.message || "Transcription failed");
    } finally {
      setBusy(false);
    }
  };

  const runUrl = async () => {
    if (!token) return toast.error("Please sign in first");
    if (!url.trim()) return;
    setBusy(true);
    setResult(null);
    setSummary("");
    setAskResult(null);
    try {
      const r = await api.transcribeMediaUrl(token, url.trim());
      setResult(r);
      onSaved?.();
      toast.success(`Transcribed from URL (${r.segments.length} segments)`);
    } catch (e: any) {
      toast.error(e.message || "URL transcription failed");
    } finally {
      setBusy(false);
    }
  };

  const runSummary = async () => {
    if (!token || !result) return;
    setSummarizing(true);
    try {
      const r = await api.summarizeTranscript(token, result.text);
      setSummary(r.summary);
    } catch (e: any) {
      toast.error(e.message || "Summarization failed");
    } finally {
      setSummarizing(false);
    }
  };

  const runAsk = async () => {
    if (!token || !result || !question.trim()) return;
    setAsking(true);
    try {
      const r = await api.askMediaQuestion(token, question.trim(), result.source_ref);
      setAskResult(r);
    } catch (e: any) {
      toast.error(e.message || "Q&A failed");
    } finally {
      setAsking(false);
    }
  };

  const seekTo = (seconds: number) => {
    if (videoRef.current) {
      videoRef.current.currentTime = seconds;
      videoRef.current.play().catch(() => {});
    }
  };

  return (
    <div className="space-y-4">
      <DropZone
        accept=".mp3,.mp4,.wav,.m4a,audio/*,video/*"
        label="Drag & drop audio/video (.mp3, .mp4, .wav, .m4a) — or click to browse"
        icon={FileAudio}
        onFile={runUpload}
        disabled={busy}
      />

      <div className="flex gap-2">
        <div className="relative flex-1">
          <LinkIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="Or paste a YouTube / Vimeo / network share URL…"
            className="pl-9"
            disabled={busy}
            onKeyDown={(e) => e.key === "Enter" && runUrl()}
          />
        </div>
        <Button onClick={runUrl} disabled={busy || !url.trim()}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Mic className="h-4 w-4 mr-2" />}
          Transcribe URL
        </Button>
      </div>

      {busy && (
        <Card>
          <CardContent className="py-6 flex items-center gap-3 text-sm text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
            Running faster-whisper locally — first run downloads the base model…
          </CardContent>
        </Card>
      )}

      {result && (
        <>
          <div className="flex items-center gap-2 flex-wrap">
            <Badge variant="secondary">Whisper {result.model}</Badge>
            <Badge variant="secondary">{result.device}</Badge>
            {result.language && <Badge variant="outline">lang: {result.language}</Badge>}
            {result.duration_seconds != null && (
              <Badge variant="outline">{Math.round(result.duration_seconds)}s audio</Badge>
            )}
            <Badge variant="outline">{result.indexed_chunks} chunks indexed into RAG</Badge>
            <Button size="sm" variant="outline" onClick={runSummary} disabled={summarizing}>
              {summarizing ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Sparkles className="h-4 w-4 mr-2" />}
              Summarize video
            </Button>
          </div>

          {summary && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">AI Summary</CardTitle>
              </CardHeader>
              <CardContent className="text-sm whitespace-pre-wrap">{summary}</CardContent>
            </Card>
          )}

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Transcript ({result.segments.length} segments)</CardTitle>
              <CardDescription>Click a timestamp to seek the video player</CardDescription>
            </CardHeader>
            <CardContent className="max-h-72 overflow-y-auto space-y-1">
              {result.segments.map((s, i) => (
                <div key={i} className="text-sm flex gap-3 group">
                  <button
                    onClick={() => seekTo(s.start)}
                    className="font-mono text-primary hover:underline shrink-0"
                    title="Seek video"
                  >
                    [{s.start_label}]
                  </button>
                  <span className="text-foreground/90">{s.text}</span>
                </div>
              ))}
            </CardContent>
          </Card>

          {result.source_ref.match(/\.(mp4|webm|mov|mkv)$/i) && (
            <video ref={videoRef} controls className="w-full rounded-lg border border-border" src={result.source_ref.startsWith("http") ? result.source_ref : undefined} />
          )}

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <MessageSquare className="h-4 w-4" /> Multimedia Q&amp;A
              </CardTitle>
              <CardDescription>
                Ask questions about this transcript — answers cite exact timestamps
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex gap-2">
                <Input
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  placeholder="e.g. What did they say about adapters?"
                  disabled={asking}
                  onKeyDown={(e) => e.key === "Enter" && runAsk()}
                />
                <Button onClick={runAsk} disabled={asking || !question.trim()}>
                  {asking ? <Loader2 className="h-4 w-4 animate-spin" /> : "Ask"}
                </Button>
              </div>
              {askResult && (
                <div className="space-y-2">
                  <p className="text-sm whitespace-pre-wrap">{askResult.answer}</p>
                  {askResult.citations.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {askResult.citations.map((c, i) => (
                        <button
                          key={i}
                          onClick={() => seekTo(c.start)}
                          title={c.text.slice(0, 120)}
                          className="font-mono text-xs text-primary hover:underline border border-border rounded px-1.5 py-0.5"
                        >
                          [{c.file_name} @ {c.start_label || `${c.start.toFixed(0)}s`}]
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 2 — Vision
// ---------------------------------------------------------------------------

function VisionTab({
  inject,
  onSaved,
}: {
  inject?: { id: number; payload: api.VisionAnalyzeResponse } | null;
  onSaved?: () => void;
}) {
  const { token } = useAuth();
  const [busy, setBusy] = React.useState(false);
  const [preview, setPreview] = React.useState<string | null>(null);
  const [imgSize, setImgSize] = React.useState<{ w: number; h: number } | null>(null);
  const [result, setResult] = React.useState<api.VisionAnalyzeResponse | null>(null);
  const [vQuestion, setVQuestion] = React.useState("");
  const [vAnswer, setVAnswer] = React.useState<api.MediaAskResponse | null>(null);
  const [vAsking, setVAsking] = React.useState(false);
  const imgRef = React.useRef<HTMLImageElement | null>(null);

  // Reload a persisted result when a history item is selected. The original
  // image file isn't stored, so the overlay preview is skipped on restore.
  React.useEffect(() => {
    if (inject?.payload) {
      setResult(inject.payload);
      setPreview(null);
      setVAnswer(null);
    }
  }, [inject]);

  const run = async (file: File) => {
    if (!token) return toast.error("Please sign in first");
    setBusy(true);
    setResult(null);
    setVAnswer(null);
    const url = URL.createObjectURL(file);
    setPreview(url);
    try {
      const r = await api.analyzeVisionFile(token, file);
      setResult(r);
      onSaved?.();
      toast.success(`Vision analysis done — ${r.ocr_blocks.length} OCR blocks, ${r.objects.length} regions, ${r.indexed_chunks} chunks indexed`);
    } catch (e: any) {
      toast.error(e.message || "Vision analysis failed");
    } finally {
      setBusy(false);
    }
  };

  const runVAsk = async () => {
    if (!token || !result || !vQuestion.trim()) return;
    setVAsking(true);
    try {
      const r = await api.askVisualQuestion(token, vQuestion.trim(), result.source_ref);
      setVAnswer(r);
    } catch (e: any) {
      toast.error(e.message || "Visual Q&A failed");
    } finally {
      setVAsking(false);
    }
  };

  React.useEffect(() => {
    return () => { if (preview) URL.revokeObjectURL(preview); };
  }, [preview]);

  const scale = result && imgSize && result.width
    ? imgSize.w / result.width
    : 1;

  return (
    <div className="space-y-4">
      <DropZone
        accept="image/*,.mp4,.mov,.webm,.mkv"
        label="Drop an image (screenshots, charts, diagrams) or video to analyze"
        icon={ScanEye}
        onFile={run}
        disabled={busy}
      />

      {busy && (
        <Card>
          <CardContent className="py-6 flex items-center gap-3 text-sm text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
            Running OpenCV + RapidOCR…
          </CardContent>
        </Card>
      )}

      {result && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {preview && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Detections overlay</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="relative inline-block w-full">
                <img
                  ref={imgRef}
                  src={preview}
                  alt="analysis"
                  className="w-full rounded-md"
                  onLoad={(e) => {
                    const el = e.currentTarget;
                    setImgSize({ w: el.clientWidth, h: el.clientHeight });
                  }}
                />
                {imgSize && result.width > 0 && (
                  <svg
                    className="absolute inset-0 pointer-events-none"
                    viewBox={`0 0 ${result.width} ${result.height}`}
                    preserveAspectRatio="none"
                    style={{ width: "100%", height: "100%" }}
                  >
                    {result.objects.map((o, i) => (
                      <g key={`obj-${i}`}>
                        <rect x={o.x} y={o.y} width={o.w} height={o.h} fill="none" stroke="hsl(var(--primary))" strokeWidth={2 / scale} opacity={0.9} />
                      </g>
                    ))}
                    {result.ocr_blocks.map((b, i) => (
                      <g key={`ocr-${i}`}>
                        <rect x={b.bbox.x} y={b.bbox.y} width={b.bbox.w} height={b.bbox.h} fill="none" stroke="#22c55e" strokeWidth={1.5 / scale} opacity={0.8} />
                      </g>
                    ))}
                  </svg>
                )}
              </div>
              <p className="text-xs text-muted-foreground mt-2">
                Primary-color boxes: detected regions · Green boxes: OCR text
              </p>
            </CardContent>
          </Card>
          )}

          <div className="space-y-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">OCR text ({result.ocr_blocks.length} blocks)</CardTitle>
              </CardHeader>
              <CardContent className="max-h-48 overflow-y-auto">
                {result.ocr_text ? (
                  <pre className="text-xs whitespace-pre-wrap font-mono">{result.ocr_text}</pre>
                ) : (
                  <p className="text-sm text-muted-foreground">No text detected.</p>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Chart data extraction</CardTitle>
              </CardHeader>
              <CardContent>
                {result.chart_rows.length > 0 ? (
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-muted-foreground border-b border-border">
                        <th className="py-1">Label</th>
                        <th className="py-1">Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.chart_rows.map((row, i) => (
                        <tr key={i} className="border-b border-border/50">
                          <td className="py-1">{row[0]}</td>
                          <td className="py-1 font-mono">{row[1]}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="text-sm text-muted-foreground">No chart-like label/value pairs found.</p>
                )}
              </CardContent>
            </Card>

            {result.caption && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">VLM caption</CardTitle>
                </CardHeader>
                <CardContent className="text-sm whitespace-pre-wrap">{result.caption}</CardContent>
              </Card>
            )}

            {result.frames_sampled > 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">Video frames sampled: {result.frames_sampled}</CardTitle>
                </CardHeader>
              </Card>
            )}

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm flex items-center gap-2">
                  <MessageSquare className="h-4 w-4" /> Visual chat
                </CardTitle>
                <CardDescription>
                  Diagram-grounded Q&amp;A over the indexed visual features
                  {result.indexed_chunks > 0 && ` (${result.indexed_chunks} chunks indexed)`}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex gap-2">
                  <Input
                    value={vQuestion}
                    onChange={(e) => setVQuestion(e.target.value)}
                    placeholder="e.g. What does this chart show?"
                    disabled={vAsking}
                    onKeyDown={(e) => e.key === "Enter" && runVAsk()}
                  />
                  <Button onClick={runVAsk} disabled={vAsking || !vQuestion.trim()}>
                    {vAsking ? <Loader2 className="h-4 w-4 animate-spin" /> : "Ask"}
                  </Button>
                </div>
                {vAnswer && (
                  <div className="space-y-2">
                    <p className="text-sm whitespace-pre-wrap">{vAnswer.answer}</p>
                    {vAnswer.citations.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {vAnswer.citations.map((c, i) => (
                          <span
                            key={i}
                            title={c.text.slice(0, 120)}
                            className="font-mono text-xs text-primary border border-border rounded px-1.5 py-0.5"
                          >
                            [{c.file_name}]
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 3 — ML Engine & Edge Telemetry
// ---------------------------------------------------------------------------

function MLEngineTab({ onSaved }: { onSaved?: () => void }) {
  const { token } = useAuth();
  const [adapters, setAdapters] = React.useState<api.MLAdaptersResponse | null>(null);
  const [gpu, setGpu] = React.useState<api.GPUSStatus | null>(null);
  const [selected, setSelected] = React.useState<string>("");
  const [switching, setSwitching] = React.useState(false);
  const [switchLog, setSwitchLog] = React.useState<Array<{ adapter: string; switch_ms: number; warmed: boolean; ts: number }>>([]);
  const [bench, setBench] = React.useState<api.EdgeBenchmark | null>(null);
  const [benchBusy, setBenchBusy] = React.useState(false);

  const refresh = React.useCallback(async () => {
    if (!token) return;
    try {
      const [a, g] = await Promise.all([api.getMLAdapters(token), api.getMLGpu(token)]);
      setAdapters(a);
      setGpu(g);
      if (!selected && a.active_adapter) setSelected(a.active_adapter);
    } catch (e: any) {
      toast.error(e.message || "Failed to load ML engine status");
    }
  }, [token, selected]);

  React.useEffect(() => { refresh(); }, [token]);
  React.useEffect(() => {
    const id = setInterval(async () => {
      if (!token) return;
      try { setGpu(await api.getMLGpu(token)); } catch {}
    }, 5000);
    return () => clearInterval(id);
  }, [token]);

  const doSwitch = async () => {
    if (!token || !selected) return;
    setSwitching(true);
    try {
      const r = await api.switchMLAdapter(token, selected);
      setSwitchLog((prev) => [...prev, { adapter: r.adapter, switch_ms: r.switch_ms, warmed: r.warmed, ts: Date.now() }].slice(-20));
      onSaved?.();
      toast.success(`Adapter switched in ${r.switch_ms.toFixed(2)} ms ${r.switch_ms < 10 ? "(sub-10ms ✓)" : "(cold load)"}`);
      refresh();
    } catch (e: any) {
      toast.error(e.message || "Switch failed");
    } finally {
      setSwitching(false);
    }
  };

  const runBench = async () => {
    if (!token) return;
    setBenchBusy(true);
    try {
      setBench(await api.runEdgeBenchmark(token));
    } catch (e: any) {
      toast.error(e.message || "Benchmark failed");
    } finally {
      setBenchBusy(false);
    }
  };

  const vramPct = gpu?.vram_total_mb ? Math.round(((gpu.vram_used_mb || 0) / gpu.vram_total_mb) * 100) : 0;
  const domainEntries = adapters ? Object.entries(adapters.domains) : [];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2"><Cpu className="h-4 w-4" /> GPU / VRAM</CardTitle>
          <CardDescription>Polled live every 5s</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {gpu?.gpu_available ? (
            <>
              <div className="flex items-center justify-between text-sm">
                <span>{gpu.device_name}</span>
                <span className="font-mono">{gpu.vram_used_mb} / {gpu.vram_total_mb} MB</span>
              </div>
              <Progress value={vramPct} />
              <p className="text-xs text-muted-foreground">{vramPct}% VRAM in use · {gpu.device_count} device(s)</p>
            </>
          ) : (
            <div className="text-sm text-muted-foreground space-y-1">
              <p>No CUDA GPU visible to this process (CPU mode).</p>
              {adapters && (
                <p>External storage drive: {adapters.external_drive_ready ? "connected" : "not connected"}</p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2"><BrainCircuit className="h-4 w-4" /> PEFT adapter control</CardTitle>
          <CardDescription>
            Engine: <span className="font-mono">{adapters?.engine_url}</span>{" "}
            {adapters && (
              <Badge variant={adapters.engine_reachable ? "default" : "destructive"} className="ml-1">
                {adapters.engine_reachable ? "online" : "offline"}
              </Badge>
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <select
              className="flex-1 text-sm border rounded px-2 py-1.5 bg-background"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              <option value="">Select adapter / domain…</option>
              {domainEntries.map(([domain, mapped]) => (
                <option key={domain} value={mapped || domain}>
                  {domain}{mapped ? ` → ${mapped}` : ""}
                </option>
              ))}
              {(adapters?.engine_adapters || [])
                .filter((a) => a && !domainEntries.some(([, m]) => m === a))
                .map((a) => (
                  <option key={a} value={a}>{a}</option>
                ))}
            </select>
            <Button onClick={doSwitch} disabled={switching || !selected}>
              {switching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4 mr-2" />}
              Switch
            </Button>
          </div>

          {switchLog.length > 0 && (
            <div className="max-h-40 overflow-y-auto rounded-md border border-border">
              <table className="w-full text-xs font-mono">
                <thead>
                  <tr className="text-left text-muted-foreground border-b border-border">
                    <th className="p-2">Time</th>
                    <th className="p-2">Adapter</th>
                    <th className="p-2">Switch time</th>
                    <th className="p-2">Sub-10ms</th>
                  </tr>
                </thead>
                <tbody>
                  {[...switchLog].reverse().map((e, i) => (
                    <tr key={i} className="border-b border-border/50">
                      <td className="p-2">{new Date(e.ts).toLocaleTimeString()}</td>
                      <td className="p-2">{e.adapter}</td>
                      <td className="p-2">{e.switch_ms.toFixed(2)} ms</td>
                      <td className="p-2">{e.switch_ms < 10 ? "✅" : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2"><Gauge className="h-4 w-4" /> Edge ML micro-benchmark (ONNX Runtime)</CardTitle>
          <CardDescription>Runs a tiny conv net to measure realistic edge inference</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Button onClick={runBench} disabled={benchBusy} variant="outline">
            {benchBusy ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Activity className="h-4 w-4 mr-2" />}
            Run benchmark
          </Button>
          {bench && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="rounded-md border border-border p-3">
                <p className="text-xs text-muted-foreground">Throughput</p>
                <p className="text-xl font-semibold">{bench.fps} FPS</p>
              </div>
              <div className="rounded-md border border-border p-3">
                <p className="text-xs text-muted-foreground">Latency</p>
                <p className="text-xl font-semibold">{bench.latency_ms} ms</p>
              </div>
              <div className="rounded-md border border-border p-3">
                <p className="text-xs text-muted-foreground">Process RSS</p>
                <p className="text-xl font-semibold">{bench.memory_mb} MB</p>
              </div>
              <div className="rounded-md border border-border p-3">
                <p className="text-xs text-muted-foreground">Backend</p>
                <p className="text-xl font-semibold">{bench.backend}</p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Root component
// ---------------------------------------------------------------------------

export function RecruiterMLShowcase() {
  const { user } = useAuth();
  const [tab, setTab] = React.useState("speech");
  const [historyKey, setHistoryKey] = React.useState(0);
  const [injectSpeech, setInjectSpeech] = React.useState<{ id: number; payload: api.TranscriptResponse } | null>(null);
  const [injectVision, setInjectVision] = React.useState<{ id: number; payload: api.VisionAnalyzeResponse } | null>(null);

  const openHistoryItem = (kind: string, detail: api.ShowcaseHistoryDetail) => {
    if (kind === "transcript") {
      setInjectSpeech({ id: detail.id, payload: detail.payload });
      setTab("speech");
    } else if (kind === "visual") {
      setInjectVision({ id: detail.id, payload: detail.payload });
      setTab("vision");
    } else {
      setTab("ml");
      toast.info(`Adapter "${detail.payload?.adapter ?? detail.title}" was switched in ${Number(detail.payload?.switch_ms ?? 0).toFixed(2)} ms`);
    }
  };

  const refreshHistory = () => setHistoryKey((k) => k + 1);

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="grid grid-cols-1 lg:grid-cols-[18rem_minmax(0,1fr)] gap-6 items-start">
        <aside className="lg:sticky lg:top-6 order-2 lg:order-1">
          <HistoryPanel refreshKey={historyKey} onOpen={openHistoryItem} />
        </aside>

        <div className="space-y-6 order-1 lg:order-2">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <Zap className="h-6 w-6 text-primary" />
              Live AI Showcase
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              Speech-to-text (faster-whisper) · Computer vision (OpenCV + RapidOCR) · Custom PEFT neural nets & edge ML telemetry — all running live in this app.
            </p>
            {!user && (
              <p className="text-sm text-destructive mt-2">Sign in to run live tests.</p>
            )}
          </div>

          <Tabs value={tab} onValueChange={setTab} className="w-full">
            <TabsList className="grid w-full grid-cols-3">
              <TabsTrigger value="speech"><Mic className="h-4 w-4 mr-2" />Speech & Video QA</TabsTrigger>
              <TabsTrigger value="vision"><ImageIcon className="h-4 w-4 mr-2" />Computer Vision</TabsTrigger>
              <TabsTrigger value="ml"><BrainCircuit className="h-4 w-4 mr-2" />Neural Net & Edge ML</TabsTrigger>
            </TabsList>
            <TabsContent value="speech" className="mt-4"><SpeechTab inject={injectSpeech} onSaved={refreshHistory} /></TabsContent>
            <TabsContent value="vision" className="mt-4"><VisionTab inject={injectVision} onSaved={refreshHistory} /></TabsContent>
            <TabsContent value="ml" className="mt-4"><MLEngineTab onSaved={refreshHistory} /></TabsContent>
          </Tabs>

          <Card className="bg-muted/30">
            <CardContent className="py-4 text-xs text-muted-foreground flex flex-wrap gap-x-6 gap-y-1">
              <span><FileVideo className="inline h-3 w-3 mr-1" />yt-dlp bestaudio extraction (no hi-res video downloads)</span>
              <span><Mic className="inline h-3 w-3 mr-1" />CTranslate2 int8 — local, private transcription</span>
              <span><ScanEye className="inline h-3 w-3 mr-1" />ONNX Runtime OCR — no external Tesseract binary</span>
              <span><BrainCircuit className="inline h-3 w-3 mr-1" />Multi-LoRA hot-swap in VRAM</span>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
