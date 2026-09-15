'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Lock, Menu } from 'lucide-react';

import { DemoBadge } from '@/components/layout/demo-badge';
import { NAV_ICONS } from '@/components/layout/nav-icons';
import { RolePicker } from '@/components/layout/role-picker';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetTrigger,
} from '@/components/ui/sheet';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { NavItem } from '@/lib/nav';
import { cn } from '@/lib/utils';

const MAIN_CONTENT_ID = 'main-content';

/*
 * Focus treatment for controls that sit ON a command surface.
 *
 * The global `:focus-visible` rule in globals.css rings in `--ring` (the teal accent) and
 * offsets in `--background` (paper). Both were chosen against the light work area, and
 * neither survives the move to --slate-900: the accent ring measures 2.31:1 there, under
 * the 3:1 SC 1.4.11 asks of a focus indicator, and the paper offset draws a bright halo
 * around every rail item. Controls on the bar and rail therefore re-point both at the
 * dark-surface pair - `--accent-on-dark` rings at 4.74:1 against --slate-900.
 *
 * It overrides rather than fights the global rule: these compile to `:focus-visible`
 * class selectors in the utilities layer, which the base-layer rule cannot outrank.
 */
const DARK_SURFACE_FOCUS =
  'focus-visible:ring-accent-on-dark focus-visible:ring-offset-slate-900';

export interface AppShellProps {
  /**
   * The rail's contents, derived from the permissions the API reports for this session
   * (see `navItemsForPermissions`). Never assembled per persona, and never assumed:
   * an empty array renders an explicit "no permissions resolved" state.
   */
  navItems: readonly NavItem[];
  /**
   * Identity control slot. Defaults to the real `RolePicker`, so no route can ship the
   * chrome without a way to see and change the demo identity.
   */
  rolePicker?: React.ReactNode;
  children: React.ReactNode;
}

/**
 * The persistent command-centre chrome.
 *
 * DESIGN_SYSTEM.md "Layout & structure": the top bar and the left rail are COMMAND
 * SURFACES and sit on --slate-900, dark and authoritative, framing a --paper work area.
 * That split is the whole point of the frame - it should read as an operations centre
 * surrounding the mission's work, not as a dark theme applied to an app. The boundary
 * between the two is a hairline in --ink, a seam on the dark side, never a shadow.
 *
 * Accessibility contract for this component:
 *  - a skip link is the first focusable element on every page (WCAG 2.4.1);
 *  - the top bar, rail and content are wrapped in `header` / `nav` / `main` landmarks,
 *    with the rail labelled so a screen reader can distinguish the two navigations;
 *  - the current page is marked with `aria-current="page"`, not by colour alone;
 *  - `<main>` is programmatically focusable so the skip link moves focus, not just
 *    scroll position;
 *  - every control has a visible focus ring - from the global `:focus-visible` rule in
 *    the work area, and from `DARK_SURFACE_FOCUS` above on the command surfaces.
 *
 * Text contrast on --slate-900, computed from the token values in globals.css:
 *    white / --surface        14.29:1   primary text, wordmark
 *    --slate-300               4.61:1   secondary text, resting nav items
 *    --accent-on-dark          4.70:1   the active nav item, 5.64:1 on its --ink seat
 *    --slate-400               4.11:1   NON-TEXT, plus the inactive planned entries
 */
