/**
 * Tailwind config for the standalone auth portal (portal/pages.py).
 *
 * The portal is server-rendered Python — Tailwind scans pages.py for class
 * names and compiles a purged stylesheet. The COMPILED output is committed at
 * mapcontrol_server/static/portal.css so neither the Docker image nor local
 * dev needs Node (ADR-0001 dual-deployability).
 *
 * Rebuild after editing pages.py or portal.tw.css (from server/):
 *
 *   npx tailwindcss@3.4.17 -c tailwind.config.js \
 *     -i portal.tw.css -o mapcontrol_server/static/portal.css --minify
 *
 * Design tokens follow the shadcn/ui convention (hsl triplet CSS variables)
 * mapped to the ESIP Federation palette: deep ocean-navy surfaces, ESIP blue
 * primary, ESIP leaf-green accents. Light theme via prefers-color-scheme.
 */
module.exports = {
  content: ["mapcontrol_server/portal/pages.py"],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border) / <alpha-value>)",
        input: "hsl(var(--input) / <alpha-value>)",
        ring: "hsl(var(--ring) / <alpha-value>)",
        background: "hsl(var(--background) / <alpha-value>)",
        foreground: "hsl(var(--foreground) / <alpha-value>)",
        primary: {
          DEFAULT: "hsl(var(--primary) / <alpha-value>)",
          foreground: "hsl(var(--primary-foreground) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "hsl(var(--accent) / <alpha-value>)",
          foreground: "hsl(var(--accent-foreground) / <alpha-value>)",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive) / <alpha-value>)",
          foreground: "hsl(var(--destructive-foreground) / <alpha-value>)",
        },
        muted: {
          DEFAULT: "hsl(var(--muted) / <alpha-value>)",
          foreground: "hsl(var(--muted-foreground) / <alpha-value>)",
        },
        card: {
          DEFAULT: "hsl(var(--card) / <alpha-value>)",
          foreground: "hsl(var(--card-foreground) / <alpha-value>)",
        },
      },
      borderRadius: {
        lg: "0.625rem",
        xl: "0.875rem",
        "2xl": "1.125rem",
      },
      fontFamily: {
        sans: [
          "-apple-system", "BlinkMacSystemFont", "SF Pro Text", "Inter",
          "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif",
        ],
        mono: [
          "SF Mono", "ui-monospace", "SFMono-Regular", "Menlo", "Monaco",
          "Cascadia Mono", "Consolas", "monospace",
        ],
      },
    },
  },
  plugins: [],
};
