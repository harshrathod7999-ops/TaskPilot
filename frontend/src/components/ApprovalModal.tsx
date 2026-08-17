import { useState } from "react";
import { ShieldAlert, Check, X } from "lucide-react";
import type { PendingAction } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
} from "@/components/ui/alert-dialog";

type FieldType = "string" | "number" | "boolean" | "json";

function fieldType(propSchema: any): FieldType {
  switch (propSchema?.type) {
    case "number":
    case "integer":
      return "number";
    case "boolean":
      return "boolean";
    case "string":
      return "string";
    default:
      return "json";
  }
}

/**
 * The human-in-the-loop gate: the agent wants to run a WRITE tool and is
 * paused. Deliberately built on AlertDialog with no Esc/overlay dismissal —
 * an approval must be an explicit Approve or Reject, not an accidental
 * click-away. When the tool's MCP JSON Schema is available, arguments are
 * edited field-by-field (typed inputs); otherwise a JSON textarea is the
 * fallback (e.g. for a hypothetical future local write tool with no schema).
 */
export function ApprovalModal({
  action,
  onDecision,
}: {
  action: PendingAction;
  onDecision: (approved: boolean, editedArgs: Record<string, unknown> | null) => void;
}) {
  const props = action.schema?.properties;
  const required = new Set(action.schema?.required ?? []);
  const hasStructuredSchema = !!props && Object.keys(props).length > 0;

  const [fields, setFields] = useState<Record<string, unknown>>({ ...action.args });
  const [argsText, setArgsText] = useState(() => JSON.stringify(action.args, null, 2));
  const [argsError, setArgsError] = useState<string | null>(null);

  const setField = (key: string, value: unknown) => setFields((f) => ({ ...f, [key]: value }));

  function approve() {
    if (hasStructuredSchema) {
      onDecision(true, fields);
      return;
    }
    try {
      onDecision(true, JSON.parse(argsText));
    } catch {
      setArgsError("Arguments must be valid JSON.");
    }
  }

  return (
    <AlertDialog open>
      <AlertDialogContent className="max-w-lg">
        <AlertDialogHeader>
          <div className="flex items-center gap-2">
            <ShieldAlert className="h-5 w-5 shrink-0 text-amber-500" />
            <AlertDialogTitle>Approval required</AlertDialogTitle>
          </div>
          <AlertDialogDescription asChild>
            <div className="space-y-3 pt-1 text-left text-foreground">
              <p className="text-sm text-muted-foreground">
                The agent wants to run a <strong className="text-foreground">write action</strong> —
                review (and optionally edit) the arguments before allowing it.
              </p>

              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary" className="font-mono">{action.tool}</Badge>
                {action.step !== undefined && action.maxSteps !== undefined && (
                  <span className="text-xs text-muted-foreground">
                    step {action.step + 1}/{action.maxSteps}
                  </span>
                )}
              </div>

              {action.thought && (
                <p className="rounded-md bg-muted/60 px-3 py-2 text-xs italic text-muted-foreground">
                  “{action.thought}”
                </p>
              )}

              {hasStructuredSchema ? (
                <div className="space-y-3">
                  {Object.entries(props!).map(([key, propSchema]) => {
                    const type = fieldType(propSchema);
                    const value = fields[key];
                    const label = required.has(key) ? `${key} *` : key;
                    const description = (propSchema as any)?.description as string | undefined;

                    return (
                      <div key={key}>
                        <label className="mb-1 flex items-baseline justify-between text-xs font-medium text-muted-foreground">
                          <span>{label}</span>
                          {description && <span className="font-normal text-muted-foreground/70">{description}</span>}
                        </label>
                        {type === "boolean" && (
                          <input
                            type="checkbox"
                            checked={!!value}
                            onChange={(e) => setField(key, e.target.checked)}
                            className="h-4 w-4"
                          />
                        )}
                        {type === "number" && (
                          <input
                            type="number"
                            value={value === undefined || value === null ? "" : String(value)}
                            onChange={(e) =>
                              setField(key, e.target.value === "" ? "" : Number(e.target.value))
                            }
                            className="w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-sm outline-none focus:ring-2 focus:ring-ring"
                          />
                        )}
                        {type === "string" && (
                          <textarea
                            value={value === undefined || value === null ? "" : String(value)}
                            onChange={(e) => setField(key, e.target.value)}
                            rows={String(value ?? "").length > 60 ? 3 : 1}
                            className="w-full resize-y rounded-md border border-input bg-background px-2.5 py-1.5 text-sm outline-none focus:ring-2 focus:ring-ring"
                          />
                        )}
                        {type === "json" && (
                          <textarea
                            defaultValue={JSON.stringify(value ?? null, null, 2)}
                            onBlur={(e) => {
                              try {
                                setField(key, JSON.parse(e.target.value));
                              } catch {
                                /* leave the field's last valid value in place */
                              }
                            }}
                            rows={3}
                            className="w-full rounded-md border border-input bg-background px-2.5 py-1.5 font-mono text-xs outline-none focus:ring-2 focus:ring-ring"
                          />
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div>
                  <p className="mb-1 text-xs font-medium text-muted-foreground">Arguments (JSON)</p>
                  <textarea
                    value={argsText}
                    onChange={(e) => {
                      setArgsText(e.target.value);
                      setArgsError(null);
                    }}
                    rows={6}
                    className="w-full rounded-md border border-input bg-background p-2 font-mono text-xs outline-none focus:ring-2 focus:ring-ring"
                  />
                  {argsError && <p className="mt-1 text-xs text-destructive">{argsError}</p>}
                </div>
              )}
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <Button variant="outline" onClick={() => onDecision(false, null)}>
            <X className="h-4 w-4" /> Reject
          </Button>
          <Button onClick={approve}>
            <Check className="h-4 w-4" /> Approve &amp; run
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
