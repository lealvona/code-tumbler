"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

export function StatusPanel() {
  const connected = useStore((s) => s.connected);
  const projects = useStore((s) => s.projects);
  const [health, setHealth] = useState<{ status: string; version: string } | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  const activeCount = projects.filter(
    (p) => p.is_running || ["planning", "engineering", "verifying"].includes(p.status)
  ).length;

  return (
    <div className="grid gap-4 md:grid-cols-3">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Backend
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2">
            <Badge variant={health?.status === "ok" ? "default" : "destructive"}>
              {health?.status === "ok" ? "Healthy" : "Offline"}
            </Badge>
            {health?.version && (
              <span className="text-xs text-muted-foreground">
                v{health.version}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Projects
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">{projects.length}</div>
          {activeCount > 0 && (
            <p className="text-xs text-muted-foreground">
              {activeCount} active
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            SSE Stream
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Badge variant={connected ? "default" : "secondary"}>
            {connected ? "Connected" : "Disconnected"}
          </Badge>
        </CardContent>
      </Card>
    </div>
  );
}
