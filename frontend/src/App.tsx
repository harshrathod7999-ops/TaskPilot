import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { useApplyTheme } from "@/lib/theme";
import { useKeyboardShortcuts } from "@/lib/useKeyboardShortcuts";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { useStore } from "@/store";
import { AppHeader } from "./components/AppHeader";
import { AgentConsole } from "./components/AgentConsole";
import { TasksPanel } from "./components/TasksPanel";

export default function App() {
  useApplyTheme();
  useKeyboardShortcuts();
  const tasksOpen = useStore((s) => s.tasksOpen);
  const setTasksOpen = useStore((s) => s.setTasksOpen);
  const isDesktop = useMediaQuery("(min-width: 1024px)");

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
        <AppHeader />
        <div className="flex min-h-0 flex-1">
          <AgentConsole />
          {isDesktop && <TasksPanel />}
        </div>
      </div>

      {/* Tasks sidebar as an overlay below the lg breakpoint */}
      <Sheet open={tasksOpen && !isDesktop} onOpenChange={setTasksOpen}>
        <SheetContent side="right" className="w-80 gap-0 p-0">
          <SheetTitle className="sr-only">Tasks</SheetTitle>
          <TasksPanel />
        </SheetContent>
      </Sheet>

      <Toaster position="bottom-right" />
    </TooltipProvider>
  );
}
