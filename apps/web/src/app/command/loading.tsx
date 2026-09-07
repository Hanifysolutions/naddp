import * as React from 'react';

import { CommandTile } from '@/components/command/command-tile';
import { buildBoard, COMMAND_GRID_CLASS } from '@/lib/command-view';
import { Skeleton } from '@/components/ui/skeleton';

/**
 * Route-level loading UI, shown while the layout resolves the demo identity server-side.
 *
 * It renders the identical grid from the identical tile definitions, in the `loading`
 * state, by asking `buildBoard` for exactly that. Because the skeleton and the real board
 * share one source of layout, there is no reflow when data arrives - the tiles do not move,
 * they only fill.
 *
 * This sits inside `command/layout.tsx`, so the DEMO badge, the rail and the identity
 * control stay on screen throughout.
 */
export default function CommandLoading(): React.JSX.Element {
  const tiles = buildBoard({ kind: 'loading' });

  return (
    <div className="px-4 py-4 laptop:px-6 laptop:py-6" aria-busy="true">
      <header className="mb-4">
        <Skeleton className="h-6 w-52" />
        <Skeleton className="mt-2 h-4 w-96 max-w-full" />
        <span className="sr-only" role="status">
          Loading the command centre
        </span>
      </header>

      <div className={COMMAND_GRID_CLASS}>
        {tiles.map((tile) => (
          <CommandTile key={tile.definition.id} view={tile} />
        ))}
      </div>
    </div>
  );
}
