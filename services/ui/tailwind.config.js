/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class", '[data-theme="dark"]'],
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#e8f4f3",
          100: "#c5e3df",
          200: "#8fc8c2",
          300: "#56aaa3",
          400: "#2f8a85",
          500: "#1f6f6c",
          600: "#185a59",
          700: "#134847",
          800: "#0e3636",
          900: "#0a2424",
          // Semantic neutrals map to CSS vars that flip per theme: light
          // values on :root, dark under [data-theme="dark"]/html.dark — both
          // in src/index.css. Fallbacks match the light values.
          bg: "var(--td-bg, #f4f7f0)",
          surface: "var(--td-surface, #ffffff)",
          surface2: "var(--td-surface-2, #eaf0e4)",
          border: "var(--td-border, rgba(31,111,108,0.16))",
          borderStrong: "var(--td-border-strong, rgba(31,111,108,0.28))",
          ink: "var(--td-ink, #0e1f1d)",
          text: "var(--td-text, #0f1f1c)",
          textSoft: "var(--td-text-soft, #3c4f47)",
          muted: "var(--td-muted, #667a6e)",
        },
        accent: {
          50: "#effaee",
          100: "#d4f2d2",
          200: "#a9e3a6",
          300: "#7ed078",
          400: "#5eb85a",
          500: "#3f9b3e",
          600: "#2f7c34",
          700: "#245d2a",
        },
        // Alias Tailwind's default `sky` palette to TerraDuckTel brand teal so
        // every legacy `sky-*` utility renders in the brand color without a
        // codebase-wide rename. New code should prefer `brand-*`.
        sky: {
          50: "#e8f4f3",
          100: "#c5e3df",
          200: "#8fc8c2",
          300: "#56aaa3",
          400: "#2f8a85",
          500: "#1f6f6c",
          600: "#185a59",
          700: "#134847",
          800: "#0e3636",
          900: "#0a2424",
          950: "#061818",
        },
        // Fantasy-tech retheme: alias Tailwind's default `slate` palette to
        // the brand's neutral ramp so the ~950 hardcoded `slate-*` utilities
        // across ~25 page/component files retone centrally, exactly like the
        // `sky` → brand-teal alias above (gray/zinc are unused in this
        // codebase, so only slate is aliased).
        //
        // The 700–950 end is the design's green-black strata (raise / inset /
        // panel / ground). Those are theme-CONSTANT — dark mode paints them as
        // surfaces, light mode as ink — so they stay literal hexes.
        //
        // The 50–600 end is whatever each theme calls "neutral", and the two
        // themes genuinely disagree: on a green-black ground a minty green
        // reads as neutral, on white it reads as *green*. v2 first shipped the
        // dark values to both themes, which is the palette bug this ramp now
        // fixes — light mode grew mint chips (`bg-slate-100` #e2f2d9), sage
        // hairlines, and sub-AA meta text (`text-slate-400` #8ba396 measured
        // 2.70:1 on white, 2.33:1 on --td-surface-2). So the whole 50–600 end
        // is driven from per-theme vars, the way 500 already was; dark keeps
        // its exact previous hexes, light gets a faintly green-tinted grey
        // (hue 152, same family as --td-muted) with 400 lifted to AA. The
        // <alpha-value> form keeps `slate-200/70`, `from-slate-500/10` etc.
        // composing.
        //
        // Theme-FLIPPING *accents* (shell chrome, accents-as-text, status
        // inks/washes) do NOT live here — they route through the --td-* vars
        // in src/index.css (light on :root, dark under
        // [data-theme="dark"]/html.dark), which App.tsx and components/ui.tsx
        // reference via var(--td-…) arbitrary values.
        slate: {
          // Per-theme neutrals — values in src/index.css. Light: 50–200 are
          // pale near-greys (row washes, chip fills, hairlines), 300 a soft
          // border, 400/500/600 secondary→strong ink (400 = 4.58:1 on white,
          // so the bare `text-slate-400` meta labels pass AA). Dark: the
          // v2 mint/sage values, unchanged.
          50: "rgb(var(--td-slate-50-rgb) / <alpha-value>)",
          100: "rgb(var(--td-slate-100-rgb) / <alpha-value>)",
          200: "rgb(var(--td-slate-200-rgb) / <alpha-value>)",
          300: "rgb(var(--td-slate-300-rgb) / <alpha-value>)",
          400: "rgb(var(--td-slate-400-rgb) / <alpha-value>)",
          500: "rgb(var(--td-slate-500-rgb) / <alpha-value>)",
          600: "rgb(var(--td-slate-600-rgb) / <alpha-value>)",
          // Theme-constant strata (see comment above).
          700: "#1d292b",
          800: "#182224",
          900: "#131c1e",
          950: "#0e1416",
        },
        // Status palettes, aliased for the same reason as `slate` above: the
        // design pins danger/warn/add to specific hues (--danger #e05c45,
        // --warn #e0a93b, --add #7ed078, --info #4fb3c4) but ~700 existing
        // utilities reach for Tailwind's stock red/amber/emerald/blue, whose
        // defaults are visibly off-palette (stock red-500 #ef4444 is a colder,
        // harder red; stock emerald #10b981 is teal-green where the design's
        // add is yellow-green). Each ramp below holds the design hue constant
        // and varies lightness, anchored so the shade the design names lands
        // on its canonical value. Dark-mode code reads the 300/400 end for
        // text and the 900/950 end for tinted fills, so both ends matter.
        red: {
          50: "#fdeeeb", 100: "#fbdcd6", 200: "#f5b8ac", 300: "#ee9483",
          400: "#e77a64", 500: "#e05c45", 600: "#c44a35", 700: "#9c3a2a",
          800: "#6b281d", 900: "#3f1913", 950: "#26100c",
        },
        amber: {
          50: "#fdf6e8", 100: "#faecc9", 200: "#f3d894", 300: "#ebc463",
          400: "#e5b64d", 500: "#e0a93b", 600: "#c08d2c", 700: "#946c22",
          800: "#654a18", 900: "#3c2c0f", 950: "#241a09",
        },
        emerald: {
          50: "#eefaed", 100: "#d9f4d7", 200: "#b3e7ae", 300: "#7ed078",
          400: "#63bd5e", 500: "#4aa347", 600: "#3a8237", 700: "#2c6229",
          800: "#1d411b", 900: "#12280f", 950: "#0a1808",
        },
        blue: {
          50: "#edf8fa", 100: "#d4eff3", 200: "#a6dde6", 300: "#7fcdd9",
          400: "#63c0cf", 500: "#4fb3c4", 600: "#3d95a4", 700: "#2e727e",
          800: "#204e56", 900: "#142f34", 950: "#0c1c1f",
        },
        // NOTE: `violet` (36 uses, mostly Badge's violet tone) is deliberately
        // NOT aliased — the design has no violet anywhere, so there is nothing
        // to anchor a ramp to. Those few spots stay off-palette until a
        // designer assigns them a hue; inventing one here would be a guess.

        // Fantasy-tech accent: arcane lime (--td-lime). DEFAULT/400 is the
        // canonical #b6ff4b; 300 is a hover-lighten, 500 a pressed-darken.
        // Other lime-* shades keep Tailwind defaults (extend deep-merges).
        lime: {
          DEFAULT: "#b6ff4b",
          300: "#c8ff6e",
          400: "#b6ff4b",
          500: "#9ae62f",
        },
      },
      fontFamily: {
        sans: ["Inter", "Helvetica Neue", "Helvetica", "Arial", "system-ui", "sans-serif"],
        // Display serif for the dark fantasy-tech theme (--disp). Loaded from
        // Google Fonts in index.html (wght 500/600/700).
        display: ["Cinzel", "Georgia", "serif"],
        mono: ["JetBrains Mono", "Fira Code", "ui-monospace", "Menlo", "monospace"],
      },
      borderRadius: {
        xs: "4px",
        sm: "6px",
        md: "8px",
        lg: "14px", // --r-lg (was 12px; purely visual, nothing measures it)
        xl: "16px",
      },
      boxShadow: {
        "td-sm": "0 1px 2px rgba(14, 31, 29, 0.06)",
        "td-md": "0 4px 12px rgba(14, 31, 29, 0.08), 0 1px 3px rgba(14, 31, 29, 0.04)",
        "td-lg": "0 12px 32px rgba(14, 31, 29, 0.12), 0 2px 6px rgba(14, 31, 29, 0.06)",
        // Arcane lime focus/emphasis glow (--glow).
        glow: "0 0 0 1px rgba(182,255,75,.28),0 0 22px -6px rgba(182,255,75,.35)",
      },
    },
  },
  plugins: [],
};
