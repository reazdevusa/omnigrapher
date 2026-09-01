"use client";

import * as React from "react";
import Markdown from "react-markdown";
import { toast } from "sonner";
import {
  AlertCircle,
  Check,
  Copy,
  Download,
  FileUp,
  Globe,
  Loader2,
  Sparkles,
  Upload,
  Wand2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn, formatDate } from "@/lib/utils";
import {
  GenerateRequest,
  GenerateResponse,
  JobResultResponse,
  JobStatusResponse,
  fetchJobResult,
  fetchJobStatus,
  generateContent,
  generateContentCsv,
  subscribeJobStatus,
  topUpCredits,
} from "@/lib/aeo-api";

const PAGE_TYPES = [
  { id: "landing", label: "Landing" },
  { id: "service", label: "Service" },
  { id: "location", label: "Location" },
  { id: "faq", label: "FAQ" },
  { id: "about", label: "About" },
  { id: "blog", label: "Blog" },
];

const STORAGE_KEY = "aeo_studio_api_key";

function useLocalApiKey() {
  const [apiKey, setApiKey] = React.useState("");
  React.useEffect(() => {
    try {
      setApiKey(window.localStorage.getItem(STORAGE_KEY) || "");
    } catch {
      setApiKey("");
    }
  }, []);
  const persist = (value: string) => {
    setApiKey(value);
    try {
      if (value) window.localStorage.setItem(STORAGE_KEY, value);
      else window.localStorage.removeItem(STORAGE_KEY);
    } catch {}
  };
  return [apiKey, persist] as const;
}

function scoreColor(score?: number) {
  if (score === undefined || score === null) return "secondary";
  if (score >= 80) return "success";
  if (score >= 60) return "default";
  return "destructive";
}

