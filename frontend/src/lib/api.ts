// Typed client for the TaskPilot backend. In dev, Vite proxies /api -> :8001.
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

export interface TaskItem {
  id: number;
  title: string;
  notes: string | null;
  due: string | null;
  status: string;
  created_at: string;
}

export interface PendingAction {
  tool: string;
  args: Record<string, unknown>;
  thought?: string;
  step?: number;
  maxSteps?: number;
  argsHint?: string | null;
  // Raw MCP JSON Schema for the tool's args, when available — powers the
  // approval dialog's per-field form. Falls back to a JSON textarea if absent.
  schema?: { properties?: Record<string, any>; required?: string[] } | null;
}

// A source the agent actually consulted, derived server-side from the run's
// scratchpad (never forced [n] citation markers — see graph.py _collect_sources).
export type Source =
  | { kind: "page"; url: string; title: string }
  | { kind: "search"; query: string };

// One agent-trace entry rendered in the timeline.
export type TraceItem =
  | { kind: "plan"; text: string; ts: number }
  | { kind: "thought"; text: string; step?: number; maxSteps?: number; ts: number }
  | { kind: "action"; tool: string; args: Record<string, unknown>; step?: number; maxSteps?: number; ts: number }
  | { kind: "observation"; tool: string; result: string; step?: number; ts: number }
  | { kind: "critique"; verdict: string; reason: string; ts: number }
  | { kind: "final"; text: string; sources?: Source[]; streaming?: boolean; ts: number }
  | { kind: "error"; message: string; ts: number }
  | { kind: "status"; phase: string; tool?: string; step?: number; maxSteps?: number };

export interface StreamHandlers {
  onEvent: (event: string, data: any) => void;
  onError?: (err: Error) => void;
}

// Shared SSE-over-POST reader (EventSource can't POST a body).
async function readSSE(
  path: string,
  body: unknown,
  handlers: StreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok || !res.body) throw new Error(`Request failed (${res.status})`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const frames = buf.split("\n\n");
      buf = frames.pop() ?? "";
      for (const frame of frames) {
        const ev = /event:\s*(.*)/.exec(frame)?.[1]?.trim();
        const dataLine = /data:\s*([\s\S]*)/.exec(frame)?.[1];
        if (!ev || dataLine == null) continue;
        handlers.onEvent(ev, JSON.parse(dataLine));
      }
    }
  } catch (err) {
    if ((err as Error).name !== "AbortError") handlers.onError?.(err as Error);
  }
}

export function runAgent(
  task: string,
  handlers: StreamHandlers,
  signal?: AbortSignal
) {
  return readSSE("/run", { task }, handlers, signal);
}

export function resumeAgent(
  threadId: string,
  approved: boolean,
  editedArgs: Record<string, unknown> | null,
  handlers: StreamHandlers,
  signal?: AbortSignal
) {
  return readSSE(
    "/resume",
    { thread_id: threadId, approved, edited_args: editedArgs },
    handlers,
    signal
  );
}

export async function listTasks(): Promise<TaskItem[]> {
  const res = await fetch(`${BASE}/tasks`);
  if (!res.ok) throw new Error(`tasks failed (${res.status})`);
  return res.json();
}

// Run history — the backend persists every trace event as it streams, so a
// finished (or paused, even across a restart) run can be listed and replayed.
export type RunStatus = "running" | "paused" | "done" | "error" | "stopped";

export interface RunSummary {
  id: string;
  task: string;
  status: RunStatus;
  created_at: string;
  elapsed_s: number | null;
  final_preview: string | null;
}

export interface RunEvent {
  event: string;
  data: any;
  ts: string;
}

export interface RunDetail extends RunSummary {
  events: RunEvent[];
}

export async function listRuns(limit = 50): Promise<RunSummary[]> {
  const res = await fetch(`${BASE}/runs?limit=${limit}`);
  if (!res.ok) throw new Error(`runs failed (${res.status})`);
  return res.json();
}

export async function getRun(id: string): Promise<RunDetail> {
  const res = await fetch(`${BASE}/runs/${id}`);
  if (!res.ok) throw new Error(`run fetch failed (${res.status})`);
  return res.json();
}

export async function deleteRun(id: string): Promise<void> {
  const res = await fetch(`${BASE}/runs/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`run delete failed (${res.status})`);
}

export async function health(): Promise<{ tools_ok: boolean; tools: string[] }> {
  const res = await fetch(`${BASE}/health`);
  return res.json();
}