export function AppShell({
  navItems,
  rolePicker,
  children,
}: AppShellProps): React.JSX.Element {
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false);
  const pathname = usePathname();

  const closeMobileNav = React.useCallback(() => {
    setMobileNavOpen(false);
  }, []);

  return (
    <div className="flex min-h-screen flex-col bg-background">
      {/*
        The skip link now lands on the dark bar, so it is drawn as a --surface chip rather
        than the app's primary (slate-900) fill, which would have been invisible against
        it. 14.29:1 against the bar behind it; behaviour and position are unchanged.
      */}
      <a
        href={`#${MAIN_CONTENT_ID}`}
        className={cn(
          'sr-only z-[100] rounded-md border border-line bg-surface px-4 py-2 text-sm font-medium text-ink',
          'focus:not-sr-only focus:absolute focus:left-3 focus:top-3',
          DARK_SURFACE_FOCUS,
        )}
      >
        Skip to main content
      </a>

      {/*
        Opaque, not translucent. A frosted bar over scrolling content is a SaaS tell, and
        it would make every ratio quoted above conditional on whatever happens to scroll
        underneath. A command surface states its own colour.
      */}
      <header className="sticky top-0 z-40 h-14 shrink-0 border-b border-ink bg-slate-900 text-white">
        <div className="flex h-full items-center gap-3 px-3 lg:px-4">
          {/* Rail trigger, shown only where the persistent rail is not. */}
          <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
            <SheetTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className={cn(
                  'text-slate-300 hover:bg-ink hover:text-white lg:hidden',
                  DARK_SURFACE_FOCUS,
                )}
              >
                <Menu aria-hidden="true" />
                <span className="sr-only">Open navigation</span>
              </Button>
            </SheetTrigger>
            {/*
              The drawer IS the rail below `lg`, so it is the same command surface and
              carries the same colours. The `[&>button…]` rules re-point the kit's close
              control, whose focus ring is offset in paper by default. The pseudo-class
              goes INSIDE the arbitrary selector: `[&>button]:focus:x` would compile to
              `.sheet:focus > button`, which keys the ring off the wrong element.
            */}
            <SheetContent
              side="left"
              className={cn(
                'w-72 border-ink bg-slate-900 p-0 text-white',
                '[&>button]:text-slate-300 [&>button:hover]:text-white',
                '[&>button:focus]:ring-accent-on-dark [&>button:focus]:ring-offset-slate-900',
              )}
            >
              <SheetHeader className="px-4 pb-2 pt-5">
                <SheetTitle className="text-white">Navigation</SheetTitle>
                <SheetDescription className="text-slate-300">
                  Destinations are generated from the permissions your current demo role
                  holds.
                </SheetDescription>
              </SheetHeader>
              <Separator className="bg-slate-700" />
              <NavList
                navItems={navItems}
                pathname={pathname}
                onNavigate={closeMobileNav}
                withTooltips={false}
                ariaLabel="Primary, drawer"
                idPrefix="drawer"
                className="px-2 py-3"
              />
            </SheetContent>
          </Sheet>

          <Wordmark />

          <div className="ml-auto flex items-center gap-3">
            <DemoBadge />
            <Separator orientation="vertical" className="hidden h-6 bg-slate-700 sm:block" />
            {rolePicker ?? <RolePicker />}
          </div>
        </div>
      </header>

      <div className="flex flex-1 items-stretch">
        <aside className="hidden w-[15rem] shrink-0 border-r border-ink bg-slate-900 lg:block">
          <div className="sticky top-14 h-[calc(100vh-3.5rem)]">
            <ScrollArea className="h-full">
              <NavList
                navItems={navItems}
                pathname={pathname}
                withTooltips
                ariaLabel="Primary"
                idPrefix="rail"
                className="px-2 py-3"
              />
            </ScrollArea>
          </div>
        </aside>

        <main
          id={MAIN_CONTENT_ID}
          tabIndex={-1}
          className="synthetic-watermark min-w-0 flex-1 focus:outline-none"
        >
          {children}
        </main>
      </div>
    </div>
  );
}

function Wordmark(): React.JSX.Element {
  return (
    <Link
      href="/command"
      className={cn(
        'flex items-center gap-2.5 rounded-md px-1 py-1 focus-visible:outline-none',
        DARK_SURFACE_FOCUS,
      )}
    >
      {/*
        The monogram takes the display face, not the mono one: monospace is reserved for
        identifiers, trace ids and the Gateway's route badge, and a wordmark is none of
        those. It is deliberately not accent-coloured either - the accent is the live,
        actionable colour and the frame spends it on the active nav item alone.
      */}
      <span
        aria-hidden="true"
        className="flex h-7 w-7 items-center justify-center rounded border border-slate-700 bg-ink font-display text-[0.7rem] font-semibold leading-none tracking-tight text-white"
      >
        NA
      </span>
      <span className="hidden flex-col leading-none sm:flex">
        <span className="text-sm font-semibold tracking-tight text-white">NADDP</span>
        <span className="mt-0.5 hidden text-2xs text-slate-300 laptop:block">
          Nigeria-Australia Digital Diplomacy Platform
        </span>
      </span>
      <span className="sr-only">
        NADDP home, Nigeria-Australia Digital Diplomacy Platform command centre
      </span>
    </Link>
  );
}

interface NavListProps {
  navItems: readonly NavItem[];
  pathname: string;
  ariaLabel: string;
  withTooltips: boolean;
  /**
   * Namespaces the generated description ids. The rail and the drawer can both be in the
   * DOM at once, and duplicate ids would make `aria-describedby` resolve to the wrong
   * element.
   */
  idPrefix: string;
  onNavigate?: () => void;
  className?: string;
}

function NavList({
  navItems,
  pathname,
  ariaLabel,
  withTooltips,
  idPrefix,
  onNavigate,
  className,
}: NavListProps): React.JSX.Element {
  if (navItems.length === 0) {
    return (
      <nav aria-label={ariaLabel} className={className}>
        <div className="rounded-md border border-dashed border-slate-700 p-3">
          <p className="flex items-center gap-2 text-sm font-medium text-white">
            <Lock aria-hidden="true" className="h-4 w-4 text-slate-400" />
            No destinations available
          </p>
          <p className="mt-1.5 text-xs leading-snug text-slate-300">
            Navigation is generated from the permissions your session holds, and access is
            denied by default. Choose a demo role in the top bar to populate this rail.
          </p>
        </div>
      </nav>
    );
  }

  return (
    <nav aria-label={ariaLabel} className={className}>
      <ul className="flex flex-col gap-0.5">
        {navItems.map((item) => (
          <li key={item.href}>
            <NavLink
              item={item}
              pathname={pathname}
              withTooltip={withTooltips}
              idPrefix={idPrefix}
              onNavigate={onNavigate}
            />
          </li>
        ))}
      </ul>
    </nav>
  );
}

