"use client";

import { useEffect, useState, type ReactNode } from "react";

/**
 * Renders children only after the component has mounted on the client.
 *
 * The server renders nothing inside, so there is no server HTML for React to
 * hydrate against — which makes the subtree immune to hydration mismatches
 * caused by DOM-mutating browser extensions (Dark Reader, Grammarly, etc.) that
 * inject attributes into elements before React hydrates. This app is a
 * client-side dashboard (all data is fetched in the browser), so nothing of
 * value is lost by skipping SSR for the shell.
 */
export function ClientOnly({ children }: { children: ReactNode }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return null;
  return <>{children}</>;
}
