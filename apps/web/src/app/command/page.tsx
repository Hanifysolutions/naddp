import * as React from 'react';
import type { Metadata } from 'next';

import { CommandTile } from '@/components/command/command-tile';
import { COMMAND_GRID_CLASS, COMMAND_TILES } from '@/components/command/tiles';

export const metadata: Metadata = {
  title: 'Command centre',
  description:
    'Executive overview of mission intelligence, opportunities, citizen services, relationships, diaspora capability and outcomes. Synthetic demonstration data.',
};

/**
 * The executive command centre.
 *
 * Week 1 deliberately ships this board *structured but empty*. Every tile declares the
 * questions it answers and the figures it will carry; not one of those figures is
 * invented. The seeded API reads are wired by a later track, at which point each tile
 * moves from `empty` to `ready` with real seed counts.
 *
 * This is not a placeholder in the lorem-ipsum sense - it is the finished layout in its
 * honest state. An Ambassador demo that showed fabricated numbers, even once, would cost
 * more trust than an empty board ever could.
 */
export default function CommandPage(): React.JSX.Element {
  return (
    <div className="px-4 py-4 laptop:px-6 laptop:py-6">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold leading-tight tracking-tight text-foreground">
            Command centre
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            One governed picture of the mission. Every figure here is role-scoped by the
            API before it is returned.
          </p>
        </div>
      </header>

      <section aria-label="Executive tiles" className={COMMAND_GRID_CLASS}>
        {COMMAND_TILES.map((tile) => (
          <CommandTile
            key={tile.id}
            title={tile.title}
            description={tile.description}
            metrics={tile.metrics}
            state="empty"
            className={tile.span}
          />
        ))}
      </section>
    </div>
  );
}
