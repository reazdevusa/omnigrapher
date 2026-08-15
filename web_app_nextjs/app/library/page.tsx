"use client";

import { useEffect, useState } from "react";
import { Sidebar } from "@/components/sidebar";
import { useAuth } from "@/components/auth-provider";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import * as api from "@/lib/api";
import { BookOpen, Search } from "lucide-react";

export default function LibraryPage() {
  const { token } = useAuth();
  const [items, setItems] = useState<any[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!token) return;
    const load = async () => {
      try {
        const res = await api.listDocuments(token);
        setItems(res.documents);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [token]);

  const filtered = items.filter((i) => i.filename.toLowerCase().includes(search.toLowerCase()));

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto p-6">
        <div className="max-w-4xl mx-auto space-y-6">
          <div className="flex items-center gap-4">
            <BookOpen className="h-8 w-8 text-primary" />
            <h1 className="text-3xl font-bold">Library</h1>
          </div>
          <div className="relative">
            <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search documents..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-10"
            />
          </div>
          {loading ? (
            <p className="text-muted-foreground">Loading...</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {filtered.map((doc) => (
                <Card key={doc.filename}>
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base truncate">{doc.filename}</CardTitle>
                    <CardContent className="p-0">
                      <Badge variant={doc.status === "indexed" || doc.status === "completed" ? "default" : doc.status === "failed" ? "destructive" : "secondary"}>{doc.status === "completed" ? "indexed" : doc.status}</Badge>
                      <p className="text-sm text-muted-foreground mt-2">{doc.chunks} chunks</p>
                      {doc.status === "failed" && (
                        <p className="mt-2 text-xs text-destructive" title={doc.error_code}>
                          {doc.error || "Document processing failed."}
                        </p>
                      )}
                    </CardContent>
                  </CardHeader>
                </Card>
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
