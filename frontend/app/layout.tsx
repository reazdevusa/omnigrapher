import type { Metadata } from "next";
import localFont from "next/font/local";
import Link from "next/link";
import "./globals.css";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-geist-sans",
  weight: "100 900",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

export const metadata: Metadata = {
  title: "OmniGrapher",
  description: "AI-powered local knowledge base and agentic RAG system",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased min-h-screen bg-gray-50 text-gray-900`}
      >
        <nav className="bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-6 shadow-sm">
          <span className="font-bold text-lg text-indigo-600 tracking-tight">
            OmniGrapher
          </span>
          <Link
            href="/documents"
            className="text-sm font-medium text-gray-600 hover:text-indigo-600 transition-colors"
          >
            Documents
          </Link>
          <Link
            href="/chat"
            className="text-sm font-medium text-gray-600 hover:text-indigo-600 transition-colors"
          >
            Chat
          </Link>
        </nav>
        <main className="max-w-4xl mx-auto px-4 py-8">{children}</main>
      </body>
    </html>
  );
}
