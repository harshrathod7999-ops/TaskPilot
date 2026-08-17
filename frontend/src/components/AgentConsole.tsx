import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bot, Play, Loader2, RotateCcw, Square } from "lucide-react";
import { toast } from "sonner";
import {
  runAgent,
  resumeAgent,
  getRun,
  type TraceItem,
  type PendingAction,
  type StreamHandlers,
} from "@/lib/api";
import { eventToTraceItem } from "@/lib/trace";
import { useStore } from "@/store";
import { Button } from "@/components/ui/button";
import { AgentTrace } from "./AgentTrace";
import { ApprovalModal } from "./ApprovalModal";

const SAMPLES = [
  "Research the latest on the James Webb telescope and summarize 3 key findings with sources.",
  "Find out who won the most recent Formula 1 race and add a task to watch the highlights.",
  "What is Retrieval-Augmented Generation? Give a concise explanation with a source.",
];

// Human-readable labels for each backend phase
const PHASE_LABEL: Record<string, string> = {
  started:          "Starting…",
  planning:         "Planning the approach…",
  reasoning:        "Deciding next step…",
  executing:        "Running tool…",
  critiquing:       "Critic reviewing answer…",
  finalizing:       "Writing the final answer…",
  waiting_provider: "Provider rate-limited — trying next model…",
};

type RunState = "idle" | "running" | "awaiting" | "done";

