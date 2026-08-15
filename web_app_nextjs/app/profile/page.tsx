"use client";

import { useEffect, useState } from "react";
import { Sidebar } from "@/components/sidebar";
import { useAuth } from "@/components/auth-provider";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import * as api from "@/lib/api";
import { toast } from "sonner";
import { User } from "lucide-react";

export default function ProfilePage() {
  const { token, user } = useAuth();
  const [profile, setProfile] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");

  useEffect(() => {
    if (!token) return;
    api.getProfile(token)
      .then((p) => {
        setProfile(p);
        setDisplayName(p.display_name || "");
        setEmail(p.email || "");
        setPhone(p.phone || "");
      })
      .catch(() => toast.error("Failed to load profile"))
      .finally(() => setLoading(false));
  }, [token]);

  const handleSave = async () => {
    if (!token) return;
    if (newPassword) {
      if (!currentPassword) {
        toast.error("Current password is required to set a new password");
        return;
      }
      if (newPassword.length < 8 || !/[A-Z]/.test(newPassword) || !/[a-z]/.test(newPassword) || !/\d/.test(newPassword)) {
        toast.error("New password must be at least 8 characters with uppercase, lowercase, and a digit");
        return;
      }
    }
    try {
      const payload: Record<string, any> = {
        display_name: displayName.trim() || null,
        email: email.trim() || null,
        phone: phone.trim() || null,
      };
      if (newPassword) {
        payload.current_password = currentPassword;
        payload.new_password = newPassword;
      }
      await api.updateProfile(token, payload);
      toast.success("Profile updated");
      setCurrentPassword("");
      setNewPassword("");
    } catch (e: any) {
      toast.error(e.message || "Update failed");
    }
  };

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto p-6">
        <div className="max-w-2xl mx-auto space-y-6">
          <div className="flex items-center gap-4">
            <User className="h-8 w-8 text-primary" />
            <h1 className="text-3xl font-bold">Profile</h1>
          </div>
          {loading ? (
            <p className="text-muted-foreground">Loading...</p>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>{user?.username}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Display Name</label>
                  <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">Email</label>
                  <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">Phone</label>
                  <Input type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">Current Password</label>
                  <Input type="password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">New Password</label>
                  <Input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
                </div>
                <Button onClick={handleSave}>Save Changes</Button>
              </CardContent>
            </Card>
          )}
        </div>
      </main>
    </div>
  );
}
