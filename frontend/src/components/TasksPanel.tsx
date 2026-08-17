import { useQuery } from "@tanstack/react-query";
import { ClipboardList, CheckCircle2, Circle, RefreshCw } from "lucide-react";
import { listTasks, type TaskItem } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

// Shows the side-effect surface: tasks the agent created via the MCP server.
export function TasksPanel() {
  const { data: tasks = [], isLoading, refetch, isFetching } = useQuery({
    queryKey: ["tasks"],
    queryFn: listTasks,
    refetchInterval: 4000,   // auto-refresh every 4 s while the panel is mounted
  });

  return (
    <aside className="flex h-full w-80 flex-col border-l border-border bg-card/40">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <ClipboardList className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-sm font-semibold">Tasks created</h2>
        <span className="text-xs text-muted-foreground">via MCP</span>
        <button
          onClick={() => refetch()}
          title="Refresh tasks"
          className="ml-auto rounded p-1 text-muted-foreground hover:bg-muted"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", isFetching && "animate-spin")} />
        </button>
      </div>
      <div className="scroll-thin flex-1 overflow-y-auto p-3">
        {isLoading && (
          <div className="space-y-2">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        )}
        {!isLoading && tasks.length === 0 && (
          <div className="space-y-2 text-xs text-muted-foreground">
            <p className="font-medium text-foreground/70">No tasks created yet.</p>
            <p>
              This panel only fills when your request includes a <strong>write action</strong>,
              e.g.:
            </p>
            <ul className="ml-3 list-disc space-y-1">
              <li>"…and <em>add a task</em> to watch the highlights"</li>
              <li>"…then <em>create a task</em> to follow up next week"</li>
            </ul>
            <p className="pt-1 text-muted-foreground/70">
              Pure research tasks (like match stats) don't create tasks — the agent just
              answers directly.
            </p>
          </div>
        )}
        <ul className="space-y-2">
          {tasks.map((t: TaskItem) => (
            <li
              key={t.id}
              className="rounded-md border border-border px-3 py-2 text-sm"
            >
              <div className="flex items-start gap-2">
                {t.status === "done" ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                ) : (
                  <Circle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                )}
                <div className="min-w-0">
                  <p className={cn("font-medium", t.status === "done" && "line-through opacity-60")}>
                    {t.title}
                  </p>
                  {t.notes && (
                    <p className="mt-0.5 text-xs text-muted-foreground">{t.notes}</p>
                  )}
                  {t.due && (
                    <p className="mt-0.5 text-xs text-amber-600">due {t.due}</p>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
