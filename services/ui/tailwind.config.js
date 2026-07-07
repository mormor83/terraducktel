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
          // Semantic neutrals map to CSS vars so they flip in dark mode
          // (see index.css [data-theme="dark"]). Fallbacks match the light values.
          bg: "var(--td-bg, #f6f7f4)",
          surface: "var(--td-surface, #ffffff)",
          surface2: "var(--td-surface-2, #f0f2ee)",
          border: "var(--td-border, #dfe3dc)",
          borderStrong: "var(--td-border-strong, #b9bfb4)",
          ink: "var(--td-ink, #0e1f1d)",
          text: "var(--td-text, #14201d)",
          textSoft: "var(--td-text-soft, #3a4641)",
          muted: "var(--td-muted, #6b7368)",
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
      },
      fontFamily: {
        sans: ["Inter", "Helvetica Neue", "Helvetica", "Arial", "system-ui", "sans-serif"],
        display: ["Space Grotesk", "Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "ui-monospace", "Menlo", "monospace"],
      },
      borderRadius: {
        xs: "4px",
        sm: "6px",
        md: "8px",
        lg: "12px",
        xl: "16px",
      },
      boxShadow: {
        "td-sm": "0 1px 2px rgba(14, 31, 29, 0.06)",
        "td-md": "0 4px 12px rgba(14, 31, 29, 0.08), 0 1px 3px rgba(14, 31, 29, 0.04)",
        "td-lg": "0 12px 32px rgba(14, 31, 29, 0.12), 0 2px 6px rgba(14, 31, 29, 0.06)",
      },
    },
  },
  plugins: [],
};
