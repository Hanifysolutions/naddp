import * as React from 'react';
import Link from 'next/link';
import { Inbox } from 'lucide-react';

import { DemoBadge } from '@/components/layout/demo-badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

/**
 * 404.
 *
 * Note what this page does *not* say: it does not distinguish "this route does not
 * exist" from "you are not permitted to see it". Under deny-by-default RBAC that
 * distinction is itself information, and the API returns the same answer for both. The
 * UI matches.
 */
export default function NotFound(): React.JSX.Element {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-6">
      <Card className="w-full max-w-lg">
        <CardHeader className="gap-2">
          <div className="flex items-center justify-between gap-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Inbox aria-hidden="true" className="h-5 w-5 text-muted-foreground" />
              Nothing here
            </CardTitle>
            <DemoBadge />
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm leading-relaxed text-muted-foreground">
            That address does not resolve to anything available to you. If you expected a
            screen here, check the demo identity in the top bar - destinations are
            generated from the permissions your role holds.
          </p>
          <Button asChild>
            <Link href="/command">Back to command centre</Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
