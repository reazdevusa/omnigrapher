import { Sidebar } from "@/components/sidebar";
import { RecruiterMLShowcase } from "@/components/RecruiterMLShowcase";

export const metadata = {
  title: "Live AI Showcase",
  description: "Live speech, vision, and neural-net capability demos",
};

export default function MLShowcasePage() {
  return (
    <div className="flex h-screen w-full overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto bg-background">
        <RecruiterMLShowcase />
      </main>
    </div>
  );
}
