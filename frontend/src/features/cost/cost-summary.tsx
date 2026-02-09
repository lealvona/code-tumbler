"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { UsageData } from "@/lib/types";

export function CostSummary({ projectName }: { projectName: string }) {
  const [usage, setUsage] = useState<UsageData | null>(null);

  useEffect(() => {
    api.getUsage(projectName).then(setUsage).catch(console.error);
  }, [projectName]);

  if (!usage) {
    return <p className="text-sm text-muted-foreground">Loading usage data...</p>;
  }

  const agents = Object.entries(usage.by_agent);

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-muted-foreground">
              Total Tokens
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {usage.total_tokens.toLocaleString()}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-muted-foreground">
              Total Cost
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              ${usage.total_cost.toFixed(4)}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-muted-foreground">
              API Calls
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {agents.reduce((sum, [, a]) => sum + a.calls, 0)}
            </div>
          </CardContent>
        </Card>
      </div>

      {agents.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Usage by Agent</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {agents.map(([name, data]) => (
                <div key={name} className="flex items-center justify-between">
                  <div>
                    <span className="text-sm font-medium capitalize">
                      {name}
                    </span>
                    <span className="text-xs text-muted-foreground ml-2">
                      ({data.calls} calls)
                    </span>
                  </div>
                  <div className="text-right text-sm">
                    <span>{data.tokens.toLocaleString()} tokens</span>
                    <span className="text-muted-foreground ml-2">
                      ${data.cost.toFixed(4)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
