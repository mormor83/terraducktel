import { useTheme } from "../hooks/useTheme";

/** Compact light/dark switch for the app header. Reuses the shared useTheme
 *  hook (Settings exposes the full light/dark/system control). */
export default function ThemeToggle() {
  const { setTheme, resolved } = useTheme();
  const isDark = resolved === "dark";

  return (
    <button
      type="button"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      title={isDark ? "Switch to light" : "Switch to dark"}
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      className="grid h-8 w-8 place-items-center rounded-md border border-brand-border bg-brand-surface text-brand-textSoft transition-colors hover:border-brand-borderStrong hover:text-brand-text dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-300 dark:hover:text-slate-100"
    >
      {isDark ? (
        // sun
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      ) : (
        // moon
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
        </svg>
      )}
    </button>
  );
}
