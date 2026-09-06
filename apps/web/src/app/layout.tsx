import * as React from 'react';
import type { Metadata, Viewport } from 'next';
import { Inter, JetBrains_Mono } from 'next/font/google';

import { Providers } from '@/app/providers';
import { cn } from '@/lib/utils';

import './globals.css';

/**
 * Inter for the interface, JetBrains Mono for identifiers, trace ids and any place a
 * number must be scanned character by character. Both are self-hosted by next/font, so
 * there is no third-party font request at runtime - which is also what lets the CSP keep
 * `font-src 'self' data:`.
 */
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
    { media: '(prefers-color-scheme: light)', color: 'hsl(210 30% 97%)' },
    { media: '(prefers-color-scheme: dark)', color: 'hsl(215 42% 8%)' },
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
          inter.variable,
          jetbrainsMono.variable,
        )}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
