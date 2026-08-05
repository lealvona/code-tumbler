"use client";

import { useState } from "react";
import type { Rule } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Trash2, Plus } from "lucide-react";

const SOURCE_STYLES: Record<string, string> = {
  seed: "bg-slate-500",
  manual: "bg-blue-600",
  promoted: "bg-purple-600",
};

export function RuleList({
  rules,
  categories,
  onAdd,
  onDelete,
  onToggle,
}: {
  rules: Rule[];
  categories: string[];
  onAdd: (text: string, category: string) => void;
  onDelete: (id: string) => void;
  onToggle: (rule: Rule, enabled: boolean) => void;
}) {
  const [text, setText] = useState("");
  const [category, setCategory] = useState(categories[0] ?? "general");

  function submit() {
    const t = text.trim();
    if (!t) return;
    onAdd(t, category);
    setText("");
  }

  return (
    <div className="space-y-3">
      {rules.length === 0 && (
        <p className="text-sm text-muted-foreground">No rules yet.</p>
      )}
      {rules.map((r) => (
        <div
          key={r.id}
          className="flex items-start gap-3 rounded-md border p-3 bg-card"
        >
          <Switch
            checked={r.enabled}
            onCheckedChange={(v) => onToggle(r, v)}
            className="mt-0.5"
          />
          <div className="flex-1 min-w-0">
            <p className={`text-sm ${r.enabled ? "" : "opacity-50 line-through"}`}>
              {r.text}
            </p>
            <div className="flex items-center gap-2 mt-1.5">
              <Badge variant="outline" className="text-[10px]">
                {r.category}
              </Badge>
              <Badge className={`text-[10px] border-0 text-white ${SOURCE_STYLES[r.source] ?? "bg-slate-500"}`}>
                {r.source}
              </Badge>
            </div>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground hover:text-destructive"
            onClick={() => onDelete(r.id)}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      ))}

      {/* Add row */}
      <div className="flex items-center gap-2 pt-1">
        <Select value={category} onValueChange={setCategory}>
          <SelectTrigger className="w-36 shrink-0">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {categories.map((c) => (
              <SelectItem key={c} value={c}>
                {c}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          placeholder="Add a rule the agents must follow…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
        />
        <Button onClick={submit} size="sm" disabled={!text.trim()}>
          <Plus className="h-4 w-4 mr-1" /> Add
        </Button>
      </div>
    </div>
  );
}
