"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { CostTimeseriesPoint } from "@/lib/types";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

interface CostChartProps {
  projectName?: string;
}

export function CostChart({ projectName }: CostChartProps) {
  const [data, setData] = useState<CostTimeseriesPoint[]>([]);

  useEffect(() => {
    api.getCostTimeseries(projectName).then(setData).catch(() => {});
  }, [projectName]);

  if (data.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Cost Over Time</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No cost data available yet.
          </p>
        </CardContent>
      </Card>
    );
  }

  const formatted = data.map((d) => ({
    ...d,
    label: new Date(d.hour).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
    }),
  }));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">Cost Over Time</CardTitle>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={220}>
          <AreaChart data={formatted}>
            <defs>
              <linearGradient id="costGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 11 }}
              className="fill-muted-foreground"
            />
            <YAxis
              tick={{ fontSize: 11 }}
              className="fill-muted-foreground"
              tickFormatter={(v: number) => `$${v.toFixed(3)}`}
            />
            <Tooltip
              formatter={(value) => [`$${Number(value).toFixed(4)}`, "Cost"]}
              labelFormatter={(label) => String(label)}
              contentStyle={{
                backgroundColor: "hsl(var(--card))",
                border: "1px solid hsl(var(--border))",
                borderRadius: 6,
                fontSize: 12,
              }}
            />
            <Area
              type="monotone"
              dataKey="cost"
              stroke="hsl(var(--primary))"
              fill="url(#costGradient)"
              strokeWidth={2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
