import type { Metadata } from "next";
import "./globals.css";
import "highlight.js/styles/github-dark.css";
import { AuthProvider } from "@/components/auth-provider";
import { Toaster } from "sonner";

export const metadata: Metadata = {
  title: "AI Knowledge Base",
  description: "Production-grade AI knowledge base assistant",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning data-scroll-behavior="smooth">
      <body className="min-h-screen bg-background text-foreground antialiased">
        <AuthProvider>
          {children}
          <Toaster position="top-right" richColors />
        </AuthProvider>
      </body>
    </html>
  );
}
