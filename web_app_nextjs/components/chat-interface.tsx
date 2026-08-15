"use client";

import { useCallback, useEffect, useRef, useState, memo } from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
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
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { LoginDialog } from "@/components/login-dialog";
import { MarkdownContent } from "@/components/markdown-content";
import { ChatInput } from "@/components/chat-input";
import * as api from "@/lib/api";
import { APP_CONFIG, loadSetting, saveSetting } from "@/lib/config";
import { ChatSession, Message, Citation, loadSessions, saveSessions, createSession, autoTitle } from "@/lib/sessions";
import { toast } from "sonner";
import { Info } from "lucide-react";
import {
  MoreVertical,
  Pencil,
  Trash2,
  ThumbsUp,
  ThumbsDown,
  Send,
  FileText,
  Bot,
  Loader2,
  ChevronDown,
  Check,
  Lock,
} from "lucide-react";

const isDownloaded = (m?: api.ModelInfo) =>
  !m || m.provider !== "ollama" || m.downloaded !== false;

const isAvailable = (m?: api.ModelInfo) =>
  !!m && m.allowed && isDownloaded(m);

const isOllama = (m?: api.ModelInfo) =>
  !!m && m.provider === "ollama" && m.allowed && m.downloaded !== false;

const GUARD_KEYWORDS = ["write", "make", "create", "summarize", "explain", "how to", "code", "calculator"];
const shouldGuardNoLLM = (text: string) =>
  GUARD_KEYWORDS.some((kw) => text.toLowerCase().includes(kw));

