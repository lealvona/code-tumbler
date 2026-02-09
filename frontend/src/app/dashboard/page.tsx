"use client";

import { useEffect } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { StatusPanel } from "@/features/dashboard/status-panel";
import { GlobalStatsPanel } from "@/features/dashboard/global-stats";
import { LiveLog } from "@/features/dashboard/live-log";
import { CostChart } from "@/features/cost/cost-chart";
import { CostByProviderChart } from "@/features/cost/cost-by-provider-chart";
import { PhaseIndicator } from "@/features/projects/phase-indicator";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import Link from "next/link";

export default function DashboardPage() {
  const projects = useStore((s) => s.projects);
  const setProjects = useStore((s) => s.setProjects);

  useEffect(() => {
    api.listProjects().then(setProjects).catch(console.error);
    const interval = setInterval(() => {
      api.listProjects().then(setProjects).catch(() => {});
    }, 10000);
    return () => clearInterval(interval);
  }, [setProjects]);

  const activeProjects = projects.filter(
    (p) =>
      p.is_running ||
      ["planning", "engineering", "verifying"].includes(p.status)
  );

  return (
    <div className="p-6 space-y-6">
      <h2 className="text-2xl font-bold tracking-tight">Dashboard</h2>

      <StatusPanel />

      <GlobalStatsPanel />

      <div className="grid gap-4 md:grid-cols-2">
        <CostChart />
        <CostByProviderChart />
      </div>

      {activeProjects.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-lg font-semibold">Active Projects</h3>
          <div className="grid gap-3 md:grid-cols-2">
            {activeProjects.map((project) => (
              <Link key={project.name} href={`/projects/${project.name}`}>
                <Card className="hover:border-primary/50 transition-colors cursor-pointer">
                  <CardHeader className="pb-2">
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-sm">
                        {project.name}
                      </CardTitle>
                      <Badge variant="secondary">
                        Iter {project.iteration}
                      </Badge>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <PhaseIndicator phase={project.status} />
                    {project.last_score !== null && (
                      <p className="text-xs text-muted-foreground mt-2">
                        Score: {project.last_score}/10
                      </p>
                    )}
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        </div>
      )}

      <LiveLog />
    </div>
  );
}
