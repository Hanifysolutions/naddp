/**
 * The primary action on a meetings screen, in the Mission Slate accent.
 *
 * DESIGN_SYSTEM.md reserves `--accent` for "the live and the actionable ... primary action",
 * and `globals.css` measures white on the accent at 6.25:1. The kit's default Button is the
 * slate-900 fill with a drop shadow; here the shadow goes (flat instrument panel, no shadows)
 * and the fill is the accent, so Send, Approve and send, and each sheet's confirmation read as
 * the one actionable thing in their panel.
 *
 * `text-primary-foreground` rather than `text-white`, so the pair still clears contrast under
 * `.dark`, where the accent lightens and that foreground darkens.
 */
export const PRIMARY_ACTION = 'bg-accent text-primary-foreground shadow-none hover:bg-accent/90';
