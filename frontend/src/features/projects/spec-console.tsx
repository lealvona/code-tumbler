"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { usePolling } from "@/hooks/use-polling";
import type { SpecInfo, SpecLayer, SpecLayerStatus } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useToast } from "@/hooks/use-toast";
import { Download, Upload, Save, FileCode2 } from "lucide-react";

interface Row {
  path: string;
  status: SpecLayerStatus;
  snippet?: string;
}

const STATUS_RANK: Record<SpecLayerStatus, number> = {
  pending: 0,
  start: 1,
  writing: 2,
  done: 3,
};

function StatusDot({ status }: { status: SpecLayerStatus }) {
  if (status === "done") {
    return <span className="text-emerald-500">✓</span>;
  }
  if (status === "writing" || status === "start") {
    return (
      <span className="relative flex h-2.5 w-2.5">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-purple-400 opacity-75" />
        <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-purple-500" />
      </span>
    );
  }
  return <span className="text-muted-foreground/50">○</span>;
}

export function SpecConsole({
  projectName,
  isRunning,
}: {
  projectName: string;
  isRunning: boolean;
}) {
  const [info, setInfo] = useState<SpecInfo | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const liveLayers = useStore((s) => s.specLayers[projectName]);
  const { toast } = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadInfo = useCallback(() => {
    api.getSpec(projectName).then(setInfo).catch(() => {});
  }, [projectName]);

  useEffect(() => {
    loadInfo();
  }, [loadInfo]);

  // Poll while the spec is being generated (paused when the tab is hidden).
  usePolling(loadInfo, 3000, isRunning);

  // Merge the required order, files on disk, and the live streaming layers into
  // one ordered list of rows with a resolved status.
  const rows: Row[] = useMemo(() => {
    const live = new Map<string, SpecLayer>();
    (liveLayers ?? []).forEach((l) => live.set(l.path, l));
    const onDisk = new Set((info?.files ?? []).map((f) => f.path));

    const order: string[] = [];
    const push = (p: string) => {
      if (!order.includes(p)) order.push(p);
    };
    (info?.required ?? []).forEach(push);
    (liveLayers ?? []).forEach((l) => push(l.path));
    (info?.files ?? []).forEach((f) => push(f.path));

    return order.map((path) => {
      const l = live.get(path);
      let status: SpecLayerStatus = "pending";
      if (onDisk.has(path)) status = "done";
      if (l && STATUS_RANK[l.status] >= STATUS_RANK[status]) status = l.status;
      return { path, status, snippet: l?.snippet };
    });
  }, [info, liveLayers]);

  const writingRow = rows.find((r) => r.status === "writing" || r.status === "start");
  const doneCount = rows.filter((r) => r.status === "done").length;

  async function openFile(path: string) {
    setSelected(path);
    setDirty(false);
    try {
      const f = await api.getSpecFile(projectName, path);
      setContent(f.content);
    } catch {
      setContent("(could not load file — it may not be written yet)");
    }
  }

  async function save() {
    if (!selected) return;
    setSaving(true);
    try {
      await api.saveSpecFile(projectName, selected, content);
      setDirty(false);
      toast({ title: "Saved", description: selected });
    } catch (e) {
      toast({
        variant: "destructive",
        title: "Save failed",
        description: e instanceof Error ? e.message : "error",
      });
    } finally {
      setSaving(false);
    }
  }

  async function exportArchive() {
    try {
      const archive = await api.exportSpec(projectName);
      const blob = new Blob([JSON.stringify(archive, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${projectName}-spec-archive.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast({ title: "Exported", description: "Spec archive downloaded." });
    } catch (e) {
      toast({
        variant: "destructive",
        title: "Export failed",
        description: e instanceof Error ? e.message : "No spec to export.",
      });
    }
  }

  async function onImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const archive = JSON.parse(await file.text());
      const res = await api.importSpec(projectName, archive);
      toast({
        title: "Imported",
        description: `${res.files} files${res.complete ? " (complete)" : ""}.`,
      });
      loadInfo();
      if (selected) openFile(selected);
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Import failed",
        description: err instanceof Error ? err.message : "Invalid archive.",
      });
    }
  }

  const hasSpec = rows.some((r) => r.status !== "pending");

  return (
    <Card className="flex flex-col">
      <CardHeader className="flex-row items-center justify-between gap-2 space-y-0">
        <div>
          <CardTitle className="text-sm flex items-center gap-2">
            <FileCode2 className="h-4 w-4 text-purple-500" />
            Specification Suite
          </CardTitle>
          <p className="text-xs text-muted-foreground mt-1">
            Phase 1 — a one-line idea becomes an ordered YAML spec the Architect builds from.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {info && (
            <Badge variant={info.complete ? "secondary" : "outline"} className="text-[10px]">
              {doneCount}/{rows.length || info.required.length} layers
            </Badge>
          )}
          <Button variant="outline" size="sm" onClick={exportArchive} disabled={!hasSpec}>
            <Download className="h-3.5 w-3.5 mr-1" /> Export
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => fileInputRef.current?.click()}
            disabled={isRunning}
          >
            <Upload className="h-3.5 w-3.5 mr-1" /> Import
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={onImportFile}
          />
        </div>
      </CardHeader>

      <CardContent>
        {/* Live "thinking" line while a layer streams */}
        {isRunning && writingRow && (
          <div className="mb-3 rounded-md border border-purple-200 dark:border-purple-900 bg-purple-50 dark:bg-purple-950/40 px-3 py-2">
            <div className="text-xs font-medium text-purple-700 dark:text-purple-300">
              Writing {writingRow.path}
            </div>
            {writingRow.snippet && (
              <div className="mt-1 font-mono text-[11px] text-purple-600/80 dark:text-purple-400/80 truncate">
                …{writingRow.snippet}
              </div>
            )}
          </div>
        )}

        {!hasSpec && !isRunning && (
          <p className="text-sm text-muted-foreground py-8 text-center">
            No spec suite yet. Start the project to generate one, or import an archive.
          </p>
        )}

        {(hasSpec || isRunning) && (
          // Grid app-shell: the min-h-0 on the grid AND on its scrolling children
          // is what lets the editor scroll internally (Step-2 grid fix).
          <div className="grid grid-cols-1 md:grid-cols-[240px_1fr] gap-4 h-[560px] min-h-0">
            {/* Animated layer index */}
            <div className="min-h-0 overflow-y-auto rounded-md border bg-muted/30 p-1">
              {rows.map((r) => (
                <button
                  key={r.path}
                  onClick={() => openFile(r.path)}
                  className={cn(
                    "w-full flex items-center gap-2 px-2 py-1.5 rounded text-left text-xs transition-colors",
                    selected === r.path ? "bg-purple-100 dark:bg-purple-900/40" : "hover:bg-muted",
                    r.status === "pending" && "opacity-50"
                  )}
                >
                  <span className="w-3 flex justify-center shrink-0">
                    <StatusDot status={r.status} />
                  </span>
                  <span className="font-mono truncate">
                    {r.path.replace(/^spec\//, "")}
                  </span>
                </button>
              ))}
            </div>

            {/* Editor / viewer — scrolls internally thanks to min-h-0 above */}
            <div className="min-h-0 flex flex-col">
              {selected ? (
                <>
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-mono text-xs text-muted-foreground truncate">
                      {selected}
                    </span>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={save}
                      disabled={!dirty || saving || isRunning}
                    >
                      <Save className="h-3.5 w-3.5 mr-1" />
                      {saving ? "Saving…" : "Save"}
                    </Button>
                  </div>
                  <textarea
                    value={content}
                    onChange={(e) => {
                      setContent(e.target.value);
                      setDirty(true);
                    }}
                    readOnly={isRunning}
                    spellCheck={false}
                    className="flex-1 min-h-0 w-full resize-none rounded-md border bg-background p-3 font-mono text-xs leading-relaxed focus:outline-none focus:ring-1 focus:ring-purple-400"
                  />
                </>
              ) : (
                <div className="flex-1 min-h-0 flex items-center justify-center text-sm text-muted-foreground border rounded-md">
                  Select a layer to view or edit its YAML.
                </div>
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
