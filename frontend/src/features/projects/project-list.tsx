"use client";

import { useState, useEffect } from "react";
import type { ProjectSummary, ProviderInfo } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Plus, ChevronDown, ChevronUp } from "lucide-react";
import { api } from "@/lib/api";
import { ProjectCard } from "./project-card";

interface ProjectListProps {
  projects: ProjectSummary[];
  onRefresh: () => void;
}

const AGENTS = ["architect", "engineer", "verifier"] as const;

export function ProjectList({ projects, onRefresh }: ProjectListProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [requirements, setRequirements] = useState("");
  const [creating, setCreating] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  // Settings state
  const [maxIterations, setMaxIterations] = useState("");
  const [qualityThreshold, setQualityThreshold] = useState("");
  const [providerOverrides, setProviderOverrides] = useState<Record<string, string>>({});
  const [compressionEnabled, setCompressionEnabled] = useState<string>("default");
  const [compressionRate, setCompressionRate] = useState("");
  const [preserveCodeBlocks, setPreserveCodeBlocks] = useState<string>("default");

  // Provider list
  const [allProviders, setAllProviders] = useState<ProviderInfo[]>([]);

  useEffect(() => {
    if (open) {
      api.listProviders().then(setAllProviders).catch(() => {});
    }
  }, [open]);

  function resetForm() {
    setName("");
    setRequirements("");
    setShowSettings(false);
    setMaxIterations("");
    setQualityThreshold("");
    setProviderOverrides({});
    setCompressionEnabled("default");
    setCompressionRate("");
    setPreserveCodeBlocks("default");
  }

  async function handleCreate() {
    if (!name.trim() || !requirements.trim()) return;
    setCreating(true);
    try {
      const options: Record<string, unknown> = {};

      if (maxIterations) options.max_iterations = parseInt(maxIterations, 10);
      if (qualityThreshold) options.quality_threshold = parseFloat(qualityThreshold);

      const activeOverrides = Object.fromEntries(
        Object.entries(providerOverrides).filter(([, v]) => v)
      );
      if (Object.keys(activeOverrides).length > 0) {
        options.provider_overrides = activeOverrides;
      }

      const compression: Record<string, unknown> = {};
      if (compressionEnabled !== "default") compression.enabled = compressionEnabled === "true";
      if (compressionRate) compression.rate = parseFloat(compressionRate);
      if (preserveCodeBlocks !== "default") compression.preserve_code_blocks = preserveCodeBlocks === "true";
      if (Object.keys(compression).length > 0) options.compression = compression;

      await api.createProject(name.trim(), requirements.trim(), Object.keys(options).length > 0 ? options as never : undefined);
      setOpen(false);
      resetForm();
      onRefresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to create project");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-2xl font-bold tracking-tight">Projects</h2>
        <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) resetForm(); }}>
          <DialogTrigger asChild>
            <Button size="sm">
              <Plus className="h-4 w-4 mr-1" />
              New Project
            </Button>
          </DialogTrigger>
          <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>Create New Project</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div>
                <label className="text-sm font-medium">Project Name</label>
                <Input
                  placeholder="my-awesome-app"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div>
                <label className="text-sm font-medium">Requirements</label>
                <Textarea
                  placeholder="Describe what you want to build..."
                  rows={6}
                  value={requirements}
                  onChange={(e) => setRequirements(e.target.value)}
                />
              </div>

              <Separator />

              <button
                type="button"
                className="flex items-center gap-1 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
                onClick={() => setShowSettings(!showSettings)}
              >
                {showSettings ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                Project Settings
              </button>

              {showSettings && (
                <div className="space-y-4">
                  {/* Iteration & Quality */}
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-sm font-medium">Max Iterations</label>
                      <Input
                        type="number"
                        placeholder="10"
                        min={1}
                        max={100}
                        value={maxIterations}
                        onChange={(e) => setMaxIterations(e.target.value)}
                      />
                      <p className="text-[10px] text-muted-foreground mt-0.5">Default: 10</p>
                    </div>
                    <div>
                      <label className="text-sm font-medium">Quality Threshold</label>
                      <Input
                        type="number"
                        placeholder="8.0"
                        min={1}
                        max={10}
                        step={0.5}
                        value={qualityThreshold}
                        onChange={(e) => setQualityThreshold(e.target.value)}
                      />
                      <p className="text-[10px] text-muted-foreground mt-0.5">Default: 8.0 / 10</p>
                    </div>
                  </div>

                  {/* Provider Overrides */}
                  <div>
                    <label className="text-sm font-medium">Provider Overrides</label>
                    <p className="text-[10px] text-muted-foreground mb-2">Assign a specific LLM provider per agent, or leave as global default.</p>
                    <div className="space-y-2">
                      {AGENTS.map((agent) => (
                        <div key={agent} className="flex items-center gap-3">
                          <label className="w-20 text-xs font-medium capitalize">{agent}</label>
                          <Select
                            value={providerOverrides[agent] || "__global__"}
                            onValueChange={(v) => {
                              if (v === "__global__") {
                                const next = { ...providerOverrides };
                                delete next[agent];
                                setProviderOverrides(next);
                              } else {
                                setProviderOverrides({ ...providerOverrides, [agent]: v });
                              }
                            }}
                          >
                            <SelectTrigger className="flex-1">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="__global__">Global default</SelectItem>
                              {allProviders.map((p) => (
                                <SelectItem key={p.name} value={p.name}>
                                  {p.name}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Compression */}
                  <div>
                    <label className="text-sm font-medium">Compression</label>
                    <p className="text-[10px] text-muted-foreground mb-2">Controls prompt compression to reduce token usage.</p>
                    <div className="space-y-2">
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="text-xs text-muted-foreground">Enabled</label>
                          <Select value={compressionEnabled} onValueChange={setCompressionEnabled}>
                            <SelectTrigger>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="default">Default (on)</SelectItem>
                              <SelectItem value="true">On</SelectItem>
                              <SelectItem value="false">Off</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        <div>
                          <label className="text-xs text-muted-foreground">Preserve Code Blocks</label>
                          <Select value={preserveCodeBlocks} onValueChange={setPreserveCodeBlocks}>
                            <SelectTrigger>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="default">Default (yes)</SelectItem>
                              <SelectItem value="true">Yes</SelectItem>
                              <SelectItem value="false">No</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      </div>
                      <div>
                        <label className="text-xs text-muted-foreground">Compression Rate</label>
                        <Input
                          type="number"
                          placeholder="0.5"
                          min={0.1}
                          max={1.0}
                          step={0.1}
                          value={compressionRate}
                          onChange={(e) => setCompressionRate(e.target.value)}
                        />
                        <p className="text-[10px] text-muted-foreground mt-0.5">Token retention rate: 0.1 (aggressive) to 1.0 (none). Default: 0.5</p>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              <Button onClick={handleCreate} disabled={creating} className="w-full">
                {creating ? "Creating..." : "Create Project"}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      {projects.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No projects yet. Create one to get started.
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {projects.map((p) => (
            <ProjectCard key={p.name} project={p} onRefresh={onRefresh} />
          ))}
        </div>
      )}
    </div>
  );
}
