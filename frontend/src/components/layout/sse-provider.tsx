"use client";

import { useSSE } from "@/hooks/use-sse";

export function SSEProvider({ children }: { children: React.ReactNode }) {
  useSSE();
  return <>{children}</>;
}
