"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { LoginDialog } from "@/components/login-dialog";
import * as api from "@/lib/api";
import { ChatSession, loadSessions, saveSessions, createSession, autoTitle } from "@/lib/sessions";
import { toast } from "sonner";
import {
  BookOpen,
  FolderKanban,
  LayoutDashboard,
  Loader2,
  LogIn,
  LogOut,
  MessageSquarePlus,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  Settings,
  Trash2,
  UploadCloud,
  User,
  ChevronRight,
  AlertCircle,
  RefreshCw,
  MoreVertical,
  CreditCard,
} from "lucide-react";

export function Sidebar() {
  const { user, token, logout, isLoading } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionSearch, setSessionSearch] = useState("");
  const [documents, setDocuments] = useState<api.DocumentItem[]>([]);
  const [isLoadingDocs, setIsLoadingDocs] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [selectedFiles, setSelectedFiles] = useState<FileList | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [jobsOpen, setJobsOpen] = useState(false);
  const [jobs, setJobs] = useState<any[]>([]);
  const [mounted, setMounted] = useState(false);
  const [credits, setCredits] = useState<api.CreditBalance | null>(null);
  const notifiedFailures = useRef(new Set<string>());

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    setSessions(loadSessions());
  }, []);

  const refreshCredits = useCallback(() => {
    if (!token) return;
    api.getCredits(token)
      .then(setCredits)
      .catch(() => {});
  }, [token]);

  useEffect(() => {
    refreshCredits();
  }, [token, refreshCredits]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = () => refreshCredits();
    window.addEventListener("credits-updated", handler);
    return () => window.removeEventListener("credits-updated", handler);
  }, [refreshCredits]);

  const loadDocs = useCallback(async (showLoading = false) => {
    if (!token) return;
    if (showLoading) setIsLoadingDocs(true);
    try {
      const res = await api.listDocuments(token);
      for (const document of res.documents) {
        if (document.status === "failed" && !notifiedFailures.current.has(document.filename)) {
          notifiedFailures.current.add(document.filename);
          toast.error(`${document.filename} could not be processed`, {
            description: `${document.error || "Document processing failed."}${document.error_code ? ` (${document.error_code})` : ""}`,
          });
        }
        if (document.status !== "failed") {
          notifiedFailures.current.delete(document.filename);
        }
      }
      setDocuments((prev) => {
        const next = res.documents;
        // Preserve stable reference if the list hasn't actually changed,
        // preventing a re-render/flicker when the sidebar remounts.
        if (JSON.stringify(prev) === JSON.stringify(next)) {
          return prev;
        }
        try {
          sessionStorage.setItem("sidebar_documents", JSON.stringify(next));
        } catch {}
        return next;
      });
    } catch (e: any) {
      toast.error(e.message || "Failed to load documents");
    } finally {
      if (showLoading) setIsLoadingDocs(false);
    }
  }, [token]);

  useEffect(() => {
    // Hydrate cached documents on the client to avoid sidebar flicker on navigation.
    try {
      const cached = sessionStorage.getItem("sidebar_documents");
      if (cached) setDocuments(JSON.parse(cached));
    } catch {}
  }, []);

  useEffect(() => {
    if (!token) return;
    // Refresh in the background without flashing the loading spinner;
    // sessionStorage already provides the previous list on first render.
    loadDocs(false);
  }, [token, loadDocs]);

  useEffect(() => {
    if (!token) return;
    const isProcessing = documents.some((document) =>
      ["pending", "processing"].includes(document.status)
    );
    if (!isProcessing) return;

    const interval = window.setInterval(() => {
      loadDocs();
    }, 4000);
    return () => window.clearInterval(interval);
  }, [token, loadDocs, documents]);

  const filteredSessions = useMemo(() => {
    return sessions.filter((s) => s.title.toLowerCase().includes(sessionSearch.toLowerCase()));
  }, [sessions, sessionSearch]);

  const handleNewChat = () => {
    const session = createSession();
    const updated = [session, ...sessions];
    saveSessions(updated);
    setSessions(updated);
    router.push(`/?session=${session.id}`);
  };

  const handleDeleteSession = (id: string) => {
    const updated = sessions.filter((s) => s.id !== id);
    saveSessions(updated);
    setSessions(updated);
  };

  const handleRenameSession = (id: string) => {
    const updated = sessions.map((s) => (s.id === id ? { ...s, title: renameValue || s.title } : s));
    saveSessions(updated);
    setSessions(updated);
    setRenamingId(null);
  };

  const handleUpload = async () => {
    if (!token || !selectedFiles || selectedFiles.length === 0) return;
    setIsUploading(true);
    try {
      const res = await api.uploadDocuments(token, selectedFiles);
      toast.success(res.message || "Upload successful");
      await loadDocs(true);
      setSelectedFiles(null);
    } catch (e: any) {
      toast.error(e.message || "Upload failed");
    } finally {
      setIsUploading(false);
    }
  };

  const handlePreview = (doc: api.DocumentItem) => {
    router.push(`/documents/${encodeURIComponent(doc.filename)}`);
  };

  const handleReindex = async (id: number) => {
    if (!token) return;
    const doc = documents.find((d) => d.id === id);
    if (!doc) return;
    try {
      notifiedFailures.current.delete(doc.filename);
      await api.reindexDocument(token, id);
      toast.success(`Re-index queued for ${doc.filename}`);
      await loadDocs();
    } catch (e: any) {
      toast.error(e.message || "Re-index failed");
    }
  };

  const handleRetry = async (id: number) => {
    if (!token) return;
    const doc = documents.find((d) => d.id === id);
    if (!doc) return;
    try {
      notifiedFailures.current.delete(doc.filename);
      await api.retryDocument(token, doc.filename);
      toast.success(`Retry queued for ${doc.filename}`);
      await loadDocs();
    } catch (e: any) {
      toast.error(e.message || "Retry failed");
    }
  };

  const handleRename = (doc: api.DocumentItem) => {
    if (!token) return;
    const newName = prompt("Rename to:", doc.filename);
    if (!newName || newName === doc.filename) return;
    api
      .renameDocument(token, doc.filename, newName)
      .then(() => {
        toast.success("Document renamed");
        return loadDocs();
      })
      .catch((e: any) => toast.error(e.message || "Rename failed"));
  };

  const handleDelete = async (id: number) => {
    if (!token) return;
    const doc = documents.find((d) => d.id === id);
    if (!doc) return;
    if (!confirm(`Delete ${doc.filename}?`)) return;
    try {
      await api.deleteDocument(token, doc.filename);
      toast.success("Document deleted");
      setDocuments((prev) => prev.filter((d) => d.id !== id));
    } catch (e: any) {
      toast.error(e.message || "Delete failed");
    }
  };

  const handleSync = async () => {
    if (!token) return;
    try {
      await api.submitSyncJob(token);
      toast.success("Sync job submitted");
      await loadDocs(true);
    } catch (e: any) {
      toast.error(e.message || "Sync failed");
    }
  };

  const handleRebuild = async () => {
    if (!token) return;
    try {
      await api.submitRebuildJob(token);
      toast.success("Rebuild job submitted");
      await loadDocs(true);
    } catch (e: any) {
      toast.error(e.message || "Rebuild failed");
    }
  };

  const loadJobs = async () => {
    if (!token) return;
    try {
      const res = await api.listJobs(token);
      setJobs(res.jobs || []);
    } catch {}
  };

  useEffect(() => {
    if (jobsOpen) loadJobs();
  }, [jobsOpen, token]);

  const navItems = [
    { href: "/library", icon: BookOpen, label: "Library" },
    { href: "/projects", icon: FolderKanban, label: "Projects" },
    { href: "/more", icon: MoreHorizontal, label: "More" },
  ];

  return (
    <aside className="w-80 h-screen border-r border-border bg-card flex flex-col">
      <div className="p-4 border-b border-border">
        <Link href="/" className="flex items-center gap-2 text-xl font-bold text-primary">
          <LayoutDashboard className="h-6 w-6" />
          AI Knowledge Base
        </Link>
      </div>

      <div className="p-3 space-y-2">
        <Button onClick={handleNewChat} className="w-full justify-start" variant="outline">
          <MessageSquarePlus className="mr-2 h-4 w-4" />
          New Chat
        </Button>

        <div className="flex gap-2">
          {navItems.map((item) => (
            <Link key={item.href} href={item.href} className="flex-1">
              <Button
                variant={pathname === item.href ? "default" : "outline"}
                className="w-full justify-center"
                size="sm"
              >
                <item.icon className="h-4 w-4" />
              </Button>
            </Link>
          ))}
        </div>
      </div>

      <div className="px-3 pb-2">
        <div className="relative">
          <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search chats"
            value={sessionSearch}
            onChange={(e) => setSessionSearch(e.target.value)}
            className="pl-8"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-3 space-y-1">
        {filteredSessions.map((session) => (
          <div key={session.id} className="group flex items-center gap-1">
            <Link href={`/?session=${session.id}`} className="flex-1">
              <Button
                variant="ghost"
                className="w-full justify-start text-sm font-normal h-9"
                size="sm"
              >
                {renamingId === session.id ? (
                  <Input
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onBlur={() => handleRenameSession(session.id)}
                    onKeyDown={(e) => e.key === "Enter" && handleRenameSession(session.id)}
                    autoFocus
                    className="h-7"
                    onClick={(e) => e.stopPropagation()}
                  />
                ) : (
                  <span className="truncate">{session.title}</span>
                )}
              </Button>
            </Link>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" className="h-8 w-8" title="Actions" aria-label="Actions">
                  <MoreVertical className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  onClick={() => {
                    setRenamingId(session.id);
                    setRenameValue(session.title);
                  }}
                >
                  <Pencil className="mr-2 h-4 w-4" /> Rename
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => handleDeleteSession(session.id)} className="text-red-600">
                  <Trash2 className="mr-2 h-4 w-4" /> Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        ))}
      </div>

      <div className="border-t border-border p-3 space-y-3">
        <div className="space-y-2">
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Documents</h4>
          <div className="flex items-center gap-2">
            <Input
              type="file"
              multiple
              onChange={(e) => setSelectedFiles(e.target.files)}
              className="text-xs h-8"
            />
            <Button size="sm" onClick={handleUpload} disabled={isUploading || !selectedFiles || selectedFiles.length === 0}>
              <UploadCloud className="h-4 w-4" />
            </Button>
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={handleSync} className="flex-1">
              Sync
            </Button>
            <Button size="sm" variant="outline" onClick={handleRebuild} className="flex-1">
              Rebuild
            </Button>
          </div>

          <Dialog open={jobsOpen} onOpenChange={setJobsOpen}>
            <DialogTrigger asChild>
              <Button variant="ghost" size="sm" className="w-full justify-between">
                Background Jobs <ChevronRight className="h-4 w-4" />
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Background Jobs</DialogTitle>
              </DialogHeader>
              <div className="space-y-2 max-h-[300px] overflow-y-auto">
                {jobs.length === 0 && <p className="text-sm text-muted-foreground">No jobs found.</p>}
                {jobs.map((job: any, index: number) => (
                  <div key={job.id ?? `job-${index}`} className="border rounded p-2 text-sm">
                    <div className="font-medium">{job.type}</div>
                    <Badge variant={job.status === "completed" ? "success" : job.status === "failed" ? "destructive" : "secondary"}>
                      {job.status}
                    </Badge>
                    <div className="text-xs text-muted-foreground mt-1">{new Date(job.created_at).toLocaleString()}</div>
                  </div>
                ))}
              </div>
            </DialogContent>
          </Dialog>
        </div>

        <div className="space-y-1 min-h-[80px]">
          {isLoadingDocs ? (
            <p className="text-xs text-muted-foreground flex items-center"><Loader2 className="h-3 w-3 animate-spin mr-1" /> Loading documents...</p>
          ) : (
            documents.map((doc) => (
              <div
                key={doc.filename}
                className="flex items-start justify-between gap-2 rounded-md px-2 py-1.5 hover:bg-muted group"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    {doc.status === "failed" && <AlertCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />}
                    <Link href={`/documents/${encodeURIComponent(doc.filename)}`} className="text-sm truncate hover:underline" title={doc.filename}>{doc.filename}</Link>
                  </div>
                  {doc.status === "failed" && (
                    <p className="mt-0.5 truncate text-[11px] text-destructive" title={doc.error}>
                      {doc.error || "Processing failed"}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  <Badge
                    variant={doc.status === "indexed" || doc.status === "completed" ? "success" : doc.status === "failed" ? "destructive" : "secondary"}
                    className="gap-1 text-xs"
                  >
                    {["pending", "processing"].includes(doc.status) && <Loader2 className="h-3 w-3 animate-spin" />}
                    {doc.status === "completed" ? "indexed" : doc.status}
                  </Badge>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-6 w-6" title="Actions" aria-label="Actions">
                        <MoreVertical className="h-3 w-3" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onClick={() => handlePreview(doc)}>
                        <BookOpen className="mr-2 h-4 w-4" /> Preview
                      </DropdownMenuItem>
                      {doc.allowed_actions?.includes("reindex") && (
                        <DropdownMenuItem onClick={() => handleReindex(doc.id)}>
                          <RefreshCw className="mr-2 h-4 w-4" /> Re-index
                        </DropdownMenuItem>
                      )}
                      {doc.allowed_actions?.includes("retry") && (
                        <DropdownMenuItem onClick={() => handleRetry(doc.id)}>
                          <RefreshCw className="mr-2 h-4 w-4" /> Retry
                        </DropdownMenuItem>
                      )}
                      <DropdownMenuItem onClick={() => handleRename(doc)}>
                        <Pencil className="mr-2 h-4 w-4" /> Rename
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleDelete(doc.id)} className="text-red-600">
                        <Trash2 className="mr-2 h-4 w-4" /> Delete
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="border-t border-border p-3">
        {!mounted ? (
          <p className="text-sm text-muted-foreground flex items-center"><Loader2 className="h-4 w-4 animate-spin mr-2" /> Loading...</p>
        ) : user ? (
          <div className="flex items-center justify-between">
            <div className="flex flex-col gap-1">
              <span className="text-sm font-medium">{user.username}</span>
              <div className="flex flex-wrap items-center gap-1">
                <Badge variant="outline" className="text-[10px] capitalize">{user.role}</Badge>
                {credits && (
                  <Badge variant="secondary" className="text-[10px] capitalize">{credits.tier}</Badge>
                )}
                {credits && (
                  <Badge variant="outline" className="text-[10px] flex items-center gap-1">
                    <CreditCard className="h-3 w-3" />
                    {credits.credits.toFixed(2)} credits
                  </Badge>
                )}
              </div>
            </div>
            <div className="flex items-center gap-1">
              <Link href="/profile">
                <Button variant="ghost" size="icon" className="h-8 w-8">
                  <User className="h-4 w-4" />
                </Button>
              </Link>
              {user.role === "admin" && (
                <Link href="/admin">
                  <Button variant="ghost" size="icon" className="h-8 w-8">
                    <Settings className="h-4 w-4" />
                  </Button>
                </Link>
              )}
              <Button variant="ghost" size="icon" className="h-8 w-8" onClick={logout}>
                <LogOut className="h-4 w-4" />
              </Button>
            </div>
          </div>
        ) : (
          <LoginDialog>
            <Button className="w-full">
              <LogIn className="mr-2 h-4 w-4" /> Sign In
            </Button>
          </LoginDialog>
        )}
      </div>
    </aside>
  );
}
