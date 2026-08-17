import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Theme = "light" | "dark" | "system";

interface AppState {
  theme: Theme;
  setTheme: (t: Theme) => void;

  // Persisted UI prefs only — run history lives server-side (the backend
  // already owns the authoritative event stream + resumable checkpoints).
  showDebug: boolean;
  setShowDebug: (v: boolean | ((prev: boolean) => boolean)) => void;

  // Tasks sidebar visibility on small screens (rendered as a Sheet).
  tasksOpen: boolean;
  setTasksOpen: (open: boolean) => void;

  // When set, the console shows a static replay of a past run instead of
  // the live composer. Set/cleared by RunsMenu + the console's "Back" action.
  viewingRunId: string | null;
  setViewingRunId: (id: string | null) => void;
}

export const useStore = create<AppState>()(
  persist(
    (set) => ({
      theme: "system",
      setTheme: (t) => set({ theme: t }),

      showDebug: false,
      setShowDebug: (v) =>
        set((s) => ({ showDebug: typeof v === "function" ? v(s.showDebug) : v })),

      tasksOpen: false,
      setTasksOpen: (open) => set({ tasksOpen: open }),

      viewingRunId: null,
      setViewingRunId: (id) => set({ viewingRunId: id }),
    }),
    {
      name: "taskpilot-app",
      version: 1,
      partialize: (s) => ({ theme: s.theme, showDebug: s.showDebug }),
    }
  )
);
