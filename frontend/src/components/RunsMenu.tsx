import { useQuery, useQueryClient } from "@tanstack/react-query";
import { History, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { deleteRun, listRuns, type RunStatus } from "@/lib/api";
import { useStore } from "@/store";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

const STATUS_DOT: Record<RunStatus, string> = {
  running: "bg-amber-500 animate-pulse",
  paused: "bg-amber-500",
  done: "bg-emerald-500",
  error: "bg-red-500",
  stopped: "bg-muted-foreground",
};

function relativeTime(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/**
 * Run history — the backend persists every run's trace (see runs_store.py),
 * including ones that paused for approval before a restart, so this list
 * survives independently of the current browser session.
 */
export function RunsMenu() {
  const qc = useQueryClient();
  const viewingRunId = useStore((s) => s.viewingRunId);
  const setViewingRunId = useStore((s) => s.setViewingRunId);

  const { data: runs = [] } = useQuery({
    queryKey: ["runs"],
    queryFn: () => listRuns(30),
    refetchInterval: 10_000,
  });

  async function remove(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    try {
      await deleteRun(id);
      qc.invalidateQueries({ queryKey: ["runs"] });
      if (viewingRunId === id) setViewingRunId(null);
    } catch (err) {
      toast.error((err as Error).message);
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" title="Run history">
          <History className="h-4 w-4" />
          <span className="hidden sm:inline">Runs</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <DropdownMenuLabel className="text-xs text-muted-foreground">
          Recent runs
        </DropdownMenuLabel>
        {runs.length === 0 && (
          <p className="px-2 py-3 text-xs text-muted-foreground">
            No runs yet — give the agent a task.
          </p>
        )}
        {runs.map((r) => (
          <DropdownMenuItem
            key={r.id}
            onClick={() => setViewingRunId(r.id)}
            className={cn("group gap-2", viewingRunId === r.id && "bg-accent")}
          >
            <span className={cn("h-2 w-2 shrink-0 rounded-full", STATUS_DOT[r.status])} />
            <span className="min-w-0 flex-1 truncate">{r.task}</span>
            <span className="shrink-0 text-[10px] text-muted-foreground">
              {relativeTime(r.created_at)}
            </span>
            <button
              onClick={(e) => remove(r.id, e)}
              className="shrink-0 rounded p-0.5 opacity-0 hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
              title="Delete run"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </DropdownMenuItem>
        ))}
        {viewingRunId && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => setViewingRunId(null)}>
              Back to live console
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
