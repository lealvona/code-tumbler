"use client";

import { useState, useEffect } from "react";
import type { AppConfig, ProviderInfo } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";

import { Switch } from "@/components/ui/switch";

export function ConfigForm() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    api.getConfig().then(setConfig).catch(console.error);
    api.listProviders().then(setProviders).catch(console.error);
  }, []);

  async function handleSave() {
    if (!config) return;
    setSaving(true);
    setMessage("");
    try {
      await api.updateConfig(config);
      setMessage("Configuration saved successfully");
    } catch (e) {
      setMessage(`Error: ${e instanceof Error ? e.message : "Failed to save"}`);
    } finally {
      setSaving(false);
    }
  }

  if (!config) {
    return <p className="text-sm text-muted-foreground">Loading config...</p>;
  }

  const providerNames = providers.map((p) => p.name);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Active Provider</CardTitle>
        </CardHeader>
        <CardContent>
          <Select
            value={config.active_provider}
            onValueChange={(v) =>
              setConfig({ ...config, active_provider: v })
            }
          >
            <SelectTrigger className="w-64">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {providerNames.map((name) => (
                <SelectItem key={name} value={name}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Agent Provider Assignments</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {["architect", "engineer", "verifier"].map((agent) => (
            <div key={agent} className="flex items-center gap-4">
              <label className="w-24 text-sm font-medium capitalize">
                {agent}
              </label>
              <Select
                value={config.agent_providers[agent] || config.active_provider}
                onValueChange={(v) =>
                  setConfig({
                    ...config,
                    agent_providers: {
                      ...config.agent_providers,
                      [agent]: v,
                    },
                  })
                }
              >
                <SelectTrigger className="w-64">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {providerNames.map((name) => (
                    <SelectItem key={name} value={name}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Tumbler Settings</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm text-muted-foreground">
                Max Iterations
              </label>
              <Input
                type="number"
                value={config.tumbler.max_iterations}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    tumbler: {
                      ...config.tumbler,
                      max_iterations: parseInt(e.target.value) || 10,
                    },
                  })
                }
              />
            </div>
            <div>
              <label className="text-sm text-muted-foreground">
                Quality Threshold (0-10)
              </label>
              <Input
                type="number"
                step="0.5"
                min="0"
                max="10"
                value={config.tumbler.quality_threshold}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    tumbler: {
                      ...config.tumbler,
                      quality_threshold: parseFloat(e.target.value) || 8.0,
                    },
                  })
                }
              />
            </div>
            <div>
              <label className="text-sm text-muted-foreground">
                Project Timeout (seconds)
              </label>
              <Input
                type="number"
                value={config.tumbler.project_timeout}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    tumbler: {
                      ...config.tumbler,
                      project_timeout: parseInt(e.target.value) || 3600,
                    },
                  })
                }
              />
            </div>
            <div>
              <label className="text-sm text-muted-foreground">
                Max Cost per Project ($)
              </label>
              <Input
                type="number"
                step="0.01"
                value={config.tumbler.max_cost_per_project}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    tumbler: {
                      ...config.tumbler,
                      max_cost_per_project: parseFloat(e.target.value) || 0,
                    },
                  })
                }
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Prompt Compression</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-4">
            <Switch
              checked={config.tumbler.prompt_compression?.enabled ?? true}
              onCheckedChange={(checked) =>
                setConfig({
                  ...config,
                  tumbler: {
                    ...config.tumbler,
                    prompt_compression: {
                      ...(config.tumbler.prompt_compression || { rate: 0.5, preserve_code_blocks: true }),
                      enabled: checked,
                    },
                  },
                })
              }
            />
            <label className="text-sm font-medium">Enable Compression</label>
          </div>
          
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm text-muted-foreground">
                Compression Rate (0.1 - 0.9)
              </label>
              <Input
                type="number"
                step="0.1"
                min="0.1"
                max="0.9"
                value={config.tumbler.prompt_compression?.rate ?? 0.5}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    tumbler: {
                      ...config.tumbler,
                      prompt_compression: {
                        ...(config.tumbler.prompt_compression || { enabled: true, preserve_code_blocks: true }),
                        rate: parseFloat(e.target.value) || 0.5,
                      },
                    },
                  })
                }
                disabled={!config.tumbler.prompt_compression?.enabled}
              />
              <p className="text-xs text-muted-foreground mt-1">
                Target retention rate (0.5 = keep 50% of tokens)
              </p>
            </div>
            
            <div className="flex items-center gap-2 mt-8">
              <Switch
                checked={config.tumbler.prompt_compression?.preserve_code_blocks ?? true}
                onCheckedChange={(checked) =>
                  setConfig({
                    ...config,
                    tumbler: {
                      ...config.tumbler,
                      prompt_compression: {
                        ...(config.tumbler.prompt_compression || { enabled: true, rate: 0.5 }),
                        preserve_code_blocks: checked,
                      },
                    },
                  })
                }
                disabled={!config.tumbler.prompt_compression?.enabled}
              />
              <label className="text-sm text-muted-foreground">
                Preserve Code Blocks
              </label>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="flex items-center gap-4">
        <Button onClick={handleSave} disabled={saving}>
          {saving ? "Saving..." : "Save Configuration"}
        </Button>
        {message && (
          <span
            className={`text-sm ${
              message.startsWith("Error") ? "text-destructive" : "text-green-600"
            }`}
          >
            {message}
          </span>
        )}
      </div>
    </div>
  );
}
