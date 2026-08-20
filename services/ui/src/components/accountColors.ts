/**
 * Cloud-account colour palette — the frontend half of
 * `services/api/app/services/account_colors.py`. Token names and ORDER must
 * match that file.
 *
 * Class strings are written out literally rather than composed (`bg-${hue}-500`)
 * because Tailwind's scanner only emits classes it can see in the source. Each
 * token carries a light/dark pair so a colour stays legible in both themes —
 * the reason accounts pick a token here instead of a raw hex.
 *
 * Note: no teal. Tailwind's `sky` is aliased to brand teal in this repo (see
 * docs/claude/design.md), so a teal account colour would read as chrome rather
 * than as data.
 */
export type AccountColor =
  | "red"
  | "orange"
  | "yellow"
  | "green"
  | "blue"
  | "purple"
  | "brown"
  | "gray";

export const ACCOUNT_COLORS: AccountColor[] = [
  "red",
  "orange",
  "yellow",
  "green",
  "blue",
  "purple",
  "brown",
  "gray",
];

type ColorClasses = {
  /** Vertical rail down the left of a Runs row, and the dot on the mono line. */
  solid: string;
  /** Filled swatch for the Settings picker (no dark variant — it's the colour itself). */
  swatch: string;
  /** Tinted pill, for previewing the colour next to an account name. */
  chip: string;
};

export const ACCOUNT_COLOR_CLASSES: Record<AccountColor, ColorClasses> = {
  red: {
    solid: "bg-red-500 dark:bg-red-400",
    swatch: "bg-red-500",
    chip: "bg-red-50 text-red-700 ring-red-300/60 dark:bg-red-900/40 dark:text-red-300 dark:ring-red-700/40",
  },
  orange: {
    solid: "bg-orange-500 dark:bg-orange-400",
    swatch: "bg-orange-500",
    chip: "bg-orange-50 text-orange-700 ring-orange-300/60 dark:bg-orange-900/40 dark:text-orange-300 dark:ring-orange-700/40",
  },
  yellow: {
    solid: "bg-yellow-500 dark:bg-yellow-400",
    swatch: "bg-yellow-500",
    chip: "bg-yellow-50 text-yellow-800 ring-yellow-300/60 dark:bg-yellow-900/40 dark:text-yellow-300 dark:ring-yellow-700/40",
  },
  green: {
    solid: "bg-emerald-500 dark:bg-emerald-400",
    swatch: "bg-emerald-500",
    chip: "bg-emerald-50 text-emerald-700 ring-emerald-300/60 dark:bg-emerald-900/40 dark:text-emerald-300 dark:ring-emerald-700/40",
  },
  blue: {
    solid: "bg-blue-500 dark:bg-blue-400",
    swatch: "bg-blue-500",
    chip: "bg-blue-50 text-blue-700 ring-blue-300/60 dark:bg-blue-900/40 dark:text-blue-300 dark:ring-blue-700/40",
  },
  purple: {
    solid: "bg-violet-500 dark:bg-violet-400",
    swatch: "bg-violet-500",
    chip: "bg-violet-50 text-violet-700 ring-violet-300/60 dark:bg-violet-900/40 dark:text-violet-300 dark:ring-violet-700/40",
  },
  brown: {
    solid: "bg-amber-700 dark:bg-amber-600",
    swatch: "bg-amber-700",
    chip: "bg-amber-50 text-amber-800 ring-amber-400/60 dark:bg-amber-900/40 dark:text-amber-300 dark:ring-amber-700/40",
  },
  gray: {
    solid: "bg-slate-400 dark:bg-slate-500",
    swatch: "bg-slate-400",
    chip: "bg-slate-100 text-slate-700 ring-slate-300/80 dark:bg-slate-800/80 dark:text-slate-300 dark:ring-slate-700/50",
  },
};

/** Human label for the picker's tooltip / aria-label. */
export const ACCOUNT_COLOR_LABELS: Record<AccountColor, string> = {
  red: "Red",
  orange: "Orange",
  yellow: "Yellow",
  green: "Green",
  blue: "Blue",
  purple: "Purple",
  brown: "Brown",
  gray: "Gray",
};

/**
 * Narrow an API `color_effective` string to a token.
 *
 * The API always sends a valid one; this guards against an older API (before
 * migration 041) omitting the field, in which case everything renders gray
 * rather than crashing on an undefined class lookup.
 */
export function asAccountColor(value: string | null | undefined): AccountColor {
  return value && value in ACCOUNT_COLOR_CLASSES ? (value as AccountColor) : "gray";
}

export function accountRailClass(value: string | null | undefined): string {
  return ACCOUNT_COLOR_CLASSES[asAccountColor(value)].solid;
}