const SearchSnippets = memo(function SearchSnippets({
  content,
  citations,
}: {
  content: string;
  citations?: Citation[];
}) {
  const matches = Array.from(content.matchAll(/Passage (\d+):\n([\s\S]*?)(?=\n\nPassage \d+:\n|$)/g));
  if (matches.length === 0) return <ChatMessageContent content={content} citations={citations} />;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
        <Info className="h-4 w-4" />
        <span>Showing raw document matches. No AI synthesis was applied.</span>
      </div>
      {matches.map((match, i) => {
        const text = match[2].trim();
        const citation = citations?.[i] || citations?.[0];
        return (
          <div key={i} className="rounded-lg border border-border bg-card p-3 shadow-sm">
            <div className="flex items-center gap-2 mb-2">
              <FileText className="h-4 w-4 text-muted-foreground" />
              {citation ? (
                <a
                  href={citation.file_url || `/documents/${encodeURIComponent(citation.source)}#page=${citation.page}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs font-medium text-primary hover:underline"
                >
                  {citation.file_name || citation.source}, Page {citation.page}
                </a>
              ) : (
                <span className="text-xs text-muted-foreground">Document match</span>
              )}
            </div>
            <div className="prose prose-sm max-w-none whitespace-pre-wrap text-sm text-foreground">{text}</div>
          </div>
        );
      })}
    </div>
  );
});

const ChatMessageContent = memo(function ChatMessageContent({
  content,
  citations,
}: {
  content: string;
  citations?: Citation[];
}) {
  const parts = content.split(/(\[Source: [^\]]+, Page \d+\]|\[Page \d+\])/g);
  const pageLinks = new Map<number, string>();
  for (const c of citations || []) {
    if (!pageLinks.has(c.page)) pageLinks.set(c.page, c.file_url || `/documents/${encodeURIComponent(c.source)}#page=${c.page}`);
  }
  return (
    <div className="prose prose-sm max-w-none whitespace-pre-wrap">
      {parts.map((part, i) => {
        const sourceMatch = part.match(/^\[Source: ([^\]]+), Page (\d+)\]$/);
        if (sourceMatch) {
          const source = sourceMatch[1].trim();
          const page = parseInt(sourceMatch[2], 10);
          const href = `/documents/${encodeURIComponent(source)}#page=${page}`;
          return (
            <a
              key={i}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center px-1.5 py-0.5 rounded bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20 mx-0.5"
            >
              [Source: {source}, Page {page}]
            </a>
          );
        }
        const pageMatch = part.match(/^\[Page (\d+)\]$/);
        if (pageMatch) {
          const page = parseInt(pageMatch[1], 10);
          const href = pageLinks.get(page) || `#`;
          return (
            <a
              key={i}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center px-1.5 py-0.5 rounded bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20 mx-0.5"
            >
              [Page {page}]
            </a>
          );
        }
        return <MarkdownContent key={i} content={part} />;
      })}
    </div>
  );
});

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

export function ChatInterface() {
  const { token, user, isAuthenticated } = useAuth();
  const searchParams = useSearchParams();
  const sessionId = searchParams.get("session");
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [session, setSession] = useState<ChatSession | null>(null);
  const [inputValue, setInputValue] = useState("");
  const [mode, setMode] = useState<"document" | "assistant">(
    (loadSetting(APP_CONFIG.storage.chatMode, "document") as "document" | "assistant")
  );
  const [scope, setScope] = useState<"single" | "knowledge_base">(
    (loadSetting(APP_CONFIG.storage.ragScope, "knowledge_base") as "single" | "knowledge_base")
  );
  const [isStreaming, setIsStreaming] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [feedbackOpen, setFeedbackOpen] = useState<string | null>(null);
  const [feedbackComment, setFeedbackComment] = useState("");
  const [mounted, setMounted] = useState(false);
  const [models, setModels] = useState<api.ModelInfo[]>([]);
  const [modelsError, setModelsError] = useState(false);
  const [selectedModel, setSelectedModel] = useState<string | null>(APP_CONFIG.defaultModel);
  const [upgradeOpen, setUpgradeOpen] = useState(false);
  const [upgradeModel, setUpgradeModel] = useState<api.ModelInfo | null>(null);
  const [byokKey, setByokKey] = useState("");
  const [byokProvider, setByokProvider] = useState("google");
  const [isSubmittingByok, setIsSubmittingByok] = useState(false);
  const [fallback, setFallback] = useState<{ reason: string; message: string; model: string } | null>(null);
  const [downloadingModels, setDownloadingModels] = useState<Set<string>>(new Set());
  const [guardOpen, setGuardOpen] = useState(false);
  const [guardText, setGuardText] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setByokProvider(upgradeModel?.provider ?? "google");
    setByokKey("");
  }, [upgradeModel]);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    const loaded = loadSessions();
    setSessions(loaded);
    if (sessionId) {
      const found = loaded.find((s) => s.id === sessionId);
      if (found) {
        setSession(found);
      } else {
        const created = createSession();
        const updated = [created, ...loaded];
        saveSessions(updated);
        setSessions(updated);
        setSession(created);
        window.history.replaceState(null, "", `/?session=${created.id}`);
      }
    } else if (loaded.length > 0) {
      setSession(loaded[0]);
      window.history.replaceState(null, "", `/?session=${loaded[0].id}`);
    } else {
      const created = createSession();
      saveSessions([created]);
      setSessions([created]);
      setSession(created);
      window.history.replaceState(null, "", `/?session=${created.id}`);
    }
  }, [sessionId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [session?.messages, isStreaming]);

  const [isLoadingModels, setIsLoadingModels] = useState(false);

  const loadModels = useCallback(async () => {
    if (!token) return;
    setIsLoadingModels(true);
    setModelsError(false);
    try {
      const loaded = await api.getModels(token);
      const sorted = [...loaded].sort((a, b) => {
        if (a.id === "no_llm") return 1;
        if (b.id === "no_llm") return -1;
        if (isOllama(a) && !isOllama(b)) return -1;
        if (!isOllama(a) && isOllama(b)) return 1;
        if (a.allowed && !b.allowed) return -1;
        if (!a.allowed && b.allowed) return 1;
        return 0;
      });
      setModels(sorted);

      let pending: string | null = null;
      try {
        pending = sessionStorage.getItem("pending_upgrade_model");
      } catch {}

      const savedModel = loadSetting(APP_CONFIG.storage.selectedModel, null);
      const savedAvailable = savedModel ? isAvailable(sorted.find((m) => m.id === savedModel)) : false;
      if (!savedAvailable) {
        saveSetting(APP_CONFIG.storage.selectedModel, null);
      }

      setSelectedModel(() => {
        let next: string | null = null;
        if (pending && isAvailable(sorted.find((m) => m.id === pending))) {
          next = pending;
        } else if (savedAvailable && savedModel) {
          // Restore a saved model only if it is still available (has a valid API key or is a local Ollama model).
          next = savedModel;
        }
        const ollamaModels = sorted.filter((m) => isOllama(m));
        const preferred = ollamaModels.find((m) => m.id === APP_CONFIG.defaultModel) || ollamaModels[0];
        const fallback = sorted.find((m) => m.id === "no_llm" && isAvailable(m));
        return next ?? preferred?.id ?? fallback?.id ?? null;
      });

      if (pending) {
        try {
          sessionStorage.removeItem("pending_upgrade_model");
        } catch {}
      }
    } catch (err: any) {
      setModelsError(true);
      toast.error(err.message || "Could not load model list");
    } finally {
      setIsLoadingModels(false);
    }
  }, [token]);

  useEffect(() => {
    // Skip the initial model refresh when returning from a Stripe redirect;
    // the topup handler will load models after payment verification and then
    // restore the originally selected paid model.
    if (searchParams.get("topup") === "success") return;
    loadModels();
  }, [loadModels, searchParams]);

  useEffect(() => {
    saveSetting(APP_CONFIG.storage.selectedModel, selectedModel);
  }, [selectedModel]);

  useEffect(() => {
    saveSetting(APP_CONFIG.storage.chatMode, mode);
  }, [mode]);

  useEffect(() => {
    saveSetting(APP_CONFIG.storage.ragScope, scope);
  }, [scope]);

  useEffect(() => {
    // Handle Stripe Checkout redirect after credit top-up.
    const topup = searchParams.get("topup");
    const checkoutSessionId = searchParams.get("session_id");
    if (topup === "success" && checkoutSessionId && token) {
      api.verifyTopUp(token, checkoutSessionId)
        .then(async (result) => {
          if (result.already_applied) {
            toast.info("Credits were already applied for this session.");
          } else {
            toast.success(`Added $${result.amount?.toFixed(2) ?? ""} credits`);
          }
          // Let the sidebar re-fetch credits immediately.
          try {
            window.dispatchEvent(new CustomEvent("credits-updated"));
          } catch {}
          await loadModels();
          const pendingModel = (() => {
            try { return sessionStorage.getItem("pending_upgrade_model"); } catch { return null; }
          })();
          if (pendingModel) {
            setSelectedModel(pendingModel);
            try { sessionStorage.removeItem("pending_upgrade_model"); } catch {}
          }
          window.history.replaceState(null, "", window.location.pathname);
        })
        .catch((err: any) => {
          toast.error(err?.message || "Could not verify payment");
        });
    } else if (topup === "cancel") {
      toast.info("Payment cancelled.");
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, [searchParams, token, loadModels]);

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
            if (m && isAvailable(m)) {
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
  }, [downloadingModels, token]);

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

  const updateSession = (updatedSession: ChatSession) => {
    const updated = sessions.map((s) => (s.id === updatedSession.id ? updatedSession : s));
    saveSessions(updated);
    setSessions(updated);
    setSession(updatedSession);
  };

  const executeSend = async (modelToUse: string, textToUse: string, retried = false) => {
    if (!token || !user) {
      toast.error("Please sign in to chat");
      return;
    }
    if (!textToUse.trim() || !session) return;
    if (!modelToUse) {
      toast.error("Please select a model first");
      return;
    }

    const userMessage: Message = {
      id: `msg_${Date.now()}_user`,
      role: "user",
      content: textToUse.trim(),
      mode,
    };
    const assistantMessage: Message = {
      id: `msg_${Date.now()}_assistant`,
      role: "assistant",
      content: "",
      mode,
      model: modelToUse,
    };

    const updatedMessages = [...session.messages, userMessage, assistantMessage];
    let updatedSession: ChatSession = {
      ...session,
      messages: updatedMessages,
      updatedAt: Date.now(),
      title: session.title === "New Chat" ? autoTitle(textToUse) : session.title,
    };
    updateSession(updatedSession);
    setInputValue("");
    setIsStreaming(true);

    try {
      const history = session.messages.map((m) => ({ role: m.role, content: m.content })) as api.ChatMessage[];

      if (mode === "assistant") {
        const selectedModelInfo = models.find((m) => m.id === modelToUse);
        if (!selectedModelInfo) {
          toast.error("Selected model is not available");
          return;
        }
        if (!isDownloaded(selectedModelInfo)) {
          const ollamaName = selectedModelInfo.id.startsWith("ollama-") ? selectedModelInfo.id.slice(7) : selectedModelInfo.id;
          toast.error(`Model ${selectedModelInfo.id} is not downloaded. Run: ollama pull ${ollamaName}`);
          return;
        }
        if (!selectedModelInfo?.allowed) {
          setUpgradeModel(selectedModelInfo ?? null);
          setUpgradeOpen(true);
          return;
        }
        const messages = [...history, { role: "user" as const, content: textToUse.trim() }];
        let accumulated = "";
        for await (const event of api.chat(token, modelToUse, messages, mode)) {
          if (event.type === "token" && typeof event.token === "string") {
            accumulated += event.token;
            updatedSession = {
              ...updatedSession,
              messages: updatedSession.messages.map((m) =>
                m.id === assistantMessage.id ? { ...m, content: accumulated } : m
              ),
            };
            updateSession(updatedSession);
          }
        }
      } else {
        const selectedModelInfo = models.find((m) => m.id === modelToUse);
        if (!selectedModelInfo) {
          toast.error("Selected model is not available");
          return;
        }
        if (!isDownloaded(selectedModelInfo)) {
          const ollamaName = selectedModelInfo.id.startsWith("ollama-") ? selectedModelInfo.id.slice(7) : selectedModelInfo.id;
          toast.error(`Model ${selectedModelInfo.id} is not downloaded. Run: ollama pull ${ollamaName}`);
          return;
        }
        if (!selectedModelInfo?.allowed) {
          setUpgradeModel(selectedModelInfo ?? null);
          setUpgradeOpen(true);
          return;
        }
        let accumulated = "";
        let currentCitations: Citation[] = [];
        for await (const event of api.streamQuery(token, textToUse.trim(), mode, history, undefined, modelToUse, scope)) {
          if (event.type === "token") {
            accumulated += event.token;
          } else if (event.type === "citation") {
            currentCitations = [
              ...currentCitations,
              {
                page: event.page,
                chunk_id: event.chunk_id,
                source: event.source,
                document_id: event.document_id,
                file_name: event.file_name,
                page_number: event.page_number,
                file_url: event.file_url,
              },
            ];
          } else if (event.type === "fallback") {
            const friendly = toUserError(event.message || "", event.model || modelToUse);
            setFallback({ reason: event.reason, message: friendly, model: event.model });
            accumulated = friendly;
            updatedSession = {
              ...updatedSession,
              messages: updatedSession.messages.map((m) =>
                m.id === assistantMessage.id ? { ...m, content: accumulated, citations: currentCitations } : m
              ),
            };
            updateSession(updatedSession);
            break;
          } else {
            continue;
          }
          updatedSession = {
            ...updatedSession,
            messages: updatedSession.messages.map((m) =>
              m.id === assistantMessage.id ? { ...m, content: accumulated, citations: currentCitations } : m
            ),
          };
          updateSession(updatedSession);
        }
      }
    } catch (e: any) {
      const raw = String(e?.message || e || "Request failed");
      const isAbort = e?.name === "AbortError";
      const friendly = isAbort
        ? `The selected model (${modelToUse}) did not respond within 60 seconds. It may still be loading. Try again.`
        : toUserError(raw, modelToUse || "selected model");
      toast.error(raw);
      setFallback({ reason: isAbort ? "timeout" : "unavailable", message: friendly, model: modelToUse || "" });
      updatedSession = {
        ...updatedSession,
        messages: updatedSession.messages.map((m) =>
          m.id === assistantMessage.id ? { ...m, content: friendly } : m
        ),
      };
      updateSession(updatedSession);

      if (!retried) {
        const currentInfo = models.find((m) => m.id === modelToUse);
        const fallback = models.find((m) => m.id === APP_CONFIG.defaultModel && isOllama(m) && isAvailable(m))
          || models.find((m) => isOllama(m) && isAvailable(m));
        if (fallback && currentInfo && !isOllama(currentInfo) && fallback.id !== modelToUse) {
          toast.info(`Falling back to ${fallback.id}...`);
          setSelectedModel(fallback.id);
          // Revert the failed user/assistant messages so the retry is clean.
          const reverted = { ...updatedSession, messages: session.messages };
          updateSession(reverted);
          await executeSend(fallback.id, textToUse.trim(), true);
          return;
        }
      }
    } finally {
      setIsStreaming(false);
      try {
        window.dispatchEvent(new CustomEvent("credits-updated"));
      } catch {}
    }
  };

  const handleSend = (text: string) => {
    if (!token || !user) {
      toast.error("Please sign in to chat");
      return;
    }
    const trimmed = text.trim();
    if (!trimmed || !session) return;
    if (!selectedModel) {
      toast.error("Please select a model first");
      return;
    }
    setInputValue("");
    if (selectedModel === "no_llm" && shouldGuardNoLLM(trimmed)) {
      setGuardText(trimmed);
      setGuardOpen(true);
      return;
    }
    executeSend(selectedModel, trimmed);
  };

  const handleDeleteMessage = (id: string) => {
    if (!session) return;
    const updated = { ...session, messages: session.messages.filter((m) => m.id !== id) };
    updateSession(updated);
  };

  const handleEditMessage = (msg: Message) => {
    if (!session) return;
    const updated = {
      ...session,
      messages: session.messages.map((m) => (m.id === msg.id ? { ...m, content: editValue } : m)),
    };
    updateSession(updated);
    setEditingId(null);
  };

  const handleFeedback = async (msg: Message, rating: "up" | "down") => {
    if (!token || !session) return;
    const userMsg = session.messages.find((m) => m.id === `msg_${msg.id.split("_")[1]}_user`);
    try {
      await api.submitFeedback(token, {
        query: userMsg?.content || "",
        response: msg.content,
        mode: msg.mode || "document",
        rating,
        comment: rating === "down" ? feedbackComment : undefined,
        session_id: session.id,
        message_id: msg.id,
      });
      const updated = {
        ...session,
        messages: session.messages.map((m) => (m.id === msg.id ? { ...m, feedback_rating: rating } : m)),
      };
      updateSession(updated);
      setFeedbackOpen(null);
      setFeedbackComment("");
      toast.success("Feedback submitted");
    } catch (e: any) {
      toast.error(e.message || "Failed to submit feedback");
    }
  };

  const exampleQueries = [
    "What technologies are used in this pilot stack?",
    "When was the project kickoff date?",
    "What is the purpose of this knowledge base?",
    "How does the RAG system work?",
  ];

  if (!mounted) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground">
        <Loader2 className="h-8 w-8 animate-spin mr-2" /> Loading chat...
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="text-center space-y-4 max-w-md">
          <h2 className="text-2xl font-bold">Welcome to AI Knowledge Base</h2>
          <p className="text-muted-foreground">Sign in to chat with your documents and manage your knowledge base.</p>
          <LoginDialog>
            <Button size="lg">Sign In to Get Started</Button>
          </LoginDialog>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col h-screen overflow-hidden bg-background">
      <div className="flex-1 overflow-y-auto p-4 md:p-8">
        <div className="max-w-3xl mx-auto space-y-6">
          {session?.title && session.title !== "New Chat" && (
            <h2 className="text-lg font-semibold text-muted-foreground">{session.title}</h2>
          )}

          {session?.messages.length === 0 && (
            <div className="text-center py-12">
              <h1 className="text-3xl font-bold mb-2">What can I help you find?</h1>
              <p className="text-muted-foreground mb-8">Ask a question about your documents or use general knowledge.</p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {exampleQueries.map((q) => (
                  <Button
                    key={q}
                    variant="outline"
                    className="justify-start h-auto py-3 px-4"
                    onClick={() => setInputValue(q)}
                  >
                    {q}
                  </Button>
                ))}
              </div>
            </div>
          )}

          {session?.messages.map((msg, idx) => (
            <div key={msg.id} className={`group flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[85%] rounded-2xl p-4 ${msg.role === "user" ? "bg-primary text-white" : msg.role === "assistant" && msg.model === "no_llm" ? "bg-muted border border-border" : "bg-card border border-border"}`}>
                <div className="flex items-center gap-2 mb-1">
                  {msg.role === "assistant" ? (
                    msg.model === "no_llm" ? (
                      <><FileText className="h-4 w-4" /><span className="text-xs font-medium opacity-80">Search Results</span></>
                    ) : (
                      <><Bot className="h-4 w-4" /><span className="text-xs font-medium opacity-80">AI Assistant</span></>
                    )
                  ) : (
                    <><FileText className="h-4 w-4" /><span className="text-xs font-medium opacity-80">You</span></>
                  )}
                </div>

                {editingId === msg.id && msg.role === "user" ? (
                  <div className="space-y-2">
                    <Textarea
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      className="min-h-[60px]"
                    />
                    <div className="flex gap-2">
                      <Button size="sm" onClick={() => handleEditMessage(msg)}>Save</Button>
                      <Button size="sm" variant="outline" onClick={() => setEditingId(null)}>Cancel</Button>
                    </div>
                  </div>
                ) : (
                  msg.role === "assistant" && msg.model === "no_llm" ? (
                    <SearchSnippets content={msg.content} citations={msg.citations} />
                  ) : (
                    <ChatMessageContent content={msg.content} citations={msg.citations} />
                  )
                )}

                {msg.citations && msg.citations.length > 0 && msg.model !== "no_llm" && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {msg.citations.map((citation, idx) => (
                      <a
                        key={idx}
                        href={citation.file_url || `/documents/${encodeURIComponent(citation.source)}#page=${citation.page}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center px-2 py-0.5 rounded bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20"
                      >
                        [Page {citation.page}] {citation.file_name || citation.source}
                      </a>
                    ))}
                  </div>
                )}

                {msg.role === "assistant" && !isStreaming && (
                  <div className="mt-3 flex items-center gap-2">
                    <button
                      onClick={() => handleFeedback(msg, "up")}
                      className={`p-1 rounded ${msg.feedback_rating === "up" ? "text-green-600 bg-green-100" : "text-muted-foreground hover:text-foreground"}`}
                    >
                      <ThumbsUp className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => setFeedbackOpen(feedbackOpen === msg.id ? null : msg.id)}
                      className={`p-1 rounded ${msg.feedback_rating === "down" ? "text-red-600 bg-red-100" : "text-muted-foreground hover:text-foreground"}`}
                    >
                      <ThumbsDown className="h-4 w-4" />
                    </button>
                    {feedbackOpen === msg.id && (
                      <div className="flex items-center gap-2">
                        <input
                          type="text"
                          value={feedbackComment}
                          onChange={(e) => setFeedbackComment(e.target.value)}
                          placeholder="What was wrong?"
                          className="text-sm border rounded px-2 py-1 w-40"
                        />
                        <Button size="sm" onClick={() => handleFeedback(msg, "down")}>Submit</Button>
                        <Button size="sm" variant="ghost" onClick={() => setFeedbackOpen(null)}>Cancel</Button>
                      </div>
                    )}
                  </div>
                )}
              </div>

              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="h-8 w-8 opacity-0 group-hover:opacity-100 ml-1">
                    <MoreVertical className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {msg.role === "user" && (
                    <DropdownMenuItem
                      onClick={() => {
                        setEditingId(msg.id);
                        setEditValue(msg.content);
                      }}
                    >
                      <Pencil className="mr-2 h-4 w-4" /> Edit
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem onClick={() => handleDeleteMessage(msg.id)} className="text-red-600">
                    <Trash2 className="mr-2 h-4 w-4" /> Delete
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          ))}
          {fallback && (
            <div className="flex justify-start">
              <div className="max-w-[85%] rounded-2xl p-4 bg-card border border-border space-y-3">
                <div className="flex items-center gap-2">
                  <Bot className="h-4 w-4 text-muted-foreground" />
                  <span className="text-sm font-medium">Model unavailable</span>
                </div>
                <p className="text-sm text-muted-foreground">{fallback.message}</p>
                <div className="flex flex-col sm:flex-row gap-2">
                  {selectedModel?.startsWith("ollama") ? (
                    <Button size="sm" disabled>
                      Ollama Model Unresponsive
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      onClick={() => {
                        const local = models.find((m) => m.provider === "ollama" && m.allowed && m.downloaded !== false);
                        if (local) {
                          setSelectedModel(local.id);
                          setFallback(null);
                          toast.success(`Switched to local Ollama model ${local.id}`);
                        } else {
                          toast.error("No downloaded Ollama model is available. Pull one in the model dropdown.");
                        }
                      }}
                    >
                      Switch to Local Ollama Model
                    </Button>
                  )}
                  <Button
                    size="sm"
                    onClick={() => {
                      const failed = models.find((m) => m.id === fallback.model);
                      setUpgradeModel(failed ?? null);
                      setUpgradeOpen(true);
                      setFallback(null);
                    }}
                  >
                    Upgrade / Update API Key
                  </Button>
                </div>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className="border-t border-border p-4 bg-card">
        <div className="max-w-3xl mx-auto space-y-3">
          <div className="flex items-center justify-between gap-2">
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
                          : isLoadingModels
                            ? "Connecting..."
                            : selectedModel === "no_llm"
                              ? "No LLM / Pure Direct Search"
                              : selectedModel
                                ? (models.find((m) => m.id === selectedModel)?.id ?? selectedModel)
                                : "Select a model"}
                      </span>
                      {(() => {
                        const selected = selectedModel ? models.find((m) => m.id === selectedModel) : undefined;
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
                    <p>🔍 Pure Search Mode: Exact document matches. Ultra-fast, 100% accurate, zero AI cost.</p>
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
                        <p>🔍 Pure Search Mode: Exact document matches. Ultra-fast, 100% accurate, zero AI cost.</p>
                      </TooltipContent>
                    </Tooltip>
                  ) : (
                    <DropdownMenuItem
                      key={m.id}
                      disabled={downloadingModels.has(m.id)}
                      onSelect={() => {
                        if (m.provider === "ollama" && m.downloaded === false && !downloadingModels.has(m.id)) {
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
                      className={`flex flex-col items-start gap-1 py-2 ${m.allowed && !(m.provider === "ollama" && m.downloaded === false) ? "" : "text-muted-foreground"} ${downloadingModels.has(m.id) ? "cursor-not-allowed opacity-60" : ""}`}
                      title={
                        m.provider === "ollama" && m.downloaded === false
                          ? (downloadingModels.has(m.id) ? "Downloading model, please wait..." : "Click to download this local model")
                          : m.tier !== "free"
                            ? "Cloud LLM — requires paid credits or your own API key (BYOK)"
                            : "Select model"
                      }
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
            <span className="text-xs text-muted-foreground">
              {mode === "assistant" ? "Model selected for chat" : "Documents use local RAG"}
            </span>
          </div>

          {selectedModel === "no_llm" && (
            <div className="flex items-center gap-2 text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded px-3 py-2">
              <Info className="h-3.5 w-3.5" />
              <span>Pure Direct Search is active. Responses show raw document passages; no LLM synthesis is used.</span>
            </div>
          )}
          <ChatInput
            value={inputValue}
            onSend={handleSend}
            disabled={isStreaming}
            placeholder="Ask a question..."
          />
          <div className="flex items-center gap-3">
            <div className="flex gap-2">
              <Button
                variant={mode === "document" ? "default" : "outline"}
                size="sm"
                onClick={() => setMode("document")}
              >
                From My Documents
              </Button>
              <Button
                variant={mode === "assistant" ? "default" : "outline"}
                size="sm"
                onClick={() => {
                  setMode("assistant");
                  if (selectedModel === "no_llm") {
                    const gemma = models.find((m) => m.id === "ollama-gemma2" && m.allowed && m.downloaded !== false);
                    if (gemma) setSelectedModel(gemma.id);
                  }
                }}
              >
                Ask AI Freely
              </Button>
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
                            // Remember which model the user wanted to unlock so we can select it after the redirect.
                            if (upgradeModel) {
                              try { sessionStorage.setItem("pending_upgrade_model", upgradeModel.id); } catch {}
                            }
                            // Redirect to Stripe Checkout for real payment.
                            window.location.href = result.session_url;
                            return;
                          }
                          // If the backend does not return a session URL, payment is not configured.
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
                      await loadModels();
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

        <Dialog open={guardOpen} onOpenChange={setGuardOpen}>
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle>AI Model Required for Direct Answers</DialogTitle>
            </DialogHeader>
            <p className="text-sm text-muted-foreground">
              Pure Search Mode only retrieves exact text snippets from your files. To write code, summarize, or generate synthesized answers, please switch to an active AI model.
            </p>
            <div className="flex flex-col gap-2 mt-4">
              <Button
                onClick={() => {
                  const firstLLM = models.find((m) => isAvailable(m) && m.id !== "no_llm");
                  if (firstLLM) {
                    setSelectedModel(firstLLM.id);
                    setGuardOpen(false);
                    executeSend(firstLLM.id, guardText);
                  } else {
                    toast.error("No AI model is available.");
                  }
                }}
              >
                Switch to Active Model & Ask
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  setGuardOpen(false);
                  executeSend("no_llm", guardText);
                }}
              >
                Show Raw Search Results
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}
