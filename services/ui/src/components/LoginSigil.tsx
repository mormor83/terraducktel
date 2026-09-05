/**
 * LoginSigil — animated brand centrepiece for the login page.
 *
 * Layers (back → front): breathing radial bloom · three live SVG rings that
 * counter-rotate at different speeds (with orbiting nodes) · the raster sigil
 * · an occasional energy sweep masked to the artwork · a staggered wordmark
 * with a periodic sheen.
 *
 * Constraints honoured here:
 *  - Pure CSS/SVG animation, `transform`/`opacity` only (blur + mask are
 *    static, so every animation composites on the GPU; no rAF, no canvas).
 *  - `prefers-reduced-motion: reduce` freezes everything into a static,
 *    fully-legible lockup (letters/tagline forced visible, sweeps removed).
 *  - Theme-aware via local `--tdls-*` vars flipped under `.dark` /
 *    `[data-theme="dark"]` (matches tailwind.config darkMode selectors):
 *    dark mode leans on arcane lime, light mode substitutes brand teal so
 *    the glow doesn't read radioactive on an off-white ground.
 *
 * All keyframes/vars are namespaced `tdls-` and live in this file so the
 * component owns its styling end-to-end (no edits to index.css/tailwind).
 */

import type { CSSProperties } from "react";

const SIGIL_URL = "/td/brand/terraducktel-sigil.png?v=2026-09-01";
const WORDMARK = "TerraDuckTel";
// "Duck" gets the accent color (lime in dark, teal in light).
const ACCENT_START = 5;
const ACCENT_END = 8;

/** Point on a circle centred at (500,500) in the 1000×1000 viewBox. */
function polar(r: number, deg: number): { x: number; y: number } {
  const rad = (deg * Math.PI) / 180;
  return { x: 500 + r * Math.cos(rad), y: 500 + r * Math.sin(rad) };
}

/** Small diamond (rotated square) path centred at a polar position. */
function diamond(r: number, deg: number, size: number): string {
  const { x, y } = polar(r, deg);
  return `M${x} ${y - size} L${x + size} ${y} L${x} ${y + size} L${x - size} ${y} Z`;
}

