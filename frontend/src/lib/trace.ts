import type { TraceItem } from "./api";

/**
 * Maps one backend SSE/stored event to a renderable trace item. Shared by
 * the live console (streaming) and run replay (reading persisted events),
 * so the timeline looks identical whether you're watching it happen or
 * looking back at history.
 *
 * Returns null for events that don't render as a trace entry — `status`,
 * `approval_required`, `paused`, and `done` are state transitions the caller
 * handles separately (phase label, pending approval, run status).
 */
export function eventToTraceItem(event: string, data: any, ts: number): TraceItem | null {
  switch (event) {
    case "plan":
      return { kind: "plan", text: data.text, ts };
    case "thought":
      return { kind: "thought", text: data.text, step: data.step, maxSteps: data.max_steps, ts };
    case "action":
      return {
        kind: "action",
        tool: data.tool,
        args: data.args ?? {},
        step: data.step,
        maxSteps: data.max_steps,
        ts,
      };
    case "observation":
      return { kind: "observation", tool: data.tool, result: data.result, step: data.step, ts };
    case "critique":
      return { kind: "critique", verdict: data.verdict, reason: data.reason, ts };
    case "final":
      return { kind: "final", text: data.text, sources: data.sources, ts };
    case "error":
      return { kind: "error", message: data.message, ts };
    default:
      return null;
  }
}
