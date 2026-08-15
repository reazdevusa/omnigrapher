"use client";

import { useEffect, useState } from "react";
import { Sidebar } from "@/components/sidebar";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FolderKanban, Plus } from "lucide-react";

type Project = { id: string; name: string; description: string; createdAt: number };

const STORAGE_KEY = "kb_projects";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [showForm, setShowForm] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) setProjects(JSON.parse(raw));
  }, []);

  const save = (items: Project[]) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
    setProjects(items);
  };

  const handleAdd = () => {
    if (!name.trim()) return;
    const project: Project = { id: `proj_${Date.now()}`, name, description, createdAt: Date.now() };
    save([project, ...projects]);
    setName("");
    setDescription("");
    setShowForm(false);
  };

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto p-6">
        <div className="max-w-4xl mx-auto space-y-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <FolderKanban className="h-8 w-8 text-primary" />
              <h1 className="text-3xl font-bold">Projects</h1>
            </div>
            <Button onClick={() => setShowForm(!showForm)}>
              <Plus className="mr-2 h-4 w-4" /> New Project
            </Button>
          </div>
          {showForm && (
            <Card>
              <CardContent className="p-4 space-y-3">
                <Input placeholder="Project name" value={name} onChange={(e) => setName(e.target.value)} />
                <Textarea placeholder="Description" value={description} onChange={(e) => setDescription(e.target.value)} />
                <div className="flex gap-2">
                  <Button onClick={handleAdd}>Create</Button>
                  <Button variant="outline" onClick={() => setShowForm(false)}>Cancel</Button>
                </div>
              </CardContent>
            </Card>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {projects.map((p) => (
              <Card key={p.id}>
                <CardHeader>
                  <CardTitle>{p.name}</CardTitle>
                  <CardContent className="p-0">
                    <p className="text-sm text-muted-foreground">{p.description}</p>
                  </CardContent>
                </CardHeader>
              </Card>
            ))}
          </div>
        </div>
      </main>
    </div>
  );
}
