import { Toaster as Sonner, type ToasterProps } from "sonner";
import { useStore } from "@/store";

// Theme-aware sonner Toaster wired to the app's zustand theme
// (the stock shadcn version depends on next-themes, which we don't use).
export function Toaster(props: ToasterProps) {
  const theme = useStore((s) => s.theme);
  return (
    <Sonner
      theme={theme}
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-popover group-[.toaster]:text-popover-foreground group-[.toaster]:border-border group-[.toaster]:shadow-lg",
          description: "group-[.toast]:text-muted-foreground",
        },
      }}
      {...props}
    />
  );
}
