import {
  ACCOUNT_COLOR_CLASSES,
  ACCOUNT_COLOR_LABELS,
  ACCOUNT_COLORS,
  asAccountColor,
  type AccountColor,
} from "./accountColors";
import { cx } from "./ui";

/**
 * A cloud account rendered as `● Account-Name`, with the raw id in the tooltip.
 *
 * The name carries the meaning and the dot carries the colour: colour is never
 * the only channel, so the row still reads for colourblind users and in
 * grayscale print. The 12-digit id moves to `title` because nobody recognises
 * an account by its number.
 */
export function AccountTag({
  color,
  name,
  id,
  className,
}: {
  color: string | null | undefined;
  name: string;
  /** Natural id (12-digit AWS account, subscription/project id, cluster PK). */
  id?: string;
  className?: string;
}) {
  const token = asAccountColor(color);
  return (
    <span
      className={cx("inline-flex items-center gap-1.5", className)}
      title={id ? `${name} · ${id}` : name}
    >
      <span
        aria-hidden
        className={cx("h-2 w-2 shrink-0 rounded-full", ACCOUNT_COLOR_CLASSES[token].solid)}
      />
      <span className="truncate">{name}</span>
    </span>
  );
}

/**
 * The 3px colour rail down the left of a Runs row. Rendered as a sibling
 * absolute element rather than a `border-l` so it butts flush against the card
 * edge and doesn't shift the row's content by a pixel when absent.
 */
export function AccountRail({ color }: { color: string | null | undefined }) {
  return (
    <span
      aria-hidden
      className={cx(
        "absolute inset-y-0 left-0 w-[3px]",
        ACCOUNT_COLOR_CLASSES[asAccountColor(color)].solid,
      )}
    />
  );
}

/**
 * Swatch radio-group for Settings. Eight fixed choices — see accountColors.ts
 * for why this isn't an `<input type="color">`.
 */
export function AccountColorPicker({
  value,
  onChange,
  disabled,
}: {
  value: string | null | undefined;
  onChange: (color: AccountColor) => void;
  disabled?: boolean;
}) {
  const selected = asAccountColor(value);
  return (
    <div className="flex flex-wrap items-center gap-1.5" role="radiogroup" aria-label="Account color">
      {ACCOUNT_COLORS.map((c) => {
        const isOn = c === selected;
        return (
          <button
            key={c}
            type="button"
            role="radio"
            aria-checked={isOn}
            aria-label={ACCOUNT_COLOR_LABELS[c]}
            title={ACCOUNT_COLOR_LABELS[c]}
            disabled={disabled}
            onClick={() => onChange(c)}
            className={cx(
              "h-6 w-6 rounded-full transition focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1",
              ACCOUNT_COLOR_CLASSES[c].swatch,
              // A ring rather than a checkmark: at 24px a glyph on a saturated
              // fill is unreadable in half the palette.
              isOn
                ? "ring-2 ring-slate-900 ring-offset-2 ring-offset-white dark:ring-white dark:ring-offset-slate-900"
                : "opacity-70 hover:opacity-100",
              disabled && "cursor-not-allowed opacity-40",
            )}
          />
        );
      })}
    </div>
  );
}
