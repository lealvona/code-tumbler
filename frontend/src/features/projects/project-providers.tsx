"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import type { ProviderInfo, ProjectProviders } from "@/lib/types";
import { useToast } from "@/hooks/use-toast";

interface ProjectProviderConfigProps {
  projectName: string;
  isRunning: boolean;
}

const AGENTS = ["architect", "engineer", "verifier"] as const;

export function ProjectProviderConfig({
  projectName,
  isRunning,
}: ProjectProviderConfigProps) {
  const [projectProviders, setProjectProviders] =
    useState<ProjectProviders | null>(null);
  const [allProviders, setAllProviders] = useState<ProviderInfo[]>([]);
  const [localOverrides, setLocalOverrides] = useState<Record<string, string>>(
    {}
  );
  const [saving, setSaving] = useState(false);
  const { toast } = useToast();

  useEffect(() => {
    api
      .getProjectProviders(projectName)
      .then((data) => {
        setProjectProviders(data);
        setLocalOverrides(data.overrides);
      })
      .catch(console.error);
    api.listProviders().then(setAllProviders).catch(console.error);
  }, [projectName]);

  async function handleSave() {
    setSaving(true);
    try {
      await api.updateProjectProviders(projectName, localOverrides);
      const updated = await api.getProjectProviders(projectName);
      setProjectProviders(updated);
      toast({
        title: "Providers updated",
        description: isRunning
          ? "Changes will take effect on the next iteration."
          : "Changes saved.",
      });
    } catch (e) {
      toast({
        variant: "destructive",
        title: "Failed to update providers",
        description: e instanceof Error ? e.message : "Unknown error",
      });
    } finally {
      setSaving(false);
    }
  }

  function handleClear() {
    setLocalOverrides({});
  }

  if (!projectProviders) {
    return (
      <p className="text-sm text-muted-foreground">Loading providers...</p>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Agent Providers</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {AGENTS.map((agent) => {
            const effective = projectProviders.effective[agent];
            const providerInfo = allProviders.find(
              (p) => p.name === effective?.provider
            );
            const isAsync = providerInfo?.supports_async ?? false;
            const concurrency = providerInfo?.concurrency_limit ?? 1;
            return (
              <div key={agent} className="flex items-center gap-4 flex-wrap">
                <label className="w-24 text-sm font-medium capitalize">
                  {agent}
                </label>
                <Select
                  value={localOverrides[agent] || "__global__"}
                  onValueChange={(v) => {
                    if (v === "__global__") {
                      const next = { ...localOverrides };
                      delete next[agent];
                      setLocalOverrides(next);
                    } else {
                      setLocalOverrides({ ...localOverrides, [agent]: v });
                    }
                  }}
                >
                  <SelectTrigger className="w-64">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__global__">
                      Use global default
                    </SelectItem>
                    {allProviders.map((p) => (
                      <SelectItem key={p.name} value={p.name}>
                        {p.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {effective?.is_override && (
                  <Badge variant="outline" className="text-xs">
                    override
                  </Badge>
                )}
                <Badge
                  className={`text-[10px] px-2 py-0 h-5 border-0 ${
                    isAsync
                      ? "bg-green-600 text-white"
                      : "bg-slate-500 text-white"
                  }`}
                >
                  {isAsync ? "Async" : "Sync"}
                </Badge>
                {agent === "engineer" && isAsync && concurrency > 1 && (
                  <Badge className="text-[10px] px-2 py-0 h-5 border-0 bg-blue-600 text-white">
                    Parallel Generation
                  </Badge>
                )}
                <span className="text-xs text-muted-foreground">
                  {effective?.model}
                  {isAsync && concurrency > 1 && (
                    <span className="ml-2 text-[10px] opacity-70">
                      concurrency: {concurrency}
                    </span>
                  )}
                </span>
              </div>
            );
          })}
        </CardContent>
      </Card>

      <div className="flex gap-2">
        <Button onClick={handleSave} disabled={saving} size="sm">
          {saving ? "Saving..." : "Save Provider Overrides"}
        </Button>
        <Button onClick={handleClear} variant="outline" size="sm">
          Clear All Overrides
        </Button>
      </div>
      {isRunning && (
        <p className="text-xs text-muted-foreground">
          Changes will take effect at the start of the next iteration.
        </p>
      )}
    </div>
  );
}
