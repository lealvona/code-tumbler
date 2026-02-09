"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { CostPerIteration } from "@/lib/types";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";

const AGENT_COLORS: Record<string, string> = {
  architect: "hsl(210, 80%, 55%)",
  engineer: "hsl(150, 60%, 45%)",
  verifier: "hsl(30, 80%, 55%)",
};

interface Props {
  projectName: string;
}

export function CostPerIterationChart({ projectName }: Props) {
  const [data, setData] = useState<CostPerIteration[]>([]);

  useEffect(() => {
    api.getCostPerIteration(projectName).then(setData).catch(() => {});
  }, [projectName]);

  if (data.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">
            Cost per Iteration
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No iteration cost data available yet.
          </p>
        </CardContent>
      </Card>
    );
  }

  // Pivot data: group by iteration, with agent costs as separate keys
  const agents = Array.from(new Set(data.map((d) => d.agent)));
  const byIteration = new Map<number, Record<string, number>>();
  for (const d of data) {
    if (!byIteration.has(d.iteration)) {
      byIteration.set(d.iteration, { iteration: d.iteration });
    }
    const row = byIteration.get(d.iteration);
    if (row) row[d.agent] = d.cost;
  }
  const pivoted = Array.from(byIteration.values()).sort(
    (a, b) => (a.iteration as number) - (b.iteration as number)
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">
          Cost per Iteration
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={pivoted}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
            <XAxis
              dataKey="iteration"
              tick={{ fontSize: 11 }}
              className="fill-muted-foreground"
              label={{
                value: "Iteration",
                position: "insideBottom",
                offset: -5,
                fontSize: 11,
              }}
            />
            <YAxis
              tick={{ fontSize: 11 }}
              className="fill-muted-foreground"
              tickFormatter={(v: number) => `$${v.toFixed(3)}`}
            />
            <Tooltip
              formatter={(value, name) => [
                `$${Number(value).toFixed(4)}`,
                name,
              ]}
              contentStyle={{
                backgroundColor: "hsl(var(--card))",
                border: "1px solid hsl(var(--border))",
                borderRadius: 6,
                fontSize: 12,
              }}
            />
            <Legend />
            {agents.map((agent) => (
              <Line
                key={agent}
                type="monotone"
                dataKey={agent}
                stroke={AGENT_COLORS[agent] || "hsl(var(--primary))"}
                strokeWidth={2}
                dot={{ r: 3 }}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
