"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { newSessionId } from "@/lib/sessions";
import { Sidebar } from "@/components/sidebar";
import { useAuth } from "@/components/auth-provider";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import * as api from "@/lib/api";
import { MarkdownContent } from "@/components/markdown-content";
import { toast } from "sonner";
import {
  FileText,
  ChevronLeft,
  Boxes,
  Bot,
  User,
  Send,
  Loader2,
  Image as ImageIcon,
  Maximize2,
  Minimize2,
  Lock,
  Check,
  ChevronDown,
} from "lucide-react";
import Link from "next/link";

type Citation = { page: number; chunk_id: string; source: string };
type Message = { role: "user" | "assistant"; content: string; citations?: Citation[]; mode?: "document" | "assistant" };

function formatMarkdown(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/__(.+?)__/g, "<strong>$1</strong>")
    .replace(/_(.+?)_/g, "<em>$1</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function toUserError(raw: string, model: string = "selected model"): string {
  const lower = raw.toLowerCase();
  if (lower.includes("quota") || lower.includes("insufficient_quota") || lower.includes("429") || lower.includes("rate limit") || lower.includes("too many requests")) {
    return `The selected model (${model}) has exceeded its API quota. Switch to a free model or add a payment method.`;
  }
  if (lower.includes("api key") || lower.includes("unauthorized") || lower.includes("authentication") || lower.includes("401")) {
    return `The selected model (${model}) needs a valid API key. Add one in Settings/Billing.`;
  }
  if (lower.includes("ollama") || lower.includes("local model")) {
    return `The local model is not available. Make sure Ollama is running, or switch to a different model.`;
  }
  return `The selected model (${model}) returned an error. Try a free local model or check your API key.`;
}

function AssistantContent({
  content,
  onJumpPage,
  filename,
}: {
  content: string;
  onJumpPage: (page: number) => void;
  filename: string;
}) {
  const parts = content.split(/(\[Page \d+\]|\[Source: [^\]]+, Page \d+\])/g);
  const handleClick = (e: React.MouseEvent<HTMLAnchorElement>, source: string, page: number) => {
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (source !== filename) return;
    e.preventDefault();
    onJumpPage(page);
  };
  return (
    <div className="whitespace-pre-wrap">
      {parts.map((part, i) => {
        const pageMatch = part.match(/^\[Page (\d+)\]$/);
        if (pageMatch) {
          const page = parseInt(pageMatch[1], 10);
          return (
            <a
              key={i}
              href={`/documents/${encodeURIComponent(filename)}#page=${page}`}
              onClick={(e) => handleClick(e, filename, page)}
              className="inline-flex items-center px-1.5 py-0.5 rounded bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20 mx-0.5"
            >
              [Page {page}]
            </a>
          );
        }
        const sourceMatch = part.match(/^\[Source: ([^\]]+), Page (\d+)\]$/);
        if (sourceMatch) {
          const source = sourceMatch[1].trim();
          const page = parseInt(sourceMatch[2], 10);
          return (
            <a
              key={i}
              href={`/documents/${encodeURIComponent(source)}#page=${page}`}
              onClick={(e) => handleClick(e, source, page)}
              title={`Jump to page ${page}`}
              className="inline-flex items-center px-1.5 py-0.5 rounded bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20 mx-0.5"
            >
              [Source: {source}, Page {page}]
            </a>
          );
        }
        return <MarkdownContent key={i} content={part} />;
      })}
    </div>
  );
}

