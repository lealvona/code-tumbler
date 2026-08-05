"use client";

import { useCallback, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { usePolling } from "@/hooks/use-polling";
import type { GlobalStats } from "@/lib/types";
import { Database, DollarSign, Hash } from "lucide-react";

export function GlobalStatsPanel() {
  const [stats, setStats] = useState<GlobalStats | null>(null);

  const load = useCallback(() => {
    api.getGlobalStats().then(setStats).catch(() => {});
  }, []);
  usePolling(load, 30000); // paused when the tab is hidden

  if (!stats) return null;

  return (
    <div className="grid grid-cols-3 gap-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Total Projects</CardTitle>
          <Database className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">{stats.project_count}</div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Total Cost</CardTitle>
          <DollarSign className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">${stats.total_cost.toFixed(4)}</div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">Total Tokens</CardTitle>
          <Hash className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">
            {stats.total_tokens.toLocaleString()}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
