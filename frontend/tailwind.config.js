/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        elevated: "var(--elevated)",
        line: "var(--border)",
        ink: "var(--text)",
        muted: "var(--text-secondary)",
        accent: "var(--accent)",
        healthy: "var(--healthy)",
        warning: "var(--warning)",
        critical: "var(--critical)",
        info: "var(--info)",
        purple: "var(--purple)",
      },
      fontFamily: {
        sans: ["Segoe UI", "IBM Plex Sans", "sans-serif"],
        mono: ["Cascadia Code", "IBM Plex Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
