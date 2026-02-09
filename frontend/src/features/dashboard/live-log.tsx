"use client";

import { useEffect, useRef } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export function LiveLog() {
  const events = useStore((s) => s.events);
  const clearEvents = useStore((s) => s.clearEvents);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  return (
    <Card>
      <CardHeader className="pb-2 flex flex-row items-center justify-between">
        <CardTitle className="text-sm font-medium">Live Log</CardTitle>
        <Button variant="ghost" size="sm" onClick={clearEvents}>
          Clear
        </Button>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-64 w-full rounded border bg-black/95 p-3">
          <div className="space-y-0.5 font-mono text-xs">
            {events.length === 0 && (
              <p className="text-muted-foreground">
                Waiting for events...
              </p>
            )}
            {events.map((event, i) => {
              const time = new Date(event.timestamp).toLocaleTimeString();
              const msg =
                (event.data.message as string) ||
                `[${event.type}] ${event.data.project || ""}`;
              const level = (event.data.level as string) || event.type;

              return (
                <div key={i} className="flex gap-2">
                  <span className="text-muted-foreground shrink-0">
                    {time}
                  </span>
                  <span
                    className={cn(
                      level === "error" || event.type === "project_failed"
                        ? "text-red-400"
                        : level === "warning"
                        ? "text-yellow-400"
                        : event.type === "project_complete"
                        ? "text-green-400"
                        : event.type === "score_update"
                        ? "text-blue-400"
                        : "text-gray-300"
                    )}
                  >
                    {msg}
                  </span>
                </div>
              );
            })}
            <div ref={bottomRef} />
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
