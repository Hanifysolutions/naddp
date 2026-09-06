import { redirect } from 'next/navigation';

/**
 * The application has no separate landing page. Every session starts at the command
 * centre, which is also the origin BUILD_BIBLE §11 measures "three clicks" from.
 *
 * The return type is left to inference: `redirect()` is declared to return `never`, so
 * the end of this function is unreachable and Next's page-type check is satisfied.
 */
export default function RootPage() {
  redirect('/command');
}