interface NavLinkProps {
  item: NavItem;
  pathname: string;
  withTooltip: boolean;
  idPrefix: string;
  onNavigate?: () => void;
}

function NavLink({
  item,
  pathname,
  withTooltip,
  idPrefix,
  onNavigate,
}: NavLinkProps): React.JSX.Element {
  const Icon = NAV_ICONS[item.icon];
  const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);
  const descriptionId = `${idPrefix}-nav-desc-${item.icon}`;

  /*
   * A destination this build does not serve is shown, not hidden, and is not a link.
   *
   * Hiding it would misrepresent the product to an audience being shown its shape.
   * Linking it would put a 404 one click from the command centre, and BUILD_BIBLE §0 is
   * explicit that the demo must be incapable of dead-ending. So: rendered, dimmed,
   * `aria-disabled`, out of the tab order, and labelled with the week that builds it.
   *
   * The permission filter has already run by this point. A role that may not read the
   * underlying object never reaches this branch, because the entry is not in `navItems`
   * at all.
   *
   * The dimmed tone is --slate-400, which measures 4.11:1 on --slate-900. globals.css
   * restricts that token to non-text, and this is the one text exception the rule
   * allows: WCAG 1.4.3 exempts text that is part of an inactive user interface
   * component, and this entry is inert by construction - aria-disabled, no href, not
   * focusable. Every ACTIVE rail item is 4.61:1 or better.
   */
  if (item.availability === 'planned') {
    const planned = (
      <span
        aria-disabled="true"
        aria-describedby={descriptionId}
        className="group relative flex cursor-default items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium text-slate-400"
      >
        <Icon aria-hidden="true" className="h-4 w-4 shrink-0" />
        <span className="truncate">{item.label}</span>
        {item.plannedFor === undefined ? null : (
          <span
            aria-hidden="true"
            className="ml-auto shrink-0 rounded border border-slate-700 px-1.5 py-0.5 text-2xs font-medium"
          >
            {item.plannedFor}
          </span>
        )}
        <span id={descriptionId} className="sr-only">
          {item.description} Not available in this build
          {item.plannedFor === undefined ? '' : `; planned for ${item.plannedFor}`}.
        </span>
      </span>
    );

    if (!withTooltip) return planned;

    return (
      <Tooltip>
        <TooltipTrigger asChild>{planned}</TooltipTrigger>
        <TooltipContent side="right" className="max-w-[16rem]">
          {item.description}
          <span className="mt-1 block text-2xs text-slate-700">
            {item.plannedFor === undefined
              ? 'Not available in this build.'
              : `Not available in this build; planned for ${item.plannedFor}.`}
          </span>
        </TooltipContent>
      </Tooltip>
    );
  }

  const link = (
    <Link
      href={item.href}
      aria-current={isActive ? 'page' : undefined}
      aria-describedby={descriptionId}
      onClick={onNavigate}
      className={cn(
        'group relative flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors',
        DARK_SURFACE_FOCUS,
        isActive
          ? // A recessed --ink seat rather than a lighter tint. Lightening the ground is
            // what a hover does, and it would also drag --accent-on-dark down to 4.07:1;
            // darkening it lifts the accent to 5.64:1 and reads as an instrument panel.
            'bg-ink font-semibold text-accent-on-dark'
          : 'font-medium text-slate-300 hover:bg-ink/60 hover:text-white',
      )}
    >
      {/*
        The current page is marked three ways - a shape (this left tick), the recessed
        background, and aria-current - so it is never signalled by colour alone
        (WCAG 1.4.1).
      */}
      {isActive ? (
        <span
          aria-hidden="true"
          className="absolute inset-y-1 left-0 w-0.5 rounded-full bg-accent-on-dark"
        />
      ) : null}
      <Icon
        aria-hidden="true"
        className={cn('h-4 w-4 shrink-0', isActive && 'text-accent-on-dark')}
      />
      <span className="truncate">{item.label}</span>
      {/*
        The description is available to assistive tech at all times, not only on hover.
        A tooltip alone would be invisible to keyboard-only and screen-reader users.
      */}
      <span id={descriptionId} className="sr-only">
        {item.description}
      </span>
    </Link>
  );

  if (!withTooltip) return link;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{link}</TooltipTrigger>
      <TooltipContent side="right" className="max-w-[16rem]">
        {item.description}
      </TooltipContent>
    </Tooltip>
  );
}
