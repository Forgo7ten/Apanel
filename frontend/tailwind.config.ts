import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      spacing: {
        page: "var(--space-page)",
        card: "var(--space-card)",
      },
      fontSize: {
        title: ["var(--font-size-title)", { lineHeight: "1.4" }],
      },
      colors: {
        app: "rgb(var(--color-app) / <alpha-value>)",
        panel: "rgb(var(--color-panel) / <alpha-value>)",
        card: "rgb(var(--color-card) / <alpha-value>)",
        line: "rgb(var(--color-line) / <alpha-value>)",
        primary: "rgb(var(--color-primary) / <alpha-value>)",
        secondary: "rgb(var(--color-secondary) / <alpha-value>)",
        muted: "rgb(var(--color-muted) / <alpha-value>)",
        brand: "rgb(var(--color-brand) / <alpha-value>)",
        positive: "rgb(var(--color-positive) / <alpha-value>)",
        warning: "rgb(var(--color-warning) / <alpha-value>)",
        negative: "rgb(var(--color-negative) / <alpha-value>)",
        neutral: "rgb(var(--color-neutral) / <alpha-value>)",
      },
      borderRadius: {
        panel: "8px",
      },
      boxShadow: {
        panel: "0 12px 32px rgb(0 0 0 / 0.18)",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Monaco", "Consolas", "Liberation Mono", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
