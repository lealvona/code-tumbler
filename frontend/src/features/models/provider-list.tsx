"use client";

import { useState } from "react";
import type { ProviderInfo } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

const typeBadgeVariant: Record<string, "default" | "secondary" | "outline"> = {
  ollama: "default",
  vllm: "default",
  openai: "secondary",
  anthropic: "secondary",
  gemini: "secondary",
};

export function ProviderList({ providers }: { providers: ProviderInfo[] }) {
  const [models, setModels] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState<string | null>(null);

  async function handleListModels(name: string) {
    setLoading(name);
    try {
      const res = await api.listModels(name);
      setModels((prev) => ({ ...prev, [name]: res.models }));
    } catch (e) {
      setModels((prev) => ({
        ...prev,
        [name]: [`Error: ${e instanceof Error ? e.message : "Failed"}`],
      }));
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="grid gap-4 md:grid-cols-2">
      {providers.map((p) => (
        <Card key={p.name}>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm">{p.name}</CardTitle>
              <div className="flex gap-1">
                <Badge variant={typeBadgeVariant[p.type] ?? "outline"}>
                  {p.type}
                </Badge>
                {p.is_active && <Badge variant="default">Active</Badge>}
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="text-sm space-y-1">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Model</span>
                <span className="font-mono text-xs">{p.model || "N/A"}</span>
              </div>
              {p.base_url && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">URL</span>
                  <span className="font-mono text-xs truncate max-w-48">
                    {p.base_url}
                  </span>
                </div>
              )}
              <div className="flex justify-between">
                <span className="text-muted-foreground">Cost (in/out per 1k)</span>
                <span className="text-xs">
                  ${p.cost_input} / ${p.cost_output}
                </span>
              </div>
            </div>

            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={() => handleListModels(p.name)}
              disabled={loading === p.name}
            >
              {loading === p.name ? "Loading..." : "List Models"}
            </Button>

            {models[p.name] && (
              <div className="text-xs font-mono bg-muted rounded p-2 max-h-32 overflow-auto">
                {models[p.name].map((m, i) => (
                  <div key={i}>{m}</div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
