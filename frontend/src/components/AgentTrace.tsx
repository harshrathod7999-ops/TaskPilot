import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Brain,
  ListChecks,
  Wrench,
  Eye,
  Gavel,
  CheckCircle2,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Copy,
  Check,
  FileText,
  Search,
} from "lucide-react";
import type { Source, TraceItem } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const META: Record<string, { icon: typeof Brain; label: string; color: string }> = {
  plan:        { icon: ListChecks,    label: "Plan",         color: "text-indigo-500" },
  thought:     { icon: Brain,         label: "Thought",      color: "text-sky-500" },
  action:      { icon: Wrench,        label: "Action",       color: "text-amber-500" },
  observation: { icon: Eye,           label: "Observation",  color: "text-emerald-600" },
  critique:    { icon: Gavel,         label: "Critic",       color: "text-purple-500" },
  final:       { icon: CheckCircle2,  label: "Final answer", color: "text-emerald-600" },
  error:       { icon: AlertTriangle, label: "Error",        color: "text-red-500" },
};

const TOOL_COLORS: Record<string, string> = {
  web_search:   "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  read_url:     "bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300",
  add_task:     "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
  list_tasks:   "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  complete_task:"bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
};

function ToolBadge({ tool }: { tool: string }) {
  const cls = TOOL_COLORS[tool] ?? "bg-muted text-muted-foreground";
  return (
    <span className={cn("rounded px-1.5 py-0.5 text-xs font-mono font-medium", cls)}>
      {tool}
    </span>
  );
}

function StepPill({ step, maxSteps }: { step: number; maxSteps: number }) {
  return (
    <span className="ml-1.5 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
      step {step + 1}/{maxSteps}
    </span>
  );
}

function Timestamp({ ts }: { ts: number }) {
  const d = new Date(ts);
  const hms = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return <span className="ml-auto text-[10px] text-muted-foreground/60 tabular-nums">{hms}</span>;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }
  return (
    <button
      onClick={copy}
      title="Copy answer"
      className="ml-2 shrink-0 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

const OBS_PREVIEW = 280;

function CollapsibleObs({ result }: { result: string }) {
  const [open, setOpen] = useState(false);
  const isError = result.startsWith("ERROR");
  const preview = result.slice(0, OBS_PREVIEW);
  const hasMore = result.length > OBS_PREVIEW;
  return (
    <div className={cn(
      "rounded-md p-2 text-xs",
      isError
        ? "border border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950/30 dark:text-red-400"
        : "border border-border bg-muted/40 text-muted-foreground"
    )}>
      <pre className="whitespace-pre-wrap font-mono leading-relaxed">
        {open ? result : preview}
        {!open && hasMore && <span className="text-muted-foreground/50">…</span>}
      </pre>
      {hasMore && (
        <button
          onClick={() => setOpen((o) => !o)}
          className="mt-1 flex items-center gap-1 text-[10px] text-sky-600 hover:underline dark:text-sky-400"
        >
          {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          {open ? "Show less" : `Show all (${result.length.toLocaleString()} chars)`}
        </button>
      )}
    </div>
  );
}

function SourceChips({ sources }: { sources: Source[] }) {
  if (!sources.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {sources.map((s, i) =>
        s.kind === "page" ? (
          <a
            key={i}
            href={s.url}
            target="_blank"
            rel="noreferrer"
            title={s.url}
          >
            <Badge variant="secondary" className="gap-1 font-normal hover:bg-accent">
              <FileText className="h-3 w-3" />
              {s.title.length > 40 ? s.title.slice(0, 40) + "…" : s.title}
            </Badge>
          </a>
        ) : (
          <Badge
            key={i}
            variant="outline"
            className="gap-1 font-normal text-muted-foreground"
          >
            <Search className="h-3 w-3" />
            searched: {s.query.length > 40 ? s.query.slice(0, 40) + "…" : s.query}
          </Badge>
        )
      )}
    </div>
  );
}

function ArgsDisplay({ args }: { args: Record<string, unknown> }) {
  const entries = Object.entries(args);
  if (entries.length === 0) return <span className="text-muted-foreground text-xs">no args</span>;
  return (
    <span className="text-xs text-muted-foreground">
      {entries.map(([k, v], i) => (
        <span key={k}>
          {i > 0 && <span className="mx-1 text-border">·</span>}
          <span className="font-medium text-foreground/70">{k}=</span>
          <span className="font-mono">{String(v).slice(0, 80)}{String(v).length > 80 ? "…" : ""}</span>
        </span>
      ))}
    </span>
  );
}

function Body({ item }: { item: TraceItem }) {
  switch (item.kind) {
    case "plan":
      return <p className="whitespace-pre-wrap text-sm leading-relaxed">{item.text}</p>;
    case "thought":
      return <p className="whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground">{item.text}</p>;
    case "final":
      return (
        <div>
          <div className="flex items-start gap-1">
            <div className="prose-chat min-w-0 flex-1 text-sm leading-relaxed">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
              {item.streaming && (
                <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-foreground/60 align-text-bottom" />
              )}
            </div>
            {!item.streaming && <CopyButton text={item.text} />}
          </div>
          {item.sources && <SourceChips sources={item.sources} />}
        </div>
      );
    case "action":
      return (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <ToolBadge tool={item.tool} />
          <ArgsDisplay args={item.args} />
        </div>
      );
    case "observation":
      return <CollapsibleObs result={item.result} />;
    case "critique":
      return (
        <p className="text-sm">
          <span className={cn("font-semibold", item.verdict === "accept" ? "text-emerald-600" : "text-amber-600")}>
            {item.verdict === "accept" ? "✓ accept" : "⚑ revise"}
          </span>{" "}
          <span className="text-muted-foreground">— {item.reason}</span>
        </p>
      );
    case "error":
      return (
        <div>
          <p className="text-sm text-red-600 dark:text-red-400">{item.message}</p>
        </div>
      );
    default:
      return null;
  }
}

export function AgentTrace({ items }: { items: TraceItem[] }) {
  const visible = items.filter((i) => i.kind !== "status");
  return (
    <ol className="space-y-3">
      {visible.map((item, i) => {
        const meta = META[item.kind] ?? META.thought;
        const Icon = meta.icon;
        const step = "step" in item ? item.step : undefined;
        const maxSteps = "maxSteps" in item ? item.maxSteps : undefined;
        const ts = "ts" in item ? item.ts : undefined;
        return (
          <li key={i} className="flex gap-3">
            <div className="flex flex-col items-center">
              <Icon className={cn("h-5 w-5 shrink-0 mt-0.5", meta.color)} />
              {i < visible.length - 1 && <div className="mt-1 w-px flex-1 bg-border" />}
            </div>
            <div
              className={cn(
                "min-w-0 flex-1 rounded-lg border border-border px-3 py-2",
                item.kind === "final" && "border-emerald-500/40 bg-emerald-50/40 dark:bg-emerald-950/20",
                item.kind === "error" && "border-red-200 bg-red-50/40 dark:border-red-800 dark:bg-red-950/20"
              )}
            >
              <div className="mb-1 flex items-center gap-1">
                <p className={cn("text-xs font-semibold", meta.color)}>{meta.label}</p>
                {step !== undefined && maxSteps !== undefined && (
                  <StepPill step={step} maxSteps={maxSteps} />
                )}
                {ts !== undefined && <Timestamp ts={ts} />}
              </div>
              <Body item={item} />
            </div>
          </li>
        );
      })}
    </ol>
  );
}