export function AeoStudioDashboard() {
  const [apiKey, setApiKey] = useLocalApiKey();
  const [form, setForm] = React.useState({
    name: "",
    businessName: "",
    industry: "",
    location: "",
    targetService: "",
    keywords: "",
    competitors: "",
    websiteUrl: "",
    pageTypes: ["landing", "faq"],
  });
  const [job, setJob] = React.useState<GenerateResponse | null>(null);
  const [status, setStatus] = React.useState<JobStatusResponse | null>(null);
  const [result, setResult] = React.useState<JobResultResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [activePage, setActivePage] = React.useState(0);
  const [copied, setCopied] = React.useState<"markdown" | "html" | "jsonld" | null>(null);
  const [selectedTab, setSelectedTab] = React.useState("preview");
  const fileRef = React.useRef<HTMLInputElement>(null);

  const logsRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    if (logsRef.current) {
      logsRef.current.scrollTop = logsRef.current.scrollHeight;
    }
  }, [status?.trace]);

  const handleGenerate = async () => {
    if (!apiKey.trim()) {
      toast.error("Please enter your AEO Studio API key");
      return;
    }
    if (!form.businessName.trim()) {
      toast.error("Business Name is required");
      return;
    }
    if (form.pageTypes.length === 0) {
      toast.error("Select at least one page type");
      return;
    }

    setLoading(true);
    setResult(null);
    setJob(null);
    setStatus(null);
    setActivePage(0);
    setSelectedTab("progress");

    const request: GenerateRequest = {
      project: {
        name: form.name.trim() || form.businessName.trim(),
        business_name: form.businessName.trim(),
        business_type: form.industry.trim() || undefined,
        location: form.location.trim() || undefined,
        target_audience: form.targetService.trim() || undefined,
        website_url: form.websiteUrl.trim() || undefined,
        seed_keywords: form.keywords
          .split(",")
          .map((k) => k.trim())
          .filter(Boolean),
      },
      page_types: form.pageTypes,
      dry_run: false,
    };

    try {
      const res = await generateContent(apiKey, request);
      setJob(res);
      toast.success(res.message);
      startTracking(res.job_id);
    } catch (err: any) {
      const msg = err?.message || "Generation failed";
      if (msg.includes("402") || msg.includes("Insufficient")) {
        toast.error(
          <div className="space-y-2">
            <p>Insufficient AEO credits.</p>
            <Button size="sm" onClick={() => handleTopUp(10)}>
              Top up 10 credits
            </Button>
          </div>,
          { duration: 10000 }
        );
      } else {
        toast.error(msg);
      }
      setLoading(false);
    }
  };

  const handleCsvUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!apiKey.trim()) {
      toast.error("Please enter your AEO Studio API key");
      return;
    }
    setLoading(true);
    setResult(null);
    setJob(null);
    setStatus(null);
    setSelectedTab("progress");
    try {
      const res = await generateContentCsv(apiKey, file);
      toast.success(res.message);
      if (res.job_ids.length > 0) {
        setJob({
          job_id: res.job_ids[0],
          project_id: 0,
          status: "pending",
          message: `CSV bulk job started (${res.total_jobs} jobs)`,
          credits_deducted: res.credits_deducted,
          page_outputs_created: 0,
        });
        startTracking(res.job_ids[0]);
      }
    } catch (err: any) {
      toast.error(err?.message || "CSV upload failed");
      setLoading(false);
    }
  };

  const handleTopUp = async (amount: number) => {
    if (!apiKey.trim()) {
      toast.error("API key required to top up");
      return;
    }
    try {
      const res = await topUpCredits(apiKey, amount);
      toast.success(`Topped up ${res.credits_added} credits. Balance: ${res.new_balance}`);
    } catch (err: any) {
      toast.error(err?.message || "Top-up failed");
    }
  };

  const startTracking = (jobId: number) => {
    let closed = false;

    // Try SSE first, then fall back to polling if it errors.
    const subscription = subscribeJobStatus(
      apiKey,
      jobId,
      (next) => {
        setStatus(next);
        if (next.status === "completed" || next.status === "failed") {
          fetchResult(jobId);
        }
      },
      () => {
        // SSE failed, fall back to polling
        poll(jobId);
      }
    );

    const poll = async (id: number) => {
      while (!closed) {
        try {
          const next = await fetchJobStatus(apiKey, id);
          setStatus(next);
          if (next.status === "completed" || next.status === "failed") {
            await fetchResult(id);
            break;
          }
        } catch (err: any) {
          toast.error(err?.message || "Status poll failed");
          break;
        }
        await new Promise((r) => setTimeout(r, 1500));
      }
    };

    return () => {
      closed = true;
      subscription.close();
    };
  };

  const fetchResult = async (jobId: number) => {
    try {
      const data = await fetchJobResult(apiKey, jobId);
      setResult(data);
      setSelectedTab("preview");
      setLoading(false);
    } catch (err: any) {
      toast.error(err?.message || "Failed to load result");
      setLoading(false);
    }
  };

  const copyToClipboard = (text: string, key: "markdown" | "html" | "jsonld") => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(key);
      setTimeout(() => setCopied(null), 2000);
      toast.success("Copied to clipboard");
    });
  };

  const downloadFile = (content: string, filename: string, type: string) => {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const activeResultPage = result?.pages?.[activePage];

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b border-border bg-card px-6 py-4">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-white">
              <Sparkles className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-xl font-semibold tracking-tight">AEO Studio</h1>
              <p className="text-sm text-muted-foreground">Answer Engine Optimization Dashboard</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Input
              type="password"
              placeholder="AEO API Key"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="w-64"
            />
            <Button variant="outline" size="sm" onClick={() => handleTopUp(10)}>
              +10 Credits
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl p-6">
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          {/* Input panel */}
          <div className="lg:col-span-4 space-y-6">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Wand2 className="h-5 w-5 text-primary" />
                  New Project
                </CardTitle>
                <CardDescription>
                  Describe the business and pick the page types you want to generate.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="businessName">Business Name *</Label>
                  <Input
                    id="businessName"
                    placeholder="Acme Roofing LLC"
                    value={form.businessName}
                    onChange={(e) => setForm({ ...form, businessName: e.target.value })}
                  />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="name">Project Name</Label>
                    <Input
                      id="name"
                      placeholder="Acme Campaign"
                      value={form.name}
                      onChange={(e) => setForm({ ...form, name: e.target.value })}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="industry">Industry / Schema Type</Label>
                    <Input
                      id="industry"
                      placeholder="RoofingContractor"
                      value={form.industry}
                      onChange={(e) => setForm({ ...form, industry: e.target.value })}
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="location">Location</Label>
                    <Input
                      id="location"
                      placeholder="Austin, TX"
                      value={form.location}
                      onChange={(e) => setForm({ ...form, location: e.target.value })}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="websiteUrl">Website URL</Label>
                    <Input
                      id="websiteUrl"
                      placeholder="https://..."
                      value={form.websiteUrl}
                      onChange={(e) => setForm({ ...form, websiteUrl: e.target.value })}
                    />
                  </div>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="targetService">Target Service / Audience</Label>
                  <Input
                    id="targetService"
                    placeholder="Emergency roof repair for homeowners"
                    value={form.targetService}
                    onChange={(e) => setForm({ ...form, targetService: e.target.value })}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="keywords">Seed Keywords (comma separated)</Label>
                  <Textarea
                    id="keywords"
                    placeholder="roof repair, new roof, emergency roofing..."
                    value={form.keywords}
                    onChange={(e) => setForm({ ...form, keywords: e.target.value })}
                    rows={3}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="competitors">Competitor URLs (comma separated)</Label>
                  <Textarea
                    id="competitors"
                    placeholder="https://competitor1.com, https://competitor2.com"
                    value={form.competitors}
                    onChange={(e) => setForm({ ...form, competitors: e.target.value })}
                    rows={2}
                  />
                </div>

                <div className="space-y-2">
                  <Label>Page Types</Label>
                  <div className="flex flex-wrap gap-2">
                    {PAGE_TYPES.map((pt) => (
                      <button
                        key={pt.id}
                        type="button"
                        onClick={() =>
                          setForm((prev) => ({
                            ...prev,
                            pageTypes: prev.pageTypes.includes(pt.id)
                              ? prev.pageTypes.filter((p) => p !== pt.id)
                              : [...prev.pageTypes, pt.id],
                          }))
                        }
                        className={cn(
                          "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                          form.pageTypes.includes(pt.id)
                            ? "border-primary bg-primary text-white"
                            : "border-border bg-background hover:bg-muted"
                        )}
                      >
                        {pt.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="flex flex-col gap-3 pt-2">
                  <Button onClick={handleGenerate} disabled={loading} className="w-full">
                    {loading ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Generating...
                      </>
                    ) : (
                      <>
                        <Sparkles className="mr-2 h-4 w-4" />
                        Generate AEO Pages
                      </>
                    )}
                  </Button>

                  <div className="flex items-center gap-3 rounded-lg border border-dashed border-border p-3">
                    <input
                      ref={fileRef}
                      type="file"
                      accept=".csv"
                      className="hidden"
                      onChange={handleCsvUpload}
                    />
                    <Button
                      variant="outline"
                      className="w-full"
                      disabled={loading}
                      onClick={() => fileRef.current?.click()}
                    >
                      <Upload className="mr-2 h-4 w-4" />
                      Bulk CSV Upload
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Workspace */}
          <div className="lg:col-span-8 space-y-6">
            <Tabs value={selectedTab} onValueChange={setSelectedTab} className="w-full">
              <TabsList className="grid w-full grid-cols-4">
                <TabsTrigger value="progress">Live Progress</TabsTrigger>
                <TabsTrigger value="preview" disabled={!result}>
                  Live Preview
                </TabsTrigger>
                <TabsTrigger value="scorecard" disabled={!result}>
                  Scorecard
                </TabsTrigger>
                <TabsTrigger value="export" disabled={!result}>
                  Code Export
                </TabsTrigger>
              </TabsList>

              {/* Progress tab */}
              <TabsContent value="progress" className="space-y-4">
                <Card>
                  <CardHeader>
                    <CardTitle>Agent Execution</CardTitle>
                    <CardDescription>
                      Real-time pipeline status, agent logs, and progress.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-6">
                    {!job && !loading && (
                      <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border py-12 text-muted-foreground">
                        <Globe className="mb-3 h-10 w-10 opacity-50" />
                        <p>Fill the form and click Generate to start the AEO pipeline.</p>
                      </div>
                    )}

                    {job && (
                      <>
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-muted-foreground">Job #{job.job_id}</span>
                          <Badge
                            data-testid="aeo-status-badge"
                            variant={
                              status?.status === "completed"
                                ? "success"
                                : status?.status === "failed"
                                ? "destructive"
                                : "default"
                            }
                          >
                            {status?.status || job.status}
                          </Badge>
                        </div>
                        <Progress value={status?.progress_percent || 0} />

                        <div
                          ref={logsRef}
                          className="h-72 overflow-y-auto rounded-lg border border-border bg-muted p-4 font-mono text-xs"
                        >
                          {(status?.trace || []).length === 0 && (
                            <p className="text-muted-foreground">Waiting for agent logs...</p>
                          )}
                          {(status?.trace || []).map((entry, i) => (
                            <div key={i} className="mb-2 flex gap-2">
                              <span className="shrink-0 text-muted-foreground">
                                {entry.timestamp
                                  ? formatDate(entry.timestamp)
                                  : new Date().toLocaleTimeString()}
                              </span>
                              <span className="font-semibold text-primary">[{entry.agent}]</span>
                              <span>{entry.step}</span>
                            </div>
                          ))}
                          {status?.status === "running" && (
                            <div className="flex items-center gap-2 text-primary">
                              <Loader2 className="h-3 w-3 animate-spin" />
                              Running...
                            </div>
                          )}
                        </div>

                        {status?.error_message && (
                          <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-200">
                            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                            {status.error_message}
                          </div>
                        )}
                      </>
                    )}
                  </CardContent>
                </Card>
              </TabsContent>

              {/* Preview tab */}
              <TabsContent value="preview" className="space-y-4">
                {result && result.pages.length > 0 && (
                  <Card>
                    <CardHeader className="flex flex-row items-center justify-between">
                      <div>
                        <CardTitle>Live Preview</CardTitle>
                        <CardDescription>
                          Rendered page with direct answer block and structured sections.
                        </CardDescription>
                      </div>
                      <div className="flex gap-2">
                        {result.pages.map((_, idx) => (
                          <Button
                            key={idx}
                            size="sm"
                            variant={activePage === idx ? "default" : "outline"}
                            onClick={() => setActivePage(idx)}
                          >
                            {result.pages[idx].page_type}
                          </Button>
                        ))}
                      </div>
                    </CardHeader>
                    <CardContent>
                      {activeResultPage && (
                        <div className="max-w-none rounded-lg border border-border bg-card p-6 text-foreground">
                          <Markdown
                            components={{
                              h1: ({ children }) => <h1 className="mb-4 text-2xl font-bold">{children}</h1>,
                              h2: ({ children }) => <h2 className="mb-3 mt-6 text-xl font-semibold">{children}</h2>,
                              p: ({ children }) => <p className="mb-4 leading-relaxed">{children}</p>,
                              ul: ({ children }) => <ul className="mb-4 list-disc pl-5">{children}</ul>,
                              ol: ({ children }) => <ol className="mb-4 list-decimal pl-5">{children}</ol>,
                              li: ({ children }) => <li className="mb-1">{children}</li>,
                              strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
                            }}
                          >
                            {activeResultPage.markdown || "No content generated."}
                          </Markdown>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                )}
                {!result && (
                  <div className="rounded-lg border border-dashed border-border p-12 text-center text-muted-foreground">
                    Generate content first to see the preview.
                  </div>
                )}
              </TabsContent>

              {/* Scorecard tab */}
              <TabsContent value="scorecard" className="space-y-4">
                {result && (
                  <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                    {result.pages.map((page, idx) => (
                      <Card key={idx} className={cn(activePage === idx && "ring-2 ring-primary")}>
                        <CardHeader>
                          <CardTitle className="text-base">{page.page_type}</CardTitle>
                          <CardDescription className="line-clamp-1">
                            {page.title}
                          </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-3">
                          <div className="flex items-center justify-between">
                            <span className="text-sm text-muted-foreground">AEO Readiness</span>
                            <Badge variant={scoreColor(page.aeo_score)}>
                              {page.aeo_score?.toFixed(0) ?? "—"}/100
                            </Badge>
                          </div>
                          <Progress value={page.aeo_score || 0} />
                          <div className="text-sm text-muted-foreground">
                            Entities: {page.json_ld.length} JSON-LD blocks
                          </div>
                          {page.direct_answer_block && (
                            <div className="rounded-md bg-muted p-2 text-xs">
                              <span className="font-semibold">Direct Answer:</span>{" "}
                              {page.direct_answer_block.slice(0, 120)}...
                            </div>
                          )}
                        </CardContent>
                      </Card>
                    ))}
                  </div>
                )}
              </TabsContent>

              {/* Export tab */}
              <TabsContent value="export" className="space-y-4">
                {result && activeResultPage && (
                  <Tabs defaultValue="markdown" className="w-full">
                    <TabsList className="mb-2">
                      <TabsTrigger value="markdown">Markdown</TabsTrigger>
                      <TabsTrigger value="html">HTML</TabsTrigger>
                      <TabsTrigger value="jsonld">JSON-LD</TabsTrigger>
                    </TabsList>

                    <TabsContent value="markdown">
                      <ExportPanel
                        value={activeResultPage.markdown}
                        filename={`${activeResultPage.slug}.md`}
                        mime="text/markdown"
                        copied={copied === "markdown"}
                        onCopy={() => copyToClipboard(activeResultPage.markdown, "markdown")}
                        onDownload={(content, name, type) => downloadFile(content, name, type)}
                      />
                    </TabsContent>

                    <TabsContent value="html">
                      <ExportPanel
                        value={activeResultPage.html}
                        filename={`${activeResultPage.slug}.html`}
                        mime="text/html"
                        copied={copied === "html"}
                        onCopy={() => copyToClipboard(activeResultPage.html, "html")}
                        onDownload={(content, name, type) => downloadFile(content, name, type)}
                      />
                    </TabsContent>

                    <TabsContent value="jsonld">
                      <ExportPanel
                        value={JSON.stringify(
                          activeResultPage.json_ld.length
                            ? activeResultPage.json_ld
                            : result.json_ld_schemas,
                          null,
                          2
                        )}
                        filename={`${activeResultPage.slug}-schema.json`}
                        mime="application/json"
                        copied={copied === "jsonld"}
                        onCopy={() =>
                          copyToClipboard(
                            JSON.stringify(
                              activeResultPage.json_ld.length
                                ? activeResultPage.json_ld
                                : result.json_ld_schemas,
                              null,
                              2
                            ),
                            "jsonld"
                          )
                        }
                        onDownload={(content, name, type) => downloadFile(content, name, type)}
                      />
                    </TabsContent>
                  </Tabs>
                )}
              </TabsContent>
            </Tabs>
          </div>
        </div>
      </main>
    </div>
  );
}

function ExportPanel({
  value,
  filename,
  mime,
  copied,
  onCopy,
  onDownload,
}: {
  value: string;
  filename: string;
  mime: string;
  copied: boolean;
  onCopy: () => void;
  onDownload: (content: string, filename: string, mime: string) => void;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">Export</CardTitle>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={onCopy}>
            {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
            <span className="ml-2">{copied ? "Copied" : "Copy"}</span>
          </Button>
          <Button size="sm" variant="outline" onClick={() => onDownload(value, filename, mime)}>
            <Download className="mr-2 h-4 w-4" />
            Download
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <pre className="max-h-[60vh] overflow-auto rounded-lg border border-border bg-muted p-4 text-xs">
          <code>{value}</code>
        </pre>
      </CardContent>
    </Card>
  );
}
