"use client";

import { useEffect, useRef } from "react";

/**
 * setInterval that only runs while the tab is visible AND `enabled` is true.
 *
 * A very active dashboard should not keep fetching + re-rendering when the user
 * has switched away. This pauses the interval on `document.hidden` (Page
 * Visibility API) and fires the callback once immediately when the tab becomes
 * visible again, so data isn't stale on return. Timers are always cleaned up.
 */
export function usePolling(
  callback: () => void,
  intervalMs: number,
  enabled: boolean = true,
) {
  const cbRef = useRef(callback);
  cbRef.current = callback;

  useEffect(() => {
    if (!enabled) return;
    let timer: ReturnType<typeof setInterval> | null = null;

    const start = () => {
      if (timer != null) return;
      cbRef.current(); // refresh immediately on (re)start
      timer = setInterval(() => cbRef.current(), intervalMs);
    };
    const stop = () => {
      if (timer != null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => (document.hidden ? stop() : start());

    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      stop();
    };
  }, [intervalMs, enabled]);
}
