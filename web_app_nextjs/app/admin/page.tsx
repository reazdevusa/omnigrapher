"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/sidebar";
import { useAuth } from "@/components/auth-provider";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import * as api from "@/lib/api";
import { toast } from "sonner";
import { Settings, Users, Activity, Globe, MessageSquare, Trash2, Shield } from "lucide-react";

export default function AdminPage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const [activeTab, setActiveTab] = useState<"users" | "health" | "widget" | "feedback">("users");
  const [users, setUsers] = useState<any[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [widget, setWidget] = useState<any>(null);
  const [feedback, setFeedback] = useState<any[]>([]);

  useEffect(() => {
    if (!user || user.role !== "admin") {
      router.push("/");
      return;
    }
    if (!token) return;
    loadUsers();
    loadHealth();
    loadWidget();
    loadFeedback();
  }, [user, token, router]);

  const loadUsers = async () => {
    if (!token) return;
    try {
      const res = await api.adminListUsers(token);
      setUsers(res.users || []);
    } catch (e: any) {
      toast.error(e.message || "Failed to load users");
    }
  };

  const loadHealth = async () => {
    if (!token) return;
    try {
      const res = await api.adminHealthStatus(token);
      setHealth(res);
    } catch {}
  };

  const loadWidget = async () => {
    if (!token) return;
    try {
      const res = await api.adminWidgetConfig(token);
      setWidget(res);
    } catch {}
  };

  const loadFeedback = async () => {
    if (!token) return;
    try {
      const res = await api.adminListFeedback(token);
      setFeedback(res.feedback || []);
    } catch {}
  };

  const handleDeleteUser = async (username: string) => {
    if (!token || !confirm(`Delete user ${username}?`)) return;
    try {
      await api.adminDeleteUser(token, username);
      toast.success("User deleted");
      loadUsers();
    } catch (e: any) {
      toast.error(e.message || "Delete failed");
    }
  };

  const handleSetRole = async (username: string, role: string) => {
    if (!token) return;
    try {
      await api.adminSetRole(token, username, role);
      toast.success("Role updated");
      loadUsers();
    } catch (e: any) {
      toast.error(e.message || "Update failed");
    }
  };

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto p-6">
        <div className="max-w-5xl mx-auto space-y-6">
          <div className="flex items-center gap-4">
            <Settings className="h-8 w-8 text-primary" />
            <h1 className="text-3xl font-bold">Admin Panel</h1>
          </div>
          <div className="flex gap-2">
            <Button variant={activeTab === "users" ? "default" : "outline"} onClick={() => setActiveTab("users")}>
              <Users className="mr-2 h-4 w-4" /> Users
            </Button>
            <Button variant={activeTab === "health" ? "default" : "outline"} onClick={() => setActiveTab("health")}>
              <Activity className="mr-2 h-4 w-4" /> Health
            </Button>
            <Button variant={activeTab === "widget" ? "default" : "outline"} onClick={() => setActiveTab("widget")}>
              <Globe className="mr-2 h-4 w-4" /> Widget
            </Button>
            <Button variant={activeTab === "feedback" ? "default" : "outline"} onClick={() => setActiveTab("feedback")}>
              <MessageSquare className="mr-2 h-4 w-4" /> Feedback
            </Button>
          </div>

          {activeTab === "users" && (
            <Card>
              <CardHeader>
                <CardTitle>Users</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {users.map((u) => (
                    <div key={u.username} className="flex items-center justify-between p-3 border rounded-lg">
                      <div>
                        <p className="font-medium">{u.username}</p>
                        <Badge variant={u.role === "admin" ? "default" : "secondary"}>{u.role}</Badge>
                      </div>
                      <div className="flex gap-2">
                        <Button size="sm" variant="outline" onClick={() => handleSetRole(u.username, u.role === "admin" ? "user" : "admin")}>
                          <Shield className="mr-2 h-4 w-4" /> {u.role === "admin" ? "Demote" : "Promote"}
                        </Button>
                        <Button size="sm" variant="destructive" onClick={() => handleDeleteUser(u.username)}>
                          <Trash2 className="mr-2 h-4 w-4" /> Delete
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {activeTab === "health" && health && (
            <Card>
              <CardHeader>
                <CardTitle>Health Status</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {Object.entries(health.latest || {}).map(([name, check]: [string, any]) => (
                    <div key={name} className="flex items-center justify-between p-3 border rounded-lg">
                      <span className="font-medium capitalize">{name}</span>
                      <Badge variant={check.status === "ok" ? "default" : "destructive"}>{check.status}</Badge>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {activeTab === "widget" && widget && (
            <Card>
              <CardHeader>
                <CardTitle>Widget Configuration</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <Badge variant={widget.enabled ? "default" : "secondary"}>{widget.enabled ? "Enabled" : "Disabled"}</Badge>
                {widget.enabled && (
                  <>
                    <p className="text-sm text-muted-foreground">Backend URL: {widget.backend_url}</p>
                    <textarea
                      readOnly
                      value={widget.embed_code}
                      className="w-full h-32 text-sm font-mono p-3 border rounded-lg bg-muted"
                    />
                    <Button onClick={() => navigator.clipboard.writeText(widget.embed_code).then(() => toast.success("Copied"))}>
                      Copy Embed Code
                    </Button>
                  </>
                )}
              </CardContent>
            </Card>
          )}

          {activeTab === "feedback" && (
            <Card>
              <CardHeader>
                <CardTitle>Feedback</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2 max-h-[500px] overflow-y-auto">
                  {feedback.length === 0 && <p className="text-muted-foreground">No feedback yet.</p>}
                  {feedback.map((f, i) => (
                    <div key={i} className="p-3 border rounded-lg text-sm">
                      <div className="flex items-center gap-2">
                        <Badge variant={f.rating === "up" ? "default" : "destructive"}>{f.rating}</Badge>
                        <span className="text-muted-foreground">{f.username}</span>
                      </div>
                      <p className="mt-1">{f.query}</p>
                      {f.comment && <p className="text-muted-foreground mt-1">Comment: {f.comment}</p>}
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      </main>
    </div>
  );
}
