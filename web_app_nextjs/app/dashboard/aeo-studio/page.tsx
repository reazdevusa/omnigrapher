import { Sidebar } from "@/components/sidebar";
import { AeoStudioDashboard } from "@/components/aeo-studio-dashboard";

export const metadata = {
  title: "AEO Studio Dashboard",
  description: "Answer Engine Optimization page generation dashboard",
};

export default function AeoStudioPage() {
  return (
    <div className="flex h-screen w-full overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto bg-background">
        <AeoStudioDashboard />
      </main>
    </div>
  );
}
