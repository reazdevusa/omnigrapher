"use client";

import Link from "next/link";
import { Sidebar } from "@/components/sidebar";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/components/auth-provider";
import { Settings, User, BarChart3, MoreHorizontal } from "lucide-react";

export default function MorePage() {
  const { user } = useAuth();
  const items = [
    { href: "/profile", icon: User, title: "Profile", desc: "Manage your account and settings" },
  ];
  if (user?.role === "admin") {
    items.push({ href: "/admin", icon: Settings, title: "Admin Panel", desc: "Users, health, widget, and feedback" });
  }

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto p-6">
        <div className="max-w-4xl mx-auto space-y-6">
          <div className="flex items-center gap-4">
            <MoreHorizontal className="h-8 w-8 text-primary" />
            <h1 className="text-3xl font-bold">More</h1>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {items.map((item) => (
              <Link href={item.href} key={item.href}>
                <Card className="hover:bg-muted/50 transition-colors cursor-pointer">
                  <CardHeader className="flex flex-row items-center gap-4">
                    <item.icon className="h-6 w-6 text-primary" />
                    <div>
                      <CardTitle className="text-lg">{item.title}</CardTitle>
                      <CardContent className="p-0 text-sm text-muted-foreground">{item.desc}</CardContent>
                    </div>
                  </CardHeader>
                </Card>
              </Link>
            ))}
          </div>
        </div>
      </main>
    </div>
  );
}
