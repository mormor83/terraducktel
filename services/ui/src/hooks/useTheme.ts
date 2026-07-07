import { useEffect, useState } from "react";

export type Theme = "light" | "dark" | "system";

const STORAGE_KEY = "terraducktel_theme";

function resolve(t: Theme): "light" | "dark" {
  if (t === "system") {
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  return t;
}

function apply(t: Theme): void {
  const r = resolve(t);
  const root = document.documentElement;
  // Tailwind's darkMode is ["class", '[data-theme="dark"]'] — the custom
  // selector means dark: variants fire on the data-theme attribute, NOT the
  // `.dark` class. Set both: the attribute drives Tailwind utilities + the
  // index.css body rule, the class covers any plain `.dark` CSS.
  if (r === "dark") {
    root.dataset.theme = "dark";
    root.classList.add("dark");
  } else {
    root.dataset.theme = "light";
    root.classList.remove("dark");
  }
}

/** Read the saved theme (or 'dark' default) and apply BEFORE first render. */
export function bootstrapTheme(): void {
  const t = (localStorage.getItem(STORAGE_KEY) as Theme | null) ?? "dark";
  apply(t);
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() => {
    return (localStorage.getItem(STORAGE_KEY) as Theme | null) ?? "dark";
  });

  useEffect(() => {
    apply(theme);
    localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  // Re-apply on system preference change while in 'system' mode.
  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => apply("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  return { theme, setTheme: setThemeState, resolved: resolve(theme) } as const;
}