export function AgentConsole() {
  const qc = useQueryClient();
  const [task, setTask] = useState("");
  const [trace, setTrace] = useState<TraceItem[]>([]);
  const [state, setState] = useState<RunState>("idle");
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [phase, setPhase] = useState<string>("");
  const [stepInfo, setStepInfo] = useState<{ step: number; max: number } | null>(null);
  const showDebug = useStore((s) => s.showDebug);
  const [debugLog, setDebugLog] = useState<string[]>([]);
  const threadRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const lastTaskRef = useRef<string>("");
  const runStartRef = useRef<number | null>(null);
  const [elapsedS, setElapsedS] = useState<number | null>(null);

  const viewingRunId = useStore((s) => s.viewingRunId);
  const setViewingRunId = useStore((s) => s.setViewingRunId);
  const { data: viewedRun, isLoading: viewedRunLoading } = useQuery({
    queryKey: ["run", viewingRunId],
    queryFn: () => getRun(viewingRunId!),
    enabled: !!viewingRunId,
  });
  const replayTrace = useMemo<TraceItem[]>(() => {
    if (!viewedRun) return [];
    return viewedRun.events
      .map((e) => eventToTraceItem(e.event, e.data, new Date(e.ts).getTime()))
      .filter((t): t is TraceItem => t !== null);
  }, [viewedRun]);

  const push = (item: TraceItem) => setTrace((t) => [...t, item]);
  const log = (msg: string) =>
    setDebugLog((l) => [...l.slice(-199), `${new Date().toLocaleTimeString()} ${msg}`]);

  const handlers: StreamHandlers = {
    onEvent: (event, data) => {
      const ts = Date.now();
      log(`← ${event} ${JSON.stringify(data).slice(0, 120)}`);

      // Streamed final-answer tokens append into (or start) a single
      // streaming "final" trace entry, rather than one entry per token.
      if (event === "final_token") {
        setTrace((t) => {
          const last = t[t.length - 1];
          if (last?.kind === "final" && last.streaming) {
            return [...t.slice(0, -1), { ...last, text: last.text + data.text }];
          }
          return [...t, { kind: "final", text: data.text, streaming: true, ts }];
        });
        return;
      }
      // The authoritative `final` event replaces the streaming entry (if any)
      // with the complete text + sources, keeping the entry's original ts.
      if (event === "final") {
        setTrace((t) => {
          const last = t[t.length - 1];
          const finalItem: TraceItem = {
            kind: "final",
            text: data.text,
            sources: data.sources,
            ts: last?.kind === "final" ? last.ts : ts,
          };
          return last?.kind === "final" ? [...t.slice(0, -1), finalItem] : [...t, finalItem];
        });
        setPhase("Done");
        return;
      }

      const item = eventToTraceItem(event, data, ts);
      if (item) push(item);
      switch (event) {
        case "status":
          if (data.thread_id) threadRef.current = data.thread_id;
          setPhase(PHASE_LABEL[data.phase] ?? data.phase ?? "");
          if (data.phase === "executing" && data.tool) {
            setPhase(`Running ${data.tool}…`);
          }
          if (data.step !== undefined && data.max_steps !== undefined) {
            setStepInfo({ step: data.step, max: data.max_steps });
          }
          break;
        case "thought":
        case "action":
          if (data.step !== undefined) setStepInfo({ step: data.step, max: data.max_steps });
          break;
        case "critique":
          setPhase("Critic reviewing…");
          break;
        case "approval_required":
          setPending({
            tool: data.tool,
            args: data.args ?? {},
            thought: data.thought,
            step: data.step,
            maxSteps: data.max_steps,
            argsHint: data.args_hint,
            schema: data.schema,
          });
          break;
        case "paused":
          threadRef.current = data.thread_id;
          setState("awaiting");
          setPhase("Waiting for your approval…");
          qc.invalidateQueries({ queryKey: ["runs"] });
          break;
        case "done":
          setState("done");
          setPhase("");
          toast.success("Run complete");
          qc.invalidateQueries({ queryKey: ["tasks"] });
          qc.invalidateQueries({ queryKey: ["runs"] });
          break;
        case "error":
          setState("done");
          setPhase("");
          toast.error(data.message);
          qc.invalidateQueries({ queryKey: ["runs"] });
          break;
      }
    },
    onError: (err) => {
      push({ kind: "error", message: err.message, ts: Date.now() });
      toast.error(err.message);
      setState("done");
      setPhase("");
    },
  };

  async function start(t: string) {
    const q = t.trim();
    if (!q || state === "running" || state === "awaiting") return;
    setViewingRunId(null);
    lastTaskRef.current = q;
    setTrace([]);
    setDebugLog([]);
    setPending(null);
    threadRef.current = null;
    setState("running");
    setPhase("Starting…");
    setStepInfo(null);
    runStartRef.current = Date.now();
    abortRef.current = new AbortController();
    log(`→ /run task=${q.slice(0, 80)}`);
    await runAgent(q, handlers, abortRef.current.signal);
  }

  async function retry() {
    if (lastTaskRef.current) start(lastTaskRef.current);
  }

  async function decide(approved: boolean, editedArgs: Record<string, unknown> | null) {
    setPending(null);
    setState("running");
    setPhase("Resuming…");
    const tid = threadRef.current;
    if (!tid) return;
    toast(approved ? "Action approved — resuming" : "Action rejected — agent will re-plan");
    runStartRef.current = Date.now();
    abortRef.current = new AbortController();
    log(`→ /resume thread=${tid.slice(0, 8)} approved=${approved}`);
    await resumeAgent(tid, approved, editedArgs, handlers, abortRef.current.signal);
  }

  // Rehydrate live state from a replayed *paused* run so the normal
  // approve/reject flow (decide → /resume) picks it up from here — this is
  // what makes "a paused run survives a restart" tangible in the UI: you can
  // browse to it from history and resume it as if it never left.
  function resumeFromReplay() {
    if (!viewedRun) return;
    const approvalEvent = [...viewedRun.events].reverse().find((e) => e.event === "approval_required");
    if (!approvalEvent) return;
    setTrace(replayTrace);
    threadRef.current = viewedRun.id;
    lastTaskRef.current = viewedRun.task;
    setPending({
      tool: approvalEvent.data.tool,
      args: approvalEvent.data.args ?? {},
      thought: approvalEvent.data.thought,
      step: approvalEvent.data.step,
      maxSteps: approvalEvent.data.max_steps,
      argsHint: approvalEvent.data.args_hint,
      schema: approvalEvent.data.schema,
    });
    setState("awaiting");
    setPhase("Waiting for your approval…");
    setViewingRunId(null);
  }

  const busy = state === "running";
  const hasError = trace.some((i) => i.kind === "error");

  // Elapsed-time ticker for the phase bar — resets whenever a new stream
  // starts (start() / decide()) and stops as soon as the run leaves "running".
  useEffect(() => {
    if (!busy) {
      setElapsedS(null);
      return;
    }
    const id = setInterval(() => {
      setElapsedS(Math.floor((Date.now() - (runStartRef.current ?? Date.now())) / 1000));
    }, 500);
    return () => clearInterval(id);
  }, [busy]);

  if (viewingRunId) {
    return (
      <main className="flex h-full min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-border bg-muted/30 px-6 py-2.5">
          <Button variant="ghost" size="sm" onClick={() => setViewingRunId(null)}>
            <ArrowLeft className="h-3.5 w-3.5" /> Back to live console
          </Button>
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
            Viewing past run: <span className="text-foreground">{viewedRun?.task}</span>
          </span>
          {viewedRun?.status === "paused" && (
            <Button size="sm" onClick={resumeFromReplay}>
              Resume
            </Button>
          )}
          {viewedRun && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                const t = viewedRun.task;
                setViewingRunId(null);
                start(t);
              }}
            >
              <RotateCcw className="h-3.5 w-3.5" /> Re-run
            </Button>
          )}
        </div>
        <div className="scroll-thin flex-1 overflow-y-auto px-6 py-5">
          {viewedRunLoading ? (
            <p className="mt-12 text-center text-sm text-muted-foreground">Loading run…</p>
          ) : (
            <AgentTrace items={replayTrace} />
          )}
        </div>
      </main>
    );
  }

  return (
    <main className="flex h-full min-w-0 flex-1 flex-col">
      {/* Debug log panel */}
      {showDebug && (
        <div className="max-h-36 overflow-y-auto border-b border-border bg-black/90 px-3 py-2">
          <p className="mb-1 text-[10px] font-semibold text-green-400">DEBUG LOG</p>
          {debugLog.length === 0 ? (
            <p className="text-[10px] text-gray-500">No events yet.</p>
          ) : (
            debugLog.map((l, i) => (
              <p key={i} className="font-mono text-[10px] text-green-300/80">{l}</p>
            ))
          )}
        </div>
      )}

      {/* Task input */}
      <div className="border-b border-border px-6 py-4">
        <div className="flex items-end gap-2">
          <textarea
            id="task-input"
            value={task}
            onChange={(e) => setTask(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) start(task);
            }}
            rows={2}
            placeholder="Give the agent a research or action task… (Ctrl/⌘+Enter to run, / to focus)"
            className="min-h-13 flex-1 resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
          />
          {busy ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => abortRef.current?.abort()}
              title="Stop generating"
            >
              <Square className="h-3.5 w-3.5" /> Stop
            </Button>
          ) : (
            <Button onClick={() => start(task)} disabled={state === "awaiting" || !task.trim()}>
              <Play className="h-4 w-4" /> Run
            </Button>
          )}
        </div>
        {trace.length === 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {SAMPLES.map((s) => (
              <button
                key={s}
                onClick={() => { setTask(s); start(s); }}
                className="rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                {s.length > 60 ? s.slice(0, 60) + "…" : s}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Trace area */}
      <div className="scroll-thin flex-1 overflow-y-auto px-6 py-5">
        {trace.length === 0 ? (
          <div className="mx-auto mt-12 max-w-sm text-center">
            <Bot className="mx-auto mb-3 h-8 w-8 text-primary/60" />
            <h2 className="text-sm font-semibold">Give it a task</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              The agent plans, calls tools, observes results, and reflects on its
              own answer in a loop — its full reasoning streams here live. Any
              action that changes real state (like adding a task) pauses for
              your approval first.
            </p>
          </div>
        ) : (
          <AgentTrace items={trace} />
        )}

        {/* Retry button after error */}
        {state === "done" && hasError && lastTaskRef.current && (
          <div className="mt-4 flex justify-center">
            <Button variant="outline" onClick={retry} className="gap-2">
              <RotateCcw className="h-4 w-4" />
              Retry same task
            </Button>
          </div>
        )}
      </div>

      {/* Phase status bar — only while running */}
      {busy && phase && (
        <div className="border-t border-border bg-muted/30">
          {stepInfo && (
            <div className="h-0.5 w-full bg-border">
              <div
                className="h-full bg-primary transition-[width]"
                style={{ width: `${Math.min(100, (stepInfo.step / stepInfo.max) * 100)}%` }}
              />
            </div>
          )}
          <div className="flex items-center gap-2 px-6 py-2">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
            <span className="text-xs text-muted-foreground">{phase}</span>
            {stepInfo && (
              <span className="ml-1 text-xs text-muted-foreground/60">
                (step {stepInfo.step + 1}/{stepInfo.max})
              </span>
            )}
            {elapsedS !== null && (
              <span className="ml-auto text-xs tabular-nums text-muted-foreground/60">{elapsedS}s</span>
            )}
          </div>
        </div>
      )}

      {pending && state === "awaiting" && (
        <ApprovalModal action={pending} onDecision={decide} />
      )}
    </main>
  );
}
