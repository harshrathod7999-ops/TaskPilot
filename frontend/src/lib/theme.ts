import { useEffect } from "react";
import { useStore, type Theme } from "@/store";

function apply(theme: Theme) {
  const dark =
    theme === "dark" ||
    (theme === "system" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

// Keeps <html class="dark"> in sync with the persisted theme choice,
// including live OS theme changes while in "system" mode.
export function useApplyTheme() {
  const theme = useStore((s) => s.theme);

  useEffect(() => {
    apply(theme);
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => apply("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);
}
