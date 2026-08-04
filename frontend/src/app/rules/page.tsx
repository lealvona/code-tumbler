"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Rule } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RuleList } from "@/features/rules/rule-list";
import { useToast } from "@/hooks/use-toast";

export default function RulesPage() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [categories, setCategories] = useState<string[]>(["general"]);
  const { toast } = useToast();

  const load = useCallback(() => {
    api
      .getGlobalRules()
      .then((d) => {
        setRules(d.rules);
        setCategories(d.categories);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function add(text: string, category: string) {
    try {
      await api.addGlobalRule(text, category);
      load();
    } catch (e) {
      toast({ variant: "destructive", title: "Failed to add rule", description: String(e) });
    }
  }

  async function del(id: string) {
    try {
      await api.deleteGlobalRule(id);
      load();
    } catch (e) {
      toast({ variant: "destructive", title: "Failed to delete", description: String(e) });
    }
  }

  async function toggle(rule: Rule, enabled: boolean) {
    const next = rules.map((r) => (r.id === rule.id ? { ...r, enabled } : r));
    setRules(next); // optimistic
    try {
      await api.replaceGlobalRules(next);
    } catch (e) {
      load();
      toast({ variant: "destructive", title: "Failed to update", description: String(e) });
    }
  }

  return (
    <div className="p-6 space-y-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight">Rules Ledger</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Global rules the Architect and Engineer must follow on every project. Per-project
          rules and auto-detected candidates live on each project&apos;s Rules tab.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Global Rules ({rules.length})</CardTitle>
        </CardHeader>
        <CardContent>
          <RuleList
            rules={rules}
            categories={categories}
            onAdd={add}
            onDelete={del}
            onToggle={toggle}
          />
        </CardContent>
      </Card>
    </div>
  );
}
