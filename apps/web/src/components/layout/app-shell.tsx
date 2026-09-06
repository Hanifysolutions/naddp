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
 * Accessibility contract for this component:
 *  - a skip link is the first focusable element on every page (WCAG 2.4.1);
 *  - the top bar, rail and content are wrapped in `header` / `nav` / `main` landmarks,
 *    with the rail labelled so a screen reader can distinguish the two navigations;
 *  - the current page is marked with `aria-current="page"`, not by colour alone;
 *  - `<main>` is programmatically focusable so the skip link moves focus, not just
 *    scroll position;
 *  - every control has a visible focus ring from the global `:focus-visible` rule.
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
      <a
        href={`#${MAIN_CONTENT_ID}`}
        className={cn(
          'sr-only z-[100] rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground',
          'focus:not-sr-only focus:absolute focus:left-3 focus:top-3',
        )}
      >
        Skip to main content
      </a>

      <header className="sticky top-0 z-40 h-14 shrink-0 border-b border-border bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
        <div className="flex h-full items-center gap-3 px-3 lg:px-4">
          {/* Rail trigger, shown only where the persistent rail is not. */}
          <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="lg:hidden">
                <Menu aria-hidden="true" />
                <span className="sr-only">Open navigation</span>
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-72 p-0">
              <SheetHeader className="px-4 pb-2 pt-5">
                <SheetTitle>Navigation</SheetTitle>
                <SheetDescription>
                  Destinations are generated from the permissions your current demo role
                  holds.
                </SheetDescription>
              </SheetHeader>
              <Separator />
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
            <Separator orientation="vertical" className="h-6" />
            {rolePicker ?? <RolePicker />}
          </div>
        </div>
      </header>

      <div className="flex flex-1 items-stretch">
        <aside className="hidden w-[15rem] shrink-0 border-r border-border bg-card lg:block">
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
      className="flex items-center gap-2.5 rounded-md px-1 py-1 focus-visible:outline-none"
    >
      <span
        aria-hidden="true"
        className="flex h-7 w-7 items-center justify-center rounded-md bg-primary font-mono text-[0.65rem] font-bold leading-none tracking-tight text-primary-foreground"
      >
        NA
      </span>
      <span className="flex flex-col leading-none">
        <span className="text-sm font-semibold tracking-tight text-foreground">NADDP</span>
        <span className="mt-0.5 hidden text-2xs text-muted-foreground laptop:block">
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
        <div className="rounded-md border border-dashed border-input p-3">
          <p className="flex items-center gap-2 text-sm font-medium text-foreground">
            <Lock aria-hidden="true" className="h-4 w-4 text-muted-foreground" />
            No destinations available
          </p>
          <p className="mt-1.5 text-xs leading-snug text-muted-foreground">
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

  const link = (
    <Link
      href={item.href}
      aria-current={isActive ? 'page' : undefined}
      aria-describedby={descriptionId}
      onClick={onNavigate}
      className={cn(
        'group relative flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium transition-colors',
        'hover:bg-accent hover:text-accent-foreground',
        isActive ? 'bg-accent text-accent-foreground' : 'text-muted-foreground',
      )}
    >
      {/*
        The current page is marked three ways - a shape (this left bar), a background
        tint, and aria-current - so it is never signalled by colour alone (WCAG 1.4.1).
      */}
      {isActive ? (
        <span
          aria-hidden="true"
          className="absolute inset-y-1 left-0 w-0.5 rounded-full bg-primary"
        />
      ) : null}
      <Icon
        aria-hidden="true"
        className={cn('h-4 w-4 shrink-0', isActive && 'text-primary')}
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
