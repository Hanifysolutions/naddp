'use client';

import * as React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { TooltipProvider } from '@/components/ui/tooltip';

/**
 * Query defaults, chosen for a live demo rather than a busy production console:
 *
 *  staleTime 30s          A demo runs for ten minutes on seeded data. Refetching every
 *                         mount would add latency to a screen the Ambassador is looking
 *                         at, for data that cannot have changed.
 *  retry 1                One retry absorbs a cold API container; more than that turns a
 *                         genuine failure into a long silent hang. Errors must surface,
 *                         because a dead end we can see is safer than one we cannot.
 *  refetchOnWindowFocus   Off. Alt-tabbing to the slide deck and back must not reflow
 *                         the screen mid-sentence.
 *  refetchOnReconnect     On. Recovering from conference wifi should heal the screen.
 */
function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        retry: 1,
        refetchOnWindowFocus: false,
        refetchOnReconnect: true,
      },
      mutations: {
        // Never silently retry something consequential. Every mutation in this product
        // is a state transition that writes an audit event.
        retry: 0,
      },
    },
  });
}

export function Providers({ children }: { children: React.ReactNode }): React.JSX.Element {
  // Created inside state so each browser session gets exactly one client, and so the
  // client is never shared across requests during server rendering.
  const [queryClient] = React.useState(makeQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      {/*
        delayDuration 250ms keeps a dense rail from flickering tooltips as the pointer
        crosses it; skipDelayDuration lets a deliberate scan along the rail feel instant.
      */}
      <TooltipProvider delayDuration={250} skipDelayDuration={400}>
        {children}
      </TooltipProvider>
    </QueryClientProvider>
  );
}
