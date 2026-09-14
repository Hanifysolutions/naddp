import * as React from 'react';
import type { Metadata, Viewport } from 'next';
import { Inter, Inter_Tight, JetBrains_Mono } from 'next/font/google';

import { Providers } from '@/app/providers';
import { cn } from '@/lib/utils';

import './globals.css';

/**
 * Three faces, each with one job (DESIGN_SYSTEM.md "Typography").
 *
 * Inter Tight is the display face: headings and every data figure. A serious
 * grotesque, tighter than Inter at large sizes, which is what stops a big figure
 * reading as a marketing statistic. Inter carries body and labels. JetBrains Mono
 * is reserved for identifiers, trace ids and anything scanned character by
 * character - never for a data LABEL, which DESIGN_SYSTEM.md lists as an
 * anti-goal.
 *
 * All three are self-hosted by next/font, so there is no third-party font request
 * at runtime - which is also what lets the CSP keep `font-src 'self' data:`.
 */
const interTight = Inter_Tight({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-display',
});

const inter = Inter({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-sans',
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-mono',
});

export const metadata: Metadata = {
  title: {
    default: 'NADDP - Nigeria-Australia Digital Diplomacy Platform',
    template: '%s | NADDP',
  },
  description:
    'Demonstration environment for the Nigeria-Australia Digital Diplomacy Platform. All data shown is synthetic.',
  applicationName: 'NADDP',
  // This is a demonstration environment containing synthetic records. It must never be
  // indexed, cached by a search engine, or surfaced as if it were a live mission system.
  robots: {
    index: false,
    follow: false,
    nocache: true,
    googleBot: { index: false, follow: false, noimageindex: true },
  },
  referrer: 'strict-origin-when-cross-origin',
  formatDetection: { telephone: false, address: false, email: false },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  // Never block zoom: WCAG 2.2 SC 1.4.4 (Resize Text).
  maximumScale: 5,
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: 'hsl(220 23% 97%)' },
    { media: '(prefers-color-scheme: dark)', color: 'hsl(222 38% 11%)' },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>): React.JSX.Element {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={cn(
          'min-h-screen bg-background font-sans text-foreground',
          interTight.variable,
          inter.variable,
          jetbrainsMono.variable,
        )}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
