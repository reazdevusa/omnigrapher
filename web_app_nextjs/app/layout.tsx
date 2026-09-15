import type { Metadata } from "next";
import "./globals.css";
import "highlight.js/styles/github-dark.css";
import { AuthProvider } from "@/components/auth-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "sonner";

export const metadata: Metadata = {
  title: "OmniGrapher",
  description: "Understand Everything. Connect Everything. AI Knowledge Base Platform",
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
          <TooltipProvider delayDuration={300}>
            {children}
          </TooltipProvider>
          <Toaster position="top-right" richColors />
        </AuthProvider>
      </body>
    </html>
  );
}