const CSS = `
.tdls-root{
  /* Light theme: teal-led, low alpha — lime reads radioactive on off-white. */
  --tdls-ring-a: rgba(31,111,108,.45);
  --tdls-ring-b: rgba(47,138,133,.55);
  --tdls-ring-c: rgba(31,111,108,.40);
  --tdls-node: #2f8a85;
  --tdls-node-glow: rgba(47,138,133,.55);
  --tdls-glow-core: rgba(126,208,120,.20);
  --tdls-glow-mid: rgba(31,111,108,.13);
  --tdls-sweep: rgba(182,255,75,.30);
  --tdls-textsweep-c: rgba(182,255,75,.16);
  --tdls-accent-text: #1f6f6c;
  --tdls-word: #14201d;
  --tdls-tagline: #6b7368;
  --tdls-art-shadow: drop-shadow(0 12px 28px rgba(14,31,29,.20));
}
.dark .tdls-root,[data-theme="dark"] .tdls-root{
  /* Dark theme: arcane lime takes over as the energy color. */
  --tdls-ring-a: rgba(86,170,163,.42);
  --tdls-ring-b: rgba(182,255,75,.42);
  --tdls-ring-c: rgba(86,170,163,.38);
  --tdls-node: #b6ff4b;
  --tdls-node-glow: rgba(182,255,75,.85);
  --tdls-glow-core: rgba(182,255,75,.21);
  --tdls-glow-mid: rgba(31,111,108,.34);
  --tdls-sweep: rgba(182,255,75,.42);
  --tdls-textsweep-c: rgba(182,255,75,.22);
  --tdls-accent-text: #b6ff4b;
  --tdls-word: #e2f2d9;
  --tdls-tagline: #6e857a;
  --tdls-art-shadow: drop-shadow(0 0 26px rgba(182,255,75,.12));
}

@keyframes tdls-rot     { to { transform: rotate(360deg); } }
@keyframes tdls-rot-rev { to { transform: rotate(-360deg); } }
@keyframes tdls-float   { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-9px); } }
@keyframes tdls-breathe { 0%,100% { transform: scale(.94); opacity:.55; } 50% { transform: scale(1.05); opacity:1; } }
@keyframes tdls-rise    { from { opacity:0; transform: translateY(.55em); } to { opacity:1; transform: translateY(0); } }
@keyframes tdls-fadeup  { from { opacity:0; transform: translateY(6px); } to { opacity:1; transform: translateY(0); } }
/* Sweep crosses in ~1.8s, then rests for the remainder of the 9s cycle. */
@keyframes tdls-sweep-x {
  0%   { transform: translateX(0) rotate(14deg);    opacity:0; }
  4%   { opacity:1; }
  16%  { opacity:1; }
  20%  { transform: translateX(420%) rotate(14deg); opacity:0; }
  100% { transform: translateX(420%) rotate(14deg); opacity:0; }
}
@keyframes tdls-textsweep {
  0%   { transform: translateX(-110%) skewX(-18deg); opacity:0; }
  5%   { opacity:.9; }
  16%  { opacity:.9; }
  21%  { transform: translateX(680%) skewX(-18deg);  opacity:0; }
  100% { transform: translateX(680%) skewX(-18deg);  opacity:0; }
}

.tdls-float{ animation: tdls-float 6.5s ease-in-out infinite; }
.tdls-glow{
  position:absolute; inset:12%; border-radius:9999px; pointer-events:none;
  background: radial-gradient(closest-side, var(--tdls-glow-core), var(--tdls-glow-mid) 55%, transparent 100%);
  filter: blur(34px);
  animation: tdls-breathe 5.4s ease-in-out infinite;
}
.tdls-ring-a, .tdls-ring-b, .tdls-ring-c, .tdls-orbit-a, .tdls-orbit-b{
  transform-box: view-box; transform-origin: 50% 50%;
}
.tdls-ring-a  { animation: tdls-rot      80s linear infinite; }  /* slow outer drift */
.tdls-ring-b  { animation: tdls-rot-rev  46s linear infinite; }  /* counter-rotating arcs */
.tdls-ring-c  { animation: tdls-rot     150s linear infinite; }  /* near-still dotted ring */
.tdls-orbit-a { animation: tdls-rot      80s linear infinite; }
.tdls-orbit-b { animation: tdls-rot-rev  46s linear infinite; }
.tdls-art{ filter: var(--tdls-art-shadow); }
.tdls-sweepwrap{
  position:absolute; inset:9%; overflow:hidden; pointer-events:none;
  -webkit-mask-image:url("${SIGIL_URL}"); mask-image:url("${SIGIL_URL}");
  -webkit-mask-size:contain; mask-size:contain;
  -webkit-mask-repeat:no-repeat; mask-repeat:no-repeat;
  -webkit-mask-position:center; mask-position:center;
}
.tdls-sweepstrip{
  position:absolute; top:-35%; bottom:-35%; left:-45%; width:38%; opacity:0;
  background: linear-gradient(100deg, transparent 0%, var(--tdls-sweep) 42%, rgba(255,255,255,.16) 50%, var(--tdls-sweep) 58%, transparent 100%);
  animation: tdls-sweep-x 9s linear infinite; animation-delay: 1.8s;
}
.tdls-word{ color: var(--tdls-word); letter-spacing:.06em; }
.tdls-letter{
  display:inline-block; opacity:0; transform: translateY(.55em);
  animation: tdls-rise .65s cubic-bezier(.22,.9,.32,1) forwards;
  animation-delay: calc(var(--i) * 55ms + 150ms);
}
.tdls-letter-accent{ color: var(--tdls-accent-text); }
.tdls-wordwrap{ position:relative; display:inline-block; overflow:hidden; }
.tdls-textsweep{
  position:absolute; top:0; bottom:0; left:0; width:18%; opacity:0; pointer-events:none;
  background: linear-gradient(90deg, transparent, var(--tdls-textsweep-c), transparent);
  mix-blend-mode: screen;
  animation: tdls-textsweep 7.5s linear infinite; animation-delay: 2.6s;
}
.tdls-flourish{ opacity:0; animation: tdls-fadeup .8s ease-out forwards; animation-delay: .9s; }
.tdls-tagline{
  color: var(--tdls-tagline); opacity:0;
  animation: tdls-fadeup .8s ease-out forwards; animation-delay: 1.05s;
}

@media (prefers-reduced-motion: reduce){
  .tdls-root, .tdls-root *{ animation: none !important; }
  .tdls-letter, .tdls-flourish, .tdls-tagline{ opacity:1; transform:none; }
  .tdls-sweepstrip, .tdls-textsweep{ display:none; }
  .tdls-glow{ opacity:.8; transform:none; }
}
`;

