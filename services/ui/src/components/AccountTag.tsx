import {
  ACCOUNT_COLOR_CLASSES,
  ACCOUNT_COLOR_LABELS,
  ACCOUNT_COLORS,
  ACCOUNT_PROVIDER_LABELS,
  asAccountColor,
  type AccountColor,
  type AccountProvider,
} from "./accountColors";
import { AzureIcon, CloudIcon, ClusterIcon, GcpIcon } from "./workspace-tree/icons";
import { cx } from "./ui";

/**
 * The provider mark, at glyph size and in `currentColor` — deliberately NOT in
 * the tree's brand colours. Inside a tag the colour channel is already spoken
 * for (it identifies the account), so a second colour next to the dot would
 * compete with it. Shape says which cloud, colour says which account.
 */
const PROVIDER_GLYPH: Record<AccountProvider, (c: string) => JSX.Element> = {
  aws: (c) => <CloudIcon className={c} />,
  azure: (c) => <AzureIcon className={c} />,
  gcp: (c) => <GcpIcon className={c} />,
  k8s: (c) => <ClusterIcon className={c} />,
};

/**
 * A cloud account rendered as `● ☁ Account-Name`, with the raw id in the
 * tooltip.
 *
 * The name carries the meaning and the dot carries the colour: colour is never
 * the only channel, so the row still reads for colourblind users and in
 * grayscale print. The 12-digit id moves to `title` because nobody recognises
 * an account by its number.
 *
 * `provider` adds the second channel. A display name is only unique *within* a
 * provider table, so an Azure subscription and an AWS account can both be named
 * "Dev-Account" — and because the API assigns colours BU-wide across all
 * four tables (`account_colors.pick_next`), those two are guaranteed to get
 * DIFFERENT colours. Without the glyph that reads as one account rendering
 * inconsistently, which is exactly how it was reported. Optional so a caller
 * that genuinely has no provider (an unresolved account) can omit it.
 */
export function AccountTag({
  color,
  name,
  id,
  provider,
  className,
}: {
  color: string | null | undefined;
  name: string;
  /** Natural id (12-digit AWS account, subscription/project id, cluster PK). */
  id?: string;
  /** Which cloud — renders the disambiguating glyph. */
  provider?: AccountProvider;
  className?: string;
}) {
  const token = asAccountColor(color);
  const providerLabel = provider ? ACCOUNT_PROVIDER_LABELS[provider] : null;
  return (
    <span
      className={cx("inline-flex items-center gap-1.5", className)}
      title={[name, id, providerLabel].filter(Boolean).join(" · ")}
    >
      <span
        aria-hidden
        className={cx("h-2 w-2 shrink-0 rounded-full", ACCOUNT_COLOR_CLASSES[token].solid)}
      />
      {provider && (
        <>
          {PROVIDER_GLYPH[provider]("h-3 w-3 shrink-0 opacity-70")}
          {/* The glyph is aria-hidden; keep the provider in the a11y tree so a
              screen reader hears "Dev-Account, Azure subscription" and
              gets the same disambiguation a sighted user does. */}
          <span className="sr-only">{providerLabel}</span>
        </>
      )}
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
