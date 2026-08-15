import { Suspense } from "react";
import { Sidebar } from "@/components/sidebar";
import { ChatInterface } from "@/components/chat-interface";

export default function HomePage() {
  return (
    <div className="flex h-screen w-full overflow-hidden">
      <Sidebar />
      <Suspense fallback={<div className="flex-1 flex items-center justify-center">Loading chat...</div>}>
        <ChatInterface />
      </Suspense>
    </div>
  );
}