export default function LoginSigil({ className = "" }: { className?: string }) {
  const orbitB = [18, 141, 262].map((deg) => polar(448, deg));
  const orbitA = polar(482, 205);

  return (
    <div className={`tdls-root ${className}`}>
      <style>{CSS}</style>
      <div className="tdls-float relative aspect-square w-full">
        {/* Breathing bloom behind everything */}
        <div className="tdls-glow" aria-hidden="true" />

        {/* Live arcane geometry — sits behind the art so rings pass under
            the wing tips, extending the raster's own (static) ring work. */}
        <svg
          className="absolute inset-0 h-full w-full"
          viewBox="0 0 1000 1000"
          fill="none"
          aria-hidden="true"
        >
          {/* Faint continuous base circle grounds the dashed rings. */}
          <circle cx="500" cy="500" r="482" stroke="var(--tdls-ring-a)" strokeWidth="0.75" opacity="0.45" />
          <g className="tdls-ring-a">
            <circle
              cx="500" cy="500" r="482"
              stroke="var(--tdls-ring-a)" strokeWidth="2"
              strokeDasharray="2 10" strokeLinecap="round"
            />
            {/* Cardinal diamonds echo the raster's crosshair marks. */}
            {[0, 90, 180, 270].map((deg) => (
              <path key={deg} d={diamond(482, deg, 8)} fill="var(--tdls-node)" opacity="0.75" />
            ))}
          </g>
          <g className="tdls-ring-b">
            <circle
              cx="500" cy="500" r="448"
              stroke="var(--tdls-ring-b)" strokeWidth="2.5"
              strokeDasharray="160 90 12 90" strokeLinecap="round"
            />
          </g>
          <g className="tdls-ring-c">
            <circle
              cx="500" cy="500" r="414"
              stroke="var(--tdls-ring-c)" strokeWidth="3"
              strokeDasharray="1 18" strokeLinecap="round"
            />
          </g>
          {/* Orbiting nodes riding rings A and B. */}
          <g className="tdls-orbit-b" style={{ filter: "drop-shadow(0 0 6px var(--tdls-node-glow))" }}>
            {orbitB.map(({ x, y }, i) => (
              <circle key={i} cx={x} cy={y} r="5" fill="var(--tdls-node)" />
            ))}
          </g>
          <g className="tdls-orbit-a" style={{ filter: "drop-shadow(0 0 5px var(--tdls-node-glow))" }}>
            <circle cx={orbitA.x} cy={orbitA.y} r="4" fill="var(--tdls-node)" opacity="0.9" />
          </g>
        </svg>

        {/* The sigil itself. Decorative — the wordmark below is real text. */}
        <img
          src={SIGIL_URL}
          alt=""
          aria-hidden="true"
          className="tdls-art absolute inset-[9%] h-[82%] w-[82%] object-contain"
        />

        {/* Occasional energy sweep, masked to the artwork's pixels. */}
        <div className="tdls-sweepwrap" aria-hidden="true">
          <div className="tdls-sweepstrip" />
        </div>
      </div>

      {/* Wordmark — real text (animatable), replacing the baked-in lockup. */}
      <div className="mt-5 text-center md:mt-6">
        <span className="tdls-wordwrap">
          <h1 className="tdls-word font-display text-[clamp(1.5rem,5.5vw,2.75rem)] font-bold leading-none">
            {WORDMARK.split("").map((ch, i) => (
              <span
                key={i}
                className={
                  i >= ACCENT_START && i <= ACCENT_END
                    ? "tdls-letter tdls-letter-accent"
                    : "tdls-letter"
                }
                style={{ "--i": i } as CSSProperties}
              >
                {ch}
              </span>
            ))}
          </h1>
          <span className="tdls-textsweep" aria-hidden="true" />
        </span>
        <div className="tdls-flourish mx-auto mt-3 flex max-w-[240px] items-center gap-2" aria-hidden="true">
          <span
            className="h-px flex-1"
            style={{ background: "linear-gradient(to right, transparent, var(--tdls-ring-b))" }}
          />
          <span
            className="h-1.5 w-1.5 rotate-45"
            style={{ background: "var(--tdls-node)" }}
          />
          <span
            className="h-px flex-1"
            style={{ background: "linear-gradient(to left, transparent, var(--tdls-ring-b))" }}
          />
        </div>
        <p className="tdls-tagline mt-3 font-mono text-[10px] uppercase tracking-[0.28em] md:text-[11px]">
          Automated Infrastructure &amp; Terraform
        </p>
      </div>
    </div>
  );
}
