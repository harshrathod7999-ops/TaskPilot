import { useEffect } from "react";
import { useStore } from "@/store";

function isTypingTarget(el: EventTarget | null): boolean {
  const node = el as HTMLElement | null;
  if (!node) return false;
  return (
    node.tagName === "INPUT" ||
    node.tagName === "TEXTAREA" ||
    node.isContentEditable
  );
}

/** Global shortcuts: "/" focuses the task composer, Escape exits run replay. */
export function useKeyboardShortcuts() {
  const setViewingRunId = useStore((s) => s.setViewingRunId);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && !isTypingTarget(e.target)) {
        e.preventDefault();
        document.getElementById("task-input")?.focus();
      } else if (e.key === "Escape") {
        setViewingRunId(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setViewingRunId]);
}
