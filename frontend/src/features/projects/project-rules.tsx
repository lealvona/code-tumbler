"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ProjectRulesData, Rule } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RuleList } from "@/features/rules/rule-list";
import { useToast } from "@/hooks/use-toast";
import { Lightbulb, ArrowUpCircle, X } from "lucide-react";

export function ProjectRules({ projectName }: { projectName: string }) {
  const [data, setData] = useState<ProjectRulesData | null>(null);
  const { toast } = useToast();

  const load = useCallback(() => {
    api.getProjectRules(projectName).then(setData).catch(() => {});
  }, [projectName]);

  useEffect(() => {
    load();
  }, [load]);

  if (!data) return <p className="text-sm text-muted-foreground p-2">Loading…</p>;

  async function addRule(text: string, category: string) {
    try {
      await api.addProjectRule(projectName, text, category);
      load();
    } catch (e) {
      toast({ variant: "destructive", title: "Failed to add", description: String(e) });
    }
  }
  async function delRule(id: string) {
    await api.deleteProjectRule(projectName, id).then(load).catch(() => {});
  }
  async function toggleRule(rule: Rule, enabled: boolean) {
    if (!data) return;
    const next = data.project_rules.map((r) => (r.id === rule.id ? { ...r, enabled } : r));
    setData({ ...data, project_rules: next });
    await api.replaceProjectRules(projectName, next).catch(load);
  }
  async function promote(id: string, scope: "project" | "global") {
    try {
      await api.promoteCandidate(projectName, id, scope);
      toast({ title: "Promoted", description: `Candidate is now a ${scope} rule.` });
      load();
    } catch (e) {
      toast({ variant: "destructive", title: "Promote failed", description: String(e) });
    }
  }
  async function dismiss(id: string) {
    await api.dismissCandidate(projectName, id).then(load).catch(() => {});
  }

  return (
    <div className="space-y-4">
      {/* Auto-detected candidates */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Lightbulb className="h-4 w-4 text-amber-500" />
            Detected Candidates ({data.candidates.length})
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            Auto-detected from sandbox output. These are <strong>not</strong> applied to the
            agents until you promote one to a rule.
          </p>
        </CardHeader>
        <CardContent className="space-y-2">
          {data.candidates.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No issues detected yet — nothing to review.
            </p>
          )}
          {data.candidates.map((c) => (
            <div key={c.id} className="rounded-md border border-amber-200 dark:border-amber-900 bg-amber-50/50 dark:bg-amber-950/20 p-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-sm">{c.suggested_text}</p>
                  <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                    <Badge variant="outline" className="text-[10px]">{c.category}</Badge>
                    <Badge variant="outline" className="text-[10px]">{c.signature}</Badge>
                    <Badge variant="outline" className="text-[10px]">seen ×{c.count}</Badge>
                  </div>
                  {c.evidence && (
                    <code className="block mt-1.5 text-[11px] text-muted-foreground font-mono truncate">
                      {c.evidence}
                    </code>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button size="sm" variant="outline" className="h-7" onClick={() => promote(c.id, "project")}>
                    <ArrowUpCircle className="h-3.5 w-3.5 mr-1" /> Project
                  </Button>
                  <Button size="sm" variant="outline" className="h-7" onClick={() => promote(c.id, "global")}>
                    <ArrowUpCircle className="h-3.5 w-3.5 mr-1" /> Global
                  </Button>
                  <Button size="sm" variant="ghost" className="h-7 w-7 p-0" onClick={() => dismiss(c.id)}>
                    <X className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* Project rules (editable) */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Project Rules ({data.project_rules.length})</CardTitle>
          <p className="text-xs text-muted-foreground">
            Rules that apply only to <strong>{projectName}</strong>, injected into its agents.
          </p>
        </CardHeader>
        <CardContent>
          <RuleList
            rules={data.project_rules}
            categories={data.categories}
            onAdd={addRule}
            onDelete={delRule}
            onToggle={toggleRule}
          />
        </CardContent>
      </Card>

      {/* Effective (read-only) */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">
            Effective Rules ({data.effective.length}) — injected into agents
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            Enabled global + project rules. Edit global rules on the Rules page.
          </p>
        </CardHeader>
        <CardContent className="space-y-1.5">
          {data.effective.map((r) => (
            <div key={r.id} className="flex items-center gap-2 text-sm">
              <Badge variant="outline" className="text-[10px] shrink-0">{r.scope}</Badge>
              <span className="truncate">{r.text}</span>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
