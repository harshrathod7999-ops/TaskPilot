import { useQuery } from "@tanstack/react-query";
import { PanelRight, Sparkles, Terminal } from "lucide-react";
import { health } from "@/lib/api";
import { useStore } from "@/store";
import { Button } from "@/components/ui/button";
import { RunsMenu } from "./RunsMenu";
import { ThemeToggle } from "./ThemeToggle";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

function HealthDot() {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: health,
    refetchInterval: 15_000,
  });

  const down = isError || !data;
  const ok = data?.tools_ok;
  const label = down
    ? "Backend unreachable"
    : ok
      ? `Tools ready: ${data.tools.join(", ")}`
      : "Some tools failed to load — running in a degraded mode";

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className="flex cursor-default items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground"
          aria-label={label}
        >
          <span
            className={cn(
              "h-2 w-2 rounded-full",
              down ? "bg-red-500" : ok ? "bg-emerald-500" : "bg-amber-500 animate-pulse"
            )}
          />
          {down ? "Offline" : ok ? "Tools ready" : "Degraded"}
        </span>
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
}

export function AppHeader() {
  const { showDebug, setShowDebug, setTasksOpen } = useStore();

  return (
    <header className="flex items-center gap-3 border-b border-border bg-card/40 px-4 py-2.5">
      <div className="flex items-center gap-2">
        <Sparkles className="h-5 w-5 text-primary" />
        <h1 className="text-base font-semibold tracking-tight">TaskPilot</h1>
      </div>
      <span className="hidden text-xs text-muted-foreground md:inline">
        plan → act → observe → reflect · web search + URL reader + MCP tools · human-approved writes
      </span>
      <div className="ml-auto flex items-center gap-1.5">
        <HealthDot />
        <Button
          variant="ghost"
          size="icon"
          className="lg:hidden"
          onClick={() => setTasksOpen(true)}
          title="Tasks"
        >
          <PanelRight className="h-4 w-4" />
        </Button>
        <RunsMenu />
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setShowDebug((d) => !d)}
          title="Toggle debug log"
          className={cn(showDebug && "bg-accent text-foreground")}
        >
          <Terminal className="h-4 w-4" />
        </Button>
        <ThemeToggle />
      </div>
    </header>
  );
}
