"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface BudgetGaugeProps {
  currentCost: number;
  maxBudget: number;
}

export function BudgetGauge({ currentCost, maxBudget }: BudgetGaugeProps) {
  if (maxBudget <= 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Budget</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            ${currentCost.toFixed(4)} spent — No budget limit set
          </p>
        </CardContent>
      </Card>
    );
  }

  const pct = Math.min((currentCost / maxBudget) * 100, 100);

  let barColor = "bg-green-500";
  let textColor = "text-green-600 dark:text-green-400";
  if (pct >= 90) {
    barColor = "bg-red-500";
    textColor = "text-red-600 dark:text-red-400";
  } else if (pct >= 70) {
    barColor = "bg-yellow-500";
    textColor = "text-yellow-600 dark:text-yellow-400";
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Budget</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex justify-between text-sm">
          <span className={textColor}>
            ${currentCost.toFixed(4)} / ${maxBudget.toFixed(2)}
          </span>
          <span className="text-muted-foreground">{pct.toFixed(1)}%</span>
        </div>
        <div className="h-3 rounded-full bg-muted overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${barColor}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      </CardContent>
    </Card>
  );
}
