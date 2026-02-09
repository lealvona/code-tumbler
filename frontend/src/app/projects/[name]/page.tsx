"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import type { ProjectStatus } from "@/lib/types";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PhaseIndicator } from "@/features/projects/phase-indicator";
import { ArtifactBrowser } from "@/features/projects/artifact-browser";
import { ProjectProviderConfig } from "@/features/projects/project-providers";
import { AgentConversation } from "@/features/projects/agent-conversation";
import { CostSummary } from "@/features/cost/cost-summary";
import { BudgetGauge } from "@/features/cost/budget-gauge";
import { CostPerIterationChart } from "@/features/cost/cost-per-iteration-chart";
import { CostChart } from "@/features/cost/cost-chart";
import type { UsageData, AppConfig, ProviderInfo } from "@/lib/types";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Play, Square, RotateCcw, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useToast } from "@/hooks/use-toast";

export default function ProjectDetailPage() {
  const params = useParams();
  const name = params.name as string;
  const [status, setStatus] = useState<ProjectStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [usage, setUsage] = useState<UsageData | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [allProviders, setAllProviders] = useState<ProviderInfo[]>([]);
  const [resetKey, setResetKey] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const router = useRouter();
  const { toast } = useToast();

  function refresh() {
    api.getProjectStatus(name).then(setStatus).catch(console.error);
  }

  useEffect(() => {
    refresh();
    api.getUsage(name).then(setUsage).catch(() => {});
    api.getConfig().then(setConfig).catch(() => {});
    api.listProviders().then(setAllProviders).catch(() => {});
    const interval = setInterval(() => {
      refresh();
      api.getUsage(name).then(setUsage).catch(() => {});
    }, 5000);
    return () => clearInterval(interval);
  }, [name]);

  async function handleStart() {
    setStarting(true);
    try {
      await api.startProject(name);
      toast({ title: "Project started", description: `${name} is now running.` });
      refresh();
    } catch (e) {
      toast({
        variant: "destructive",
        title: "Failed to start",
        description: e instanceof Error ? e.message : "Unknown error",
      });
    } finally {
      setStarting(false);
    }
  }

  async function handleStop() {
    try {
      await api.stopProject(name);
      toast({ title: "Project stopped", description: `${name} has been stopped.` });
      refresh();
    } catch (e) {
      toast({
        variant: "destructive",
        title: "Failed to stop",
        description: e instanceof Error ? e.message : "Unknown error",
      });
    }
  }

  async function handleReset() {
    try {
      await api.resetProject(name);
      toast({ title: "Project reset", description: `${name} has been reset to initial state.` });
      refresh();
      setResetKey((k) => k + 1);
      api.getUsage(name).then(setUsage).catch(() => {});
    } catch (e) {
      toast({
        variant: "destructive",
        title: "Failed to reset",
        description: e instanceof Error ? e.message : "Unknown error",
      });
    }
  }

  async function handleDelete() {
    setDeleting(true);
    try {
      await api.deleteProject(name);
      toast({ title: "Project deleted", description: `${name} has been permanently deleted.` });
      router.push("/projects");
    } catch (e) {
      toast({
        variant: "destructive",
        title: "Failed to delete",
        description: e instanceof Error ? e.message : "Unknown error",
      });
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  if (!status) {
    return (
      <div className="p-6">
        <p className="text-muted-foreground">Loading...</p>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">{name}</h2>
          <div className="flex items-center gap-3 mt-1 flex-wrap">
            <Badge
              variant={status.status === "failed" ? "destructive" : "secondary"}
            >
              {status.status}
            </Badge>
            <span className="text-sm text-muted-foreground">
              Iteration {status.iteration} / {status.max_iterations}
            </span>
            {status.last_score !== null && (
              <span className="text-sm text-muted-foreground">
                Score: {status.last_score}/10
              </span>
            )}
          </div>
          {status.providers && (
            <div className="flex gap-3 mt-1 flex-wrap">
              {Object.entries(status.providers).map(([agent, info]) => {
                const pInfo = allProviders.find(
                  (p) => p.name === info.provider
                );
                const isAsync = pInfo?.supports_async ?? false;
                return (
                  <span
                    key={agent}
                    className="text-xs text-muted-foreground flex items-center gap-1"
                  >
                    <span className="capitalize">{agent}:</span>{" "}
                    {info.provider}
                    {info.is_override && " *"}
                    <Badge
                      className={`text-[9px] px-1.5 py-0 h-4 border-0 ${
                        isAsync
                          ? "bg-green-600 text-white"
                          : "bg-slate-500 text-white"
                      }`}
                    >
                      {isAsync ? "Async" : "Sync"}
                    </Badge>
                  </span>
                );
              })}
            </div>
          )}
        </div>
        <div className="flex gap-2">
          {!status.is_running ? (
            <>
              <Button onClick={handleStart} disabled={starting} size="sm">
                <Play className="h-4 w-4 mr-1" />
                {starting ? "Starting..." : "Start"}
              </Button>
              <Button onClick={handleReset} variant="outline" size="sm">
                <RotateCcw className="h-4 w-4 mr-1" />
                Reset
              </Button>
              <Button onClick={() => setConfirmDelete(true)} variant="destructive" size="sm">
                <Trash2 className="h-4 w-4 mr-1" />
                Delete
              </Button>
            </>
          ) : (
            <Button onClick={handleStop} variant="destructive" size="sm">
              <Square className="h-4 w-4 mr-1" />
              Stop
            </Button>
          )}
        </div>
      </div>

      <PhaseIndicator phase={status.status} />

      {status.error && (
        <Card className="border-destructive">
          <CardContent className="pt-4">
            <p className="text-sm text-destructive">{status.error}</p>
          </CardContent>
        </Card>
      )}

      <Tabs defaultValue="conversation">
        <TabsList>
          <TabsTrigger value="conversation">Conversation</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
          <TabsTrigger value="providers">Providers</TabsTrigger>
          <TabsTrigger value="artifacts">Artifacts</TabsTrigger>
          <TabsTrigger value="cost">Cost</TabsTrigger>
        </TabsList>

        <TabsContent value="conversation" className="mt-4">
          <AgentConversation
            projectName={name}
            isRunning={status.is_running}
            key={resetKey}
          />
        </TabsContent>

        <TabsContent value="status" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Project Details</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div className="grid grid-cols-2 gap-2">
                <span className="text-muted-foreground">Status</span>
                <span>{status.status}</span>
                <span className="text-muted-foreground">Iteration</span>
                <span>
                  {status.iteration} / {status.max_iterations}
                </span>
                <span className="text-muted-foreground">Quality Threshold</span>
                <span>{status.quality_threshold}/10</span>
                <span className="text-muted-foreground">Last Score</span>
                <span>
                  {status.last_score !== null
                    ? `${status.last_score}/10`
                    : "N/A"}
                </span>
                <span className="text-muted-foreground">Started</span>
                <span>
                  {new Date(status.start_time).toLocaleString()}
                </span>
                <span className="text-muted-foreground">Last Updated</span>
                <span>
                  {new Date(status.last_update).toLocaleString()}
                </span>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="providers" className="mt-4">
          <ProjectProviderConfig
            projectName={name}
            isRunning={status.is_running}
          />
        </TabsContent>

        <TabsContent value="artifacts" className="mt-4">
          <ArtifactBrowser projectName={name} />
        </TabsContent>

        <TabsContent value="cost" className="mt-4 space-y-4">
          <CostSummary projectName={name} />
          <BudgetGauge
            currentCost={usage?.total_cost ?? 0}
            maxBudget={config?.tumbler.max_cost_per_project ?? 0}
          />
          <CostChart projectName={name} />
          <CostPerIterationChart projectName={name} />
        </TabsContent>
      </Tabs>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Project</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Are you sure you want to delete <strong>{name}</strong>? This will permanently remove all files, artifacts, and history. This action cannot be undone.
          </p>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="outline" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
              {deleting ? "Deleting..." : "Delete"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