export default function DocumentPage() {
  const { token } = useAuth();
  const params = useParams();
  const searchParams = useSearchParams();
  const filename = decodeURIComponent(params.filename as string);
  const [content, setContent] = useState<any>(null);
  const [chunks, setChunks] = useState<any[]>([]);
  const [activeTab, setActiveTab] = useState<"content" | "chunks" | "original">("original");
  const [rawUrl, setRawUrl] = useState<string | null>(null);
  const [rawType, setRawType] = useState<string>("");
  const [rawText, setRawText] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [pdfLoading, setPdfLoading] = useState(true);
  const [loading, setLoading] = useState(true);

  const [messages, setMessages] = useState<Message[]>([]);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"document" | "assistant">("document");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionTitle, setSessionTitle] = useState<string>("Document Chat");
  const [scope, setScope] = useState<"single" | "knowledge_base">("single");
  const [documentModel, setDocumentModel] = useState("no_llm");
  const [assistantModel, setAssistantModel] = useState("no_llm");
  const [hasMounted, setHasMounted] = useState(false);
  const selectedModel = mode === "document" ? documentModel : assistantModel;
  const setSelectedModel = useCallback((id: string) => {
    if (mode === "document") setDocumentModel(id);
    else setAssistantModel(id);
  }, [mode]);
  const [models, setModels] = useState<api.ModelInfo[]>([]);
  const [isLoadingModels, setIsLoadingModels] = useState(false);
  const [modelsError, setModelsError] = useState(false);
  const [isFullScreen, setIsFullScreen] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [activePage, setActivePage] = useState(1);
  const [fallback, setFallback] = useState<{ reason: string; message: string; model: string } | null>(null);
  const [upgradeOpen, setUpgradeOpen] = useState(false);
  const [upgradeModel, setUpgradeModel] = useState<api.ModelInfo | null>(null);
  const [byokKey, setByokKey] = useState("");
  const [byokProvider, setByokProvider] = useState("google");
  const [isSubmittingByok, setIsSubmittingByok] = useState(false);
  const [downloadingModels, setDownloadingModels] = useState<Set<string>>(new Set());
  const chatBottomRef = useRef<HTMLDivElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    setHasMounted(true);
    try {
      const doc = localStorage.getItem("document_viewer_document_model_v2");
      const asst = localStorage.getItem("document_viewer_assistant_model_v2");
      if (doc) setDocumentModel(doc);
      if (asst) setAssistantModel(asst);
    } catch {}
  }, []);

  useEffect(() => {
    if (!hasMounted) return;
    try {
      localStorage.setItem("document_viewer_document_model_v2", documentModel);
    } catch {}
  }, [documentModel, hasMounted]);

  useEffect(() => {
    if (!hasMounted) return;
    try {
      localStorage.setItem("document_viewer_assistant_model_v2", assistantModel);
    } catch {}
  }, [assistantModel, hasMounted]);

  const jumpToPage = (page: number) => {
    if (activeTab !== "original") setActiveTab("original");
    setActivePage(page);
  };

  useEffect(() => {
    // Read #page=N from the URL on the client so citation links jump to the cited page.
    const match = typeof window !== "undefined" ? window.location.hash.match(/page=(\d+)/) : null;
    if (match) {
      const page = parseInt(match[1], 10);
      if (page > 0) setActivePage(page);
    }
  }, []);

  useEffect(() => {
    if (!token) return;
    setIsLoadingModels(true);
    setModelsError(false);
    api.getModels(token)
      .then((list) => {
        const sorted = [...list].sort((a, b) => {
          if (a.id === "no_llm") return -1;
          if (b.id === "no_llm") return 1;
          return 0;
        });
        setModels(sorted);
        const firstLLM = sorted.find((m) => m.allowed && m.downloaded !== false && m.id !== "no_llm");
        if (documentModel === "no_llm" && firstLLM) setDocumentModel(firstLLM.id);
        if (assistantModel === "no_llm" && firstLLM) setAssistantModel(firstLLM.id);
      })
      .catch(() => setModelsError(true))
      .finally(() => setIsLoadingModels(false));
  }, [token]);

  // Poll the model list while any Ollama model is being downloaded.
  useEffect(() => {
    if (downloadingModels.size === 0 || !token) return;
    const interval = window.setInterval(async () => {
      try {
        const loaded = await api.getModels(token);
        const sorted = [...loaded].sort((a, b) => {
          if (a.id === "no_llm") return -1;
          if (b.id === "no_llm") return 1;
          return 0;
        });
        setModels(sorted);
        const completed: string[] = [];
        downloadingModels.forEach((id) => {
          const m = sorted.find((x) => x.id === id);
          if (m && m.downloaded === true) completed.push(id);
        });
        if (completed.length > 0) {
          setDownloadingModels((prev) => {
            const next = new Set(prev);
            completed.forEach((id) => next.delete(id));
            return next;
          });
          completed.forEach((id) => {
            const m = sorted.find((x) => x.id === id);
            if (m && m.allowed && m.downloaded !== false) {
              setSelectedModel(id);
              toast.success(`${id} is ready to use`);
            } else {
              toast.success(`${id} downloaded`);
            }
          });
        }
      } catch {}
    }, 3000);
    return () => window.clearInterval(interval);
  }, [downloadingModels, token, setSelectedModel]);

  const downloadModel = useCallback(async (modelId: string) => {
    if (!token) {
      toast.error("Please sign in first");
      return;
    }
    if (downloadingModels.has(modelId)) return;
    setDownloadingModels((prev) => new Set(prev).add(modelId));
    try {
      const result = await api.downloadOllamaModel(token, modelId);
      toast.info(`Downloading ${result.model}...`);
    } catch (err: any) {
      setDownloadingModels((prev) => {
        const next = new Set(prev);
        next.delete(modelId);
        return next;
      });
      toast.error(err?.message || `Could not start download for ${modelId}`);
    }
  }, [token, downloadingModels]);

  useEffect(() => {
    setByokProvider(upgradeModel?.provider ?? "google");
    setByokKey("");
  }, [upgradeModel]);

  const querySession = searchParams.get("session");

  // Rehydrate an existing document chat session from the URL or localStorage.
  useEffect(() => {
    if (!token || !filename) return;
    const storageKey = `kb_doc_chat_session_${filename}`;
    let activeId = querySession;
    if (!activeId) {
      try {
        activeId = localStorage.getItem(storageKey);
      } catch {}
    }
    if (!activeId) {
      activeId = newSessionId();
      try {
        localStorage.setItem(storageKey, activeId);
      } catch {}
    }
    if (!querySession && activeId) {
      try {
        window.history.replaceState(null, "", `?session=${encodeURIComponent(activeId)}`);
      } catch {}
    }
    setSessionId(activeId);
    api.getChatHistory(token, activeId)
      .then((s) => {
        setSessionTitle(s.title || "Document Chat");
        setMessages(s.messages as Message[]);
      })
      .catch((err: any) => {
        if (err?.message?.includes("404") || err?.message?.toLowerCase().includes("not found")) {
          // No prior session; start fresh.
        } else {
          toast.error(err?.message || "Could not load chat history");
        }
      });
  }, [token, filename, querySession]);

  const saveSession = useCallback(async (id: string, messagesToSave: Message[], title?: string) => {
    if (!token) return;
    try {
      await api.saveChatHistory(token, id, {
        title: title ?? sessionTitle,
        document: filename,
        model: selectedModel,
        mode,
        messages: messagesToSave as api.ChatHistoryMessage[],
      });
    } catch (err: any) {
      toast.error(err?.message || "Could not save chat history");
    }
  }, [token, filename, selectedModel, mode, sessionTitle]);

  useEffect(() => {
    if (!token || !filename) return;
    let objectUrl: string | null = null;
    setPdfLoading(true);
    setLoading(true);

    const loadPdf = async () => {
      if (filename.toLowerCase().endsWith(".pdf")) {
        try {
          const res = await api.getDocumentRaw(token, filename);
          if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
          const blob = await res.blob();
          objectUrl = URL.createObjectURL(blob);
          setPdfUrl(objectUrl);
          setRawType("application/pdf");
        } catch (e: any) {
          toast.error(`Failed to load PDF: ${e.message || "Unknown error"}`);
          setRawType("application/pdf");
          const fallbackUrl = api.getDocumentRawUrl(token, filename);
          setPdfUrl(fallbackUrl);
          setRawUrl(fallbackUrl);
        }
      } else {
        const directUrl = api.getDocumentRawUrl(token, filename);
        setRawUrl(directUrl);
        setRawType("");
      }
      setPdfLoading(false);
    };

    const loadMetadata = async () => {
      try {
        const [contentRes, chunksRes] = await Promise.all([
          api.getDocumentContent(token, filename),
          api.getDocumentChunks(token, filename),
        ]);
        setContent(contentRes);
        setChunks(chunksRes.chunks || []);

        const isPdf = filename.toLowerCase().endsWith(".pdf");
        const isTxt = filename.toLowerCase().endsWith(".txt");

        // Do not override rawType for PDFs; keep the "application/pdf" set by loadPdf.
        if (!isPdf) {
          const detectedType =
            contentRes.type ||
            (isTxt ? "text/plain" : "");
          setRawType(detectedType);
          if ((detectedType || "").startsWith("text/") || detectedType === "application/json") {
            setRawText(contentRes.content || null);
          } else {
            setRawText(null);
          }
        }
      } catch (e: any) {
        toast.error(e.message || "Failed to load document metadata");
      } finally {
        setLoading(false);
      }
    };

    loadPdf();
    loadMetadata();

    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [token, filename]);


  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isFullScreen) setIsFullScreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isFullScreen]);

  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = async () => {
    if (!token || !query.trim() || isStreaming) return;
    const userMsg = query.trim();
    setQuery("");

    // Ensure a session id exists for this document before sending.
    let activeSessionId = sessionId;
    if (!activeSessionId) {
      activeSessionId = newSessionId();
      setSessionId(activeSessionId);
      try {
        localStorage.setItem(`kb_doc_chat_session_${filename}`, activeSessionId);
      } catch {}
      try {
        window.history.replaceState(null, "", `?session=${encodeURIComponent(activeSessionId)}`);
      } catch {}
    }

    const title = userMsg.length > 40 ? userMsg.slice(0, 40) + "…" : userMsg;
    setSessionTitle(title);

    const userMessage: Message = { role: "user", content: userMsg, mode };
    const assistantMessage: Message = { role: "assistant", content: "", citations: [], mode };
    const updatedMessages: Message[] = [...messages, userMessage, assistantMessage];
    setMessages(updatedMessages);
    setIsStreaming(true);

    const history = updatedMessages.slice(0, -1).map((m) => ({ role: m.role, content: m.content })) as api.ChatMessage[];
    const source = mode === "document" && filename ? decodeURIComponent(filename) : undefined;
    const modelForSend =
      models.find((m) => m.id === selectedModel && m.allowed && m.downloaded !== false)?.id ||
      models.find((m) => m.provider === "ollama" && m.allowed && m.downloaded !== false)?.id ||
      models.find((m) => m.allowed)?.id ||
      selectedModel;
    const selectedModelInfo = models.find((m) => m.id === modelForSend);
    if (selectedModelInfo && !selectedModelInfo.allowed) {
      toast.error("Selected model is not available. Please configure the API key or choose another model.");
      setIsStreaming(false);
      return;
    }
    let accumulated = "";
    let currentCitations: Citation[] = [];
    let finalMessages: Message[] = updatedMessages;

    try {
      for await (const event of api.streamQuery(token, userMsg, mode, history, source, modelForSend, scope)) {
        if (event.type === "token") {
          accumulated += event.token;
          finalMessages = [...updatedMessages.slice(0, -1), { ...assistantMessage, content: accumulated, citations: currentCitations }];
          setMessages(finalMessages);
        } else if (event.type === "citation") {
          currentCitations = [...currentCitations, { page: event.page, chunk_id: event.chunk_id, source: event.source }];
          finalMessages = [...updatedMessages.slice(0, -1), { ...assistantMessage, content: accumulated, citations: currentCitations }];
          setMessages(finalMessages);
        } else if (event.type === "fallback") {
          const friendly = toUserError(event.message || "", event.model || modelForSend);
          setFallback({ reason: event.reason, message: friendly, model: event.model || modelForSend });
          finalMessages = [...updatedMessages.slice(0, -1), { ...assistantMessage, content: friendly, citations: currentCitations }];
          setMessages(finalMessages);
          break;
        }
      }
    } catch (e: any) {
      const friendly = toUserError(e.message || "", modelForSend);
      toast.error(friendly);
      if (e?.message && /quota|insufficient|429|rate limit|too many requests/i.test(e.message)) {
        setFallback({ reason: "quota", message: friendly, model: modelForSend });
      }
      finalMessages = [...updatedMessages.slice(0, -1), { ...assistantMessage, content: "Sorry, I could not process your request.", citations: currentCitations }];
      setMessages(finalMessages);
    } finally {
      setIsStreaming(false);
      if (activeSessionId) {
        await saveSession(activeSessionId, finalMessages, title);
      }
      try {
        window.dispatchEvent(new CustomEvent("credits-updated"));
      } catch {}
    }
  };

  return (
    <div className="flex h-screen w-full overflow-hidden">
      {!isFullScreen && <Sidebar />}
      <div className="flex-1 h-full flex flex-row overflow-hidden">
        <main className={`${isFullScreen ? "w-full" : "w-[45%]"} h-full flex flex-col overflow-hidden p-6`}>
          {isFullScreen && (
            <Button variant="outline" size="sm" onClick={() => setIsFullScreen(false)} className="fixed top-4 right-4 z-50">
              <Minimize2 className="mr-2 h-4 w-4" /> Exit Full-screen
            </Button>
          )}
          <div className="w-full flex-1 min-h-0 flex flex-col gap-6">
            <div className="flex items-center gap-4">
              <Link href="/">
                <Button variant="outline" size="icon">
                  <ChevronLeft className="h-4 w-4" />
                </Button>
              </Link>
              <FileText className="h-8 w-8 text-primary" />
              <h1 className="text-2xl font-bold truncate">{filename}</h1>
              {!isFullScreen && (
                <Button variant="outline" size="sm" onClick={() => setIsFullScreen(true)} className="ml-auto">
                  <Maximize2 className="mr-2 h-4 w-4" /> Full-screen
                </Button>
              )}
            </div>
            <div className="flex gap-2 flex-wrap">
              <Button variant={activeTab === "original" ? "default" : "outline"} onClick={() => setActiveTab("original")}>
                <ImageIcon className="mr-2 h-4 w-4" /> Original
              </Button>
              <Button variant={activeTab === "content" ? "default" : "outline"} onClick={() => setActiveTab("content")}>
                <FileText className="mr-2 h-4 w-4" /> Content
              </Button>
              <Button variant={activeTab === "chunks" ? "default" : "outline"} onClick={() => setActiveTab("chunks")}>
                <Boxes className="mr-2 h-4 w-4" /> Chunks ({chunks.length})
              </Button>
            </div>


            <div className="flex-1 w-full min-h-0 pr-2 flex flex-col overflow-hidden overflow-y-auto">
            {activeTab === "original" ? (
              <div className="relative w-full h-full flex-1 min-h-0 overflow-hidden">
                {pdfLoading ? (
                  <p className="text-muted-foreground">Loading PDF...</p>
                ) : pdfUrl && rawType === "application/pdf" ? (
                  <iframe
                    key={`${pdfUrl}-page-${activePage}`}
                    ref={iframeRef}
                    src={`${pdfUrl}#page=${activePage}&zoom=page-width`}
                    className="w-full h-full border-0"
                    title={filename}
                  />
                ) : rawUrl && rawType.startsWith("image/") ? (
                  <img src={rawUrl} alt={filename} className="max-w-full rounded border" />
                ) : rawUrl && (rawType.startsWith("text/") || rawType === "application/json") ? (
                  <pre className="whitespace-pre-wrap text-sm bg-muted p-4 rounded-lg">{rawText}</pre>
                ) : rawUrl ? (
                  <div className="p-4 border rounded bg-muted">
                    <p className="text-sm mb-2">This file type cannot be previewed directly.</p>
                    <a href={rawUrl} download={filename} className="text-primary underline text-sm">Download {filename}</a>
                  </div>
                ) : (
                  <p className="text-muted-foreground">Original document unavailable.</p>
                )}
              </div>
            ) : activeTab === "content" ? (
              loading ? (
                <p className="text-muted-foreground">Loading content...</p>
              ) : content ? (
                <div className="w-full flex-1 min-h-0 overflow-y-auto space-y-4">
                  {content.type && (
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-muted-foreground">Type:</span>
                      <Badge variant="secondary">{content.type}</Badge>
                    </div>
                  )}
                  {content.pages && content.pages.length > 0 ? (
                    content.pages.map((page: any) => (
                      <Card key={page.page}>
                        <CardHeader>
                          <CardTitle className="text-sm">Page {page.page}</CardTitle>
                        </CardHeader>
                        <CardContent>
                          <div className="whitespace-pre-wrap text-sm leading-relaxed">
                            {page.text}
                          </div>
                        </CardContent>
                      </Card>
                    ))
                  ) : (
                    <Card>
                      <CardHeader>
                        <CardTitle>Content</CardTitle>
                      </CardHeader>
                      <CardContent>
                        <div className="whitespace-pre-wrap text-sm leading-relaxed bg-muted p-4 rounded-lg">
                          {content.content}
                        </div>
                      </CardContent>
                    </Card>
                  )}
                </div>
              ) : (
                <p className="text-muted-foreground">No content available.</p>
              )
            ) : (
              <div className="w-full flex-1 min-h-0 overflow-y-auto space-y-3">
                {loading ? (
                  <p className="text-muted-foreground">Loading chunks...</p>
                ) : chunks.length === 0 ? (
                  <p className="text-muted-foreground">No indexed chunks found.</p>
                ) : (
                  chunks.map((chunk: any, i: number) => (
                    <Card key={chunk.chunk_id || i}>
                      <CardHeader className="pb-2">
                        <div className="flex items-center gap-2">
                          <CardTitle className="text-sm">Chunk {i + 1}</CardTitle>
                          {chunk.page && <Badge variant="outline">Page {chunk.page}</Badge>}
                        </div>
                      </CardHeader>
                      <CardContent>
                        <p className="text-sm text-muted-foreground mb-2">{chunk.chunk_id}</p>
                        <p className="text-sm whitespace-pre-wrap leading-relaxed">{chunk.text}</p>
                      </CardContent>
                    </Card>
                  ))
                )}
              </div>
            )}
            </div>
          </div>
        </main>

        <div className={`bg-background p-4 w-[55%] h-full flex flex-col overflow-hidden ${isFullScreen ? "hidden" : ""}`}>
          <div className="w-full flex flex-col flex-1 min-h-0 overflow-hidden">
            <div className="flex items-center gap-2 flex-wrap">
              <TooltipProvider>
                <DropdownMenu>
                  <Tooltip>
                    <DropdownMenuTrigger asChild>
                      <TooltipTrigger asChild>
                        <Button
                          variant="outline"
                          size="sm"
                          className="gap-2"
                          disabled={isStreaming || isLoadingModels || modelsError || models.length === 0}
                        >
                          <Bot className="h-4 w-4" />
                          <span className="truncate max-w-[160px]">
                            {modelsError
                              ? "Backend Offline"
                              : isLoadingModels || !hasMounted
                                ? "Connecting..."
                                : selectedModel === "no_llm"
                                  ? "No LLM / Pure Direct Search"
                                  : (models.find((m) => m.id === selectedModel)?.id ?? selectedModel)}
                          </span>
                          {(() => {
                            const selected = hasMounted && selectedModel ? models.find((m) => m.id === selectedModel) : undefined;
                            const undownloaded = selected && selected.provider === "ollama" && selected.downloaded === false;
                            return (
                              <>
                                {selected && !selected.allowed && <Lock className="h-3.5 w-3.5 text-muted-foreground" />}
                                {undownloaded && <Badge variant="outline" className="text-[10px] text-amber-600 border-amber-600">Not Downloaded</Badge>}
                                {selected?.id === "no_llm" && <Badge variant="secondary" className="text-[10px]">SEARCH</Badge>}
                                {selected?.tier === "free" && selected?.id !== "no_llm" && !undownloaded && <Badge variant="success" className="text-[10px]">FREE</Badge>}
                                {selected && selected.tier !== "free" && <Badge variant="destructive" className="text-[10px]">PRO</Badge>}
                              </>
                            );
                          })()}
                          <ChevronDown className="h-4 w-4" />
                        </Button>
                      </TooltipTrigger>
                    </DropdownMenuTrigger>
                    {selectedModel === "no_llm" && (
                      <TooltipContent className="max-w-[240px] whitespace-normal break-words">
                        <p>Pure Search Mode: Exact document matches. Ultra-fast, 100% accurate, zero AI cost.</p>
                      </TooltipContent>
                    )}
                  </Tooltip>
                  <DropdownMenuContent align="start" className="w-64 max-h-80 overflow-y-auto">
                    {isLoadingModels && (
                      <DropdownMenuItem disabled className="text-muted-foreground">
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading models...
                      </DropdownMenuItem>
                    )}
                    {!isLoadingModels && modelsError && (
                      <DropdownMenuItem disabled className="text-muted-foreground">
                        Backend offline
                      </DropdownMenuItem>
                    )}
                    {!isLoadingModels && !modelsError && models.length === 0 && (
                      <DropdownMenuItem disabled className="text-muted-foreground">
                        No models available
                      </DropdownMenuItem>
                    )}
                    {!isLoadingModels && !modelsError && models.map((m) =>
                      m.id === "no_llm" ? (
                        <Tooltip key={m.id}>
                          <TooltipTrigger asChild>
                            <DropdownMenuItem
                              onSelect={() => setSelectedModel(m.id)}
                              className={`flex flex-col items-start gap-1 py-2 ${m.allowed ? "" : "text-muted-foreground"}`}
                            >
                              <div className="flex items-center gap-2 w-full">
                                <span className="flex-1 truncate">No LLM / Pure Direct Search</span>
                                <Badge variant="secondary" className="text-[10px]">SEARCH</Badge>
                                {selectedModel === m.id && <Check className="h-4 w-4 text-primary" />}
                              </div>
                              <span className="text-xs text-muted-foreground">Pure document search — zero AI cost</span>
                            </DropdownMenuItem>
                          </TooltipTrigger>
                          <TooltipContent className="max-w-[240px] whitespace-normal break-words">
                            <p>Pure Search Mode: Exact document matches. Ultra-fast, 100% accurate, zero AI cost.</p>
                          </TooltipContent>
                        </Tooltip>
                      ) : (
                        <DropdownMenuItem
                          key={m.id}
                          onSelect={() => {
                            if (m.provider === "ollama" && m.downloaded === false) {
                              downloadModel(m.id);
                              return;
                            }
                            if (m.tier !== "free") {
                              setUpgradeModel(m);
                              setUpgradeOpen(true);
                            } else {
                              setSelectedModel(m.id);
                            }
                          }}
                          className={`flex flex-col items-start gap-1 py-2 ${m.allowed && !(m.provider === "ollama" && m.downloaded === false) ? "" : "text-muted-foreground"}`}
                          title={m.provider === "ollama" && m.downloaded === false ? "Click to download this local model" : m.tier !== "free" ? "Cloud LLM — requires paid credits or your own API key (BYOK)" : undefined}
                        >
                          <div className="flex items-center gap-2 w-full">
                            <span className="flex-1 truncate">{m.id}</span>
                            {m.provider === "ollama" && m.downloaded === false && (
                              downloadingModels.has(m.id)
                                ? <Loader2 className="mr-1 h-3 w-3 animate-spin text-amber-600" />
                                : <Badge variant="outline" className="text-[10px] text-amber-600 border-amber-600">Not Downloaded</Badge>
                            )}
                            {m.tier === "free" && !(m.provider === "ollama" && m.downloaded === false) && <Badge variant="success" className="text-[10px]">FREE</Badge>}
                            {m.tier !== "free" && <Badge variant="destructive" className="text-[10px]">PRO</Badge>}
                            {!m.allowed && <Lock className="h-3.5 w-3.5 text-muted-foreground" />}
                            {selectedModel === m.id && <Check className="h-4 w-4 text-primary" />}
                          </div>
                          <span className="text-xs text-muted-foreground">
                            {m.provider === "ollama" && m.downloaded === false
                              ? (downloadingModels.has(m.id) ? "Downloading..." : "Click to download this local model")
                              : m.tier === "free"
                                ? "Local model — free to use"
                                : `Cloud LLM — in $${m.cost_input_1k}/1K · out $${m.cost_output_1k}/1K`}
                          </span>
                        </DropdownMenuItem>
                      )
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
              </TooltipProvider>
              <div className="flex border rounded-md overflow-hidden">
                <button
                  type="button"
                  className={`px-3 py-1.5 text-xs ${scope === "single" ? "bg-primary text-white" : "bg-background"}`}
                  onClick={() => setScope("single")}
                >
                  This Document
                </button>
                <button
                  type="button"
                  className={`px-3 py-1.5 text-xs ${scope === "knowledge_base" ? "bg-primary text-white" : "bg-background"}`}
                  onClick={() => setScope("knowledge_base")}
                >
                  Entire KB
                </button>
              </div>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto space-y-3 pr-2">
              {messages.map((msg, i) => (
                <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[80%] rounded-xl px-4 py-2 text-sm ${msg.role === "user" ? "bg-primary text-white" : "bg-muted border"}`}>
                    <div className="flex items-center gap-2 mb-1">
                      {msg.role === "assistant" ? <Bot className="h-3 w-3" /> : <User className="h-3 w-3" />}
                      <span className="text-xs font-medium capitalize">{msg.role}</span>
                    </div>
                    {msg.role === "assistant" ? (
                      <AssistantContent content={msg.content} onJumpPage={jumpToPage} filename={filename} />
                    ) : (
                      <div className="whitespace-pre-wrap">{msg.content}</div>
                    )}
                  </div>
                </div>
              ))}
              {fallback && (
                <div className="flex justify-start">
                  <Card className="max-w-[80%]">
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">Model unavailable</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <p className="text-sm text-muted-foreground">{fallback.message}</p>
                      <div className="flex flex-col sm:flex-row gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            const free = models.find((m) => m.allowed && m.tier === "free" && m.id !== "no_llm" && m.downloaded !== false);
                            if (free) {
                              setSelectedModel(free.id);
                              setFallback(null);
                              toast.success(`Switched to ${free.id}`);
                            } else {
                              toast.error("No free model available");
                            }
                          }}
                        >
                          Switch to Free Model
                        </Button>
                        <Button
                          size="sm"
                          onClick={() => {
                            setFallback(null);
                            toast.info("Add an API key in Settings/Billing to use this model");
                          }}
                        >
                          Upgrade / Update API Key
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                </div>
              )}
              <div ref={chatBottomRef} />
            </div>
            <div className="flex gap-2 mb-2 flex-wrap">
              <Button
                size="sm"
                variant={mode === "document" ? "default" : "outline"}
                onClick={() => {
                  setMode("document");
                }}
                className="h-8 px-2 text-xs"
              >
                From This Document
              </Button>
              <Button
                size="sm"
                variant={mode === "assistant" ? "default" : "outline"}
                onClick={() => {
                  setMode("assistant");
                  const gemma = models.find((m) => m.id === "ollama-gemma2" && m.allowed && m.downloaded !== false);
                  if (gemma) setAssistantModel(gemma.id);
                }}
                className="h-8 px-2 text-xs"
              >
                Ask AI Freely
              </Button>
            </div>
            <div className="flex gap-2 flex-wrap mb-2">
              {["Summarize", "Key findings", "Main concepts"].map((q) => (
                <Button
                  key={q}
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setQuery(q);
                  }}
                  disabled={isStreaming}
                >
                  {q}
                </Button>
              ))}
            </div>
            <div className="flex gap-2">
              <Textarea
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                placeholder={`Ask about ${filename}...`}
                className="min-h-[60px] flex-1 resize-none"
                disabled={isStreaming}
              />
              <Button onClick={handleSend} disabled={isStreaming || !query.trim()} className="self-end">
                {isStreaming ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              </Button>
            </div>
          </div>
        </div>
      </div>

      <Dialog open={upgradeOpen} onOpenChange={setUpgradeOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Unlock Pro Models</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              {upgradeModel?.id ?? "This model"} is a cloud LLM. Choose an option to unlock it.
            </p>

            {models.find((m) => m.id === upgradeModel?.id)?.allowed && (
              <Button
                className="w-full"
                onClick={() => {
                  if (upgradeModel) {
                    setSelectedModel(upgradeModel.id);
                  }
                  setUpgradeOpen(false);
                }}
              >
                Use this model with current credits / BYOK
              </Button>
            )}

            <div className="space-y-2">
              <h4 className="text-sm font-medium">Option A: Buy Credits</h4>
              <div className="flex gap-2">
                {[5, 10, 20].map((amount) => (
                  <Button
                    key={amount}
                    variant="outline"
                    onClick={async () => {
                      if (!token) {
                        toast.error("Please sign in first");
                        return;
                      }
                      try {
                        const result = await api.topUpCredits(token, amount);
                        if (result.session_url) {
                          if (upgradeModel) {
                            try { sessionStorage.setItem("pending_upgrade_model", upgradeModel.id); } catch {}
                          }
                          window.location.href = result.session_url;
                          return;
                        }
                        toast.error(result.error || "Payment provider is not configured.");
                      } catch (e: any) {
                        toast.error(e?.message || String(e) || "Top-up failed");
                      }
                    }}
                  >
                    ${amount}
                  </Button>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <h4 className="text-sm font-medium">Option B: Bring Your Own Key (BYOK)</h4>
              <select
                className="w-full text-sm border rounded px-2 py-1.5 bg-background"
                value={byokProvider}
                onChange={(e) => setByokProvider(e.target.value)}
              >
                <option value="google">Gemini (google)</option>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="xai">xAI / Grok</option>
                <option value="deepseek">DeepSeek</option>
              </select>
              <Input
                type="password"
                placeholder="Paste your API key"
                value={byokKey}
                onChange={(e) => setByokKey(e.target.value)}
              />
              <Button
                className="w-full"
                disabled={isSubmittingByok || byokKey.length < 10}
                onClick={async () => {
                  if (!token) {
                    toast.error("Please sign in first");
                    return;
                  }
                  setIsSubmittingByok(true);
                  try {
                    await api.setApiKey(token, byokProvider, byokKey);
                    const refreshed = await api.getModels(token);
                    const sorted = [...refreshed].sort((a, b) => {
                      if (a.id === "no_llm") return -1;
                      if (b.id === "no_llm") return 1;
                      return 0;
                    });
                    setModels(sorted);
                    if (upgradeModel) {
                      setSelectedModel(upgradeModel.id);
                    }
                    setUpgradeOpen(false);
                    toast.success("API key saved — this provider is now free for you");
                  } catch (e: any) {
                    toast.error(e?.message || String(e) || "Could not save API key");
                  } finally {
                    setIsSubmittingByok(false);
                  }
                }}
              >
                {isSubmittingByok ? "Saving..." : "Save API Key"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
