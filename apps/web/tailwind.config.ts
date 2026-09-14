import type { Config } from 'tailwindcss';
import animate from 'tailwindcss-animate';

/**
 * NADDP design system - Tailwind 3.4 bound to the shadcn/ui CSS-variable theme.
 *
 * Every colour resolves through `hsl(var(--token))`, so the palette is swapped by
 * toggling the `.dark` class on <html> and never by conditional class names in
 * components. See `src/app/globals.css` for the token values and their measured
 * WCAG 2.2 contrast ratios.
 */
const config = {
  darkMode: ['class'],
  content: [
    './src/**/*.{ts,tsx}',
    './src/app/**/*.{ts,tsx}',
    './src/components/**/*.{ts,tsx}',
  ],
  theme: {
    container: {
      center: true,
      padding: {
        DEFAULT: '1rem',
        sm: '1.25rem',
        lg: '1.5rem',
        '2xl': '2rem',
      },
      screens: {
        '2xl': '1600px',
      },
    },
    extend: {
      screens: {
        // The two rehearsal targets from BUILD_BIBLE §11, named so layout intent is
        // readable at the call site. Both are declared in `extend`, so they sort after
        // the built-in screens in the generated CSS - which is what makes `desk:` win
        // over `xl:` above 1920px.
        laptop: '1440px',
        desk: '1920px',
      },
      colors: {
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',

        /* ---- Mission Slate scale (DESIGN_SYSTEM.md v1.0) ----------------
         * Exposed as utilities so a component can name the institutional
         * colour directly (`bg-slate-900` for a command surface) instead of
         * routing everything through a shadcn semantic alias that was named
         * for a different design system. See globals.css for the values, the
         * measured contrast, and the two AA departures.
         */
        ink: 'hsl(var(--ink))',
        slate: {
          900: 'hsl(var(--slate-900))',
          700: 'hsl(var(--slate-700))',
          400: 'hsl(var(--slate-400))',
          300: 'hsl(var(--slate-300))',
        },
        paper: 'hsl(var(--paper))',
        surface: 'hsl(var(--surface))',
        line: 'hsl(var(--line))',
        ok: 'hsl(var(--ok))',
        warn: {
          DEFAULT: 'hsl(var(--warn))',
          ink: 'hsl(var(--warn-ink))',
        },
        risk: 'hsl(var(--risk))',
        /* The Q-17 honesty colour. An AI-proposed item is rendered in this
         * muted violet so the gap between proposed and evidenced is carried
         * chromatically as well as numerically. */
        proposed: {
          DEFAULT: 'hsl(var(--proposed))',
          weak: 'hsl(var(--proposed-weak))',
        },
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        success: {
          DEFAULT: 'hsl(var(--success))',
          foreground: 'hsl(var(--success-foreground))',
        },
        warning: {
          DEFAULT: 'hsl(var(--warning))',
          foreground: 'hsl(var(--warning-foreground))',
        },
        demo: {
          DEFAULT: 'hsl(var(--demo))',
          foreground: 'hsl(var(--demo-foreground))',
          border: 'hsl(var(--demo-border))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          weak: 'hsl(var(--accent-weak))',
          'on-dark': 'hsl(var(--accent-on-dark))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        chart: {
          '1': 'hsl(var(--chart-1))',
          '2': 'hsl(var(--chart-2))',
          '3': 'hsl(var(--chart-3))',
          '4': 'hsl(var(--chart-4))',
          '5': 'hsl(var(--chart-5))',
        },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        // The display face: a serious grotesque with tabular figures, used for
        // headings and for every data figure (DESIGN_SYSTEM.md "Typography").
        // Applied to h1-h4 globally in globals.css, so most components inherit
        // it without naming it.
        display: [
          'var(--font-display)',
          'var(--font-sans)',
          'ui-sans-serif',
          'sans-serif',
        ],
        mono: ['var(--font-mono)', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        // A denser type ramp than Tailwind's default; a command centre reads better
        // with tight leading and a small, consistent step.
        '2xs': ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.02em' }],
        // DESIGN_SYSTEM.md major-third scale (1.25): 12.8 / 16 / 20 / 25 / 31 / 39 / 49.
        // Named by role rather than by size so a figure is chosen for what it IS.
        label: ['0.8rem', { lineHeight: '1.15rem' }],
        'figure-sm': ['1.5625rem', { lineHeight: '1.9rem', letterSpacing: '-0.015em' }],
        figure: ['1.9375rem', { lineHeight: '2.25rem', letterSpacing: '-0.018em' }],
        'figure-lg': ['2.4375rem', { lineHeight: '2.75rem', letterSpacing: '-0.02em' }],
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
      },
    },
  },
  plugins: [animate],
} satisfies Config;

export default config;
