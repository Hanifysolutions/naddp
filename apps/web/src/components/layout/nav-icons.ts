import {
  BookOpen,
  CalendarClock,
  GraduationCap,
  LayoutDashboard,
  LifeBuoy,
  Radar,
  ShieldCheck,
  Target,
  Users,
  type LucideIcon,
} from 'lucide-react';

import type { NavIconName } from '@/lib/nav';

/**
 * Resolves the serializable icon keys used in `@/lib/nav` to actual components.
 *
 * This registry exists so a `NavItem[]` can be produced by a server component and handed
 * to a client component as plain data. React function references cannot cross that
 * boundary; strings can.
 */
export const NAV_ICONS: Readonly<Record<NavIconName, LucideIcon>> = {
  command: LayoutDashboard,
  intelligence: Radar,
  opportunities: Target,
  stakeholders: Users,
  meetings: CalendarClock,
  consular: LifeBuoy,
  diaspora: GraduationCap,
  knowledge: BookOpen,
  governance: ShieldCheck,
};
