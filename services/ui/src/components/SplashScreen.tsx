/**
 * SplashScreen — the boot curtain.
 *
 * A full-viewport summoning-circle overlay shown while the app comes up: the
 * same animated sigil the login page uses (`LoginSigil`, so there is exactly
 * one copy of that artwork/animation), over the theme's ground with the
 * login's ember gradients, plus a hairline progress bar that fills across the
 * dwell. It then fades out and unmounts.
 *
 * Deliberate constraints:
 *  - `pointer-events: none` on the overlay. It never intercepts a click — the
 *    app behind it stays hit-testable the whole time (this is also what keeps
 *    Playwright's actionability checks green). Dismissal is wired to *window*
 *    listeners instead, so any click or keypress skips the rest of the dwell.
 *  - `aria-hidden` — it is decorative and transient, and the real content is
 *    already mounted underneath. This also keeps LoginSigil's <h1> out of the
 *    accessibility tree, so it can't collide with the login card's heading.
 *  - `prefers-reduced-motion: reduce` → the bar is drawn full, the scale-out
 *    is dropped (the opacity fade stays; it isn't vestibular motion) and the
 *    dwell shortens.
 *  - Escape hatch: `localStorage.terraducktel_splash = "off"` skips it
 *    entirely, for anyone who reloads the console all day.
 *
 * Keyframes/vars are namespaced `tdsp-` and live in this file, matching
 * LoginSigil's "component owns its styling" pattern (no index.css edits).
 */

import { useEffect, useState } from "react";
import LoginSigil from "./LoginSigil";

/** How long the curtain holds before it starts leaving. */
const DWELL_MS = 1500;
const DWELL_REDUCED_MS = 700;
/** Must match the transition duration in CSS below. */
const FADE_MS = 520;

const STORAGE_KEY = "terraducktel_splash";

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Opt-out switch — `localStorage.terraducktel_splash = "off"`. */
function splashDisabled(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "off";
  } catch {
    // Private mode / storage blocked — show it, it's only 1.5s.
    return false;
  }
}

const CSS = `
.tdsp-root{
  position:fixed; inset:0; z-index:100;
  display:flex; flex-direction:column; align-items:center; justify-content:center; gap:26px;
  padding:24px;
  pointer-events:none;
  opacity:1; transform:scale(1);
  transition:opacity ${FADE_MS}ms ease, transform ${FADE_MS}ms cubic-bezier(.22,1,.36,1);
  background:
    radial-gradient(1100px 600px at 78% 8%, rgba(var(--td-glow-rgb), .10), transparent 55%),
    radial-gradient(900px 520px at 12% 92%, rgba(var(--td-edge-rgb), .16), transparent 60%),
    var(--td-bg, #0e1416);
}
.tdsp-leaving{ opacity:0; transform:scale(1.045); }

/* Hairline "channeling" bar — fills once across the dwell. */
.tdsp-bar{
  position:relative; height:2px; width:min(240px, 46vw);
  border-radius:2px; overflow:hidden;
  background:rgba(var(--td-edge-rgb), .18);
}
.tdsp-bar > span{
  position:absolute; top:0; bottom:0; left:0; width:0; border-radius:inherit;
  background:linear-gradient(90deg, rgba(var(--td-edge-rgb), .55), var(--td-accent-ink, #b6ff4b));
  box-shadow:0 0 12px -2px var(--td-accent-ink, #b6ff4b);
  animation:tdsp-fill ${DWELL_MS}ms cubic-bezier(.4,0,.2,1) forwards;
}
@keyframes tdsp-fill{ from{ width:0; } to{ width:100%; } }

@media (prefers-reduced-motion: reduce){
  .tdsp-leaving{ transform:none; }
  .tdsp-bar > span{ width:100%; animation:none; }
}
`;

type Phase = "visible" | "leaving" | "done";

export default function SplashScreen() {
  const [phase, setPhase] = useState<Phase>(() => (splashDisabled() ? "done" : "visible"));

  // Hold, then leave — early on any click/keypress.
  useEffect(() => {
    if (phase !== "visible") return;
    const leave = () => setPhase((p) => (p === "visible" ? "leaving" : p));
    const timer = window.setTimeout(leave, prefersReducedMotion() ? DWELL_REDUCED_MS : DWELL_MS);
    window.addEventListener("pointerdown", leave);
    window.addEventListener("keydown", leave);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("pointerdown", leave);
      window.removeEventListener("keydown", leave);
    };
    // Runs once: `phase` only ever moves forward, and the leaving/done
    // transitions are owned by the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Unmount once the fade has played out.
  useEffect(() => {
    if (phase !== "leaving") return;
    const timer = window.setTimeout(() => setPhase("done"), FADE_MS);
    return () => window.clearTimeout(timer);
  }, [phase]);

  if (phase === "done") return null;

  return (
    <div className={`tdsp-root${phase === "leaving" ? " tdsp-leaving" : ""}`} aria-hidden="true">
      <style>{CSS}</style>
      {/* Same responsive widths as the login hero — the Cinzel wordmark is
          sized in vw and needs that much room per breakpoint or it wraps. */}
      <LoginSigil className="w-full max-w-[220px] sm:max-w-[260px] md:max-w-[420px]" />
      <div className="tdsp-bar">
        <span />
      </div>
    </div>
  );
}
