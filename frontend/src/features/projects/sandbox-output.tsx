"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import type { SandboxPhaseEvent } from "@/lib/types";

interface SandboxOutputProps {
  phases: SandboxPhaseEvent[];
  isLive: boolean;
}

const PHASE_ORDER = ["install", "build", "test", "lint"] as const;

const PHASE_META: Record<
  string,
  { label: string; icon: string }
> = {
  install: { label: "Install", icon: "\ud83d\udce6" },
  build: { label: "Build", icon: "\ud83d\udd28" },
  test: { label: "Test", icon: "\ud83e\uddea" },
  lint: { label: "Lint", icon: "\ud83d\udd0d" },
};

function StatusBadge({ status }: { status: string }) {
  switch (status) {
    case "success":
      return (
        <Badge className="bg-green-600 text-white text-[10px] px-2 py-0 h-5 border-0">
          PASS
        </Badge>
      );
    case "failed":
      return (
        <Badge className="bg-red-600 text-white text-[10px] px-2 py-0 h-5 border-0">
          FAIL
        </Badge>
      );
    case "timeout":
      return (
        <Badge className="bg-yellow-600 text-white text-[10px] px-2 py-0 h-5 border-0">
          TIMEOUT
        </Badge>
      );
    case "skipped":
      return (
        <Badge className="bg-slate-500 text-white text-[10px] px-2 py-0 h-5 border-0">
          SKIP
        </Badge>
      );
    default:
      return null;
  }
}

function PhaseRow({
  phase,
  data,
  pending,
}: {
  phase: string;
  data: SandboxPhaseEvent | null;
  pending: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const meta = PHASE_META[phase] || { label: phase, icon: "\u2699\ufe0f" };

  return (
    <div className="border-b border-amber-200/50 dark:border-amber-800/30 last:border-b-0">
      <button
        className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-amber-100/50 dark:hover:bg-amber-900/20 transition-colors"
        onClick={() => data && setExpanded(!expanded)}
        disabled={!data}
      >
        <span className="text-sm">{meta.icon}</span>
        <span className="text-xs font-medium text-amber-800 dark:text-amber-200 w-16">
          {meta.label}
        </span>
        {data ? (
          <>
            <StatusBadge status={data.status} />
            <span className="text-[10px] text-muted-foreground ml-auto font-mono">
              {data.duration_s.toFixed(1)}s
            </span>
            <span className="text-[10px] text-muted-foreground">
              {expanded ? "\u25b2" : "\u25bc"}
            </span>
          </>
        ) : pending ? (
          <span className="text-xs text-amber-500 animate-pulse ml-auto">
            Waiting...
          </span>
        ) : (
          <span className="text-xs text-muted-foreground ml-auto">--</span>
        )}
      </button>
      {expanded && data && (data.stdout || data.stderr) && (
        <div className="px-3 pb-2">
          <pre className="text-[11px] leading-relaxed bg-black/80 text-green-400 p-3 rounded-md overflow-auto max-h-[300px] whitespace-pre-wrap break-words">
            {data.stdout}
            {data.stderr ? `\n--- stderr ---\n${data.stderr}` : ""}
          </pre>
          {data.commands.length > 0 && (
            <p className="text-[10px] text-muted-foreground mt-1 font-mono">
              $ {data.commands.join(" && ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export function SandboxOutput({ phases, isLive }: SandboxOutputProps) {
  const phaseMap = new Map(phases.map((p) => [p.phase, p]));

  return (
    <div className="rounded-lg border-l-4 border-l-amber-500 dark:border-l-amber-400 border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 overflow-hidden">
      <div className="bg-amber-100/80 dark:bg-amber-900/40 px-4 py-2 flex items-center gap-2">
        <span className="text-base">{"\ud83d\udce1"}</span>
        <span className="text-sm font-bold text-amber-600 dark:text-amber-400">
          Sandbox Verification
        </span>
        {isLive && (
          <Badge className="bg-amber-600 text-white text-[10px] px-2 py-0 h-5 border-0 animate-pulse">
            Running...
          </Badge>
        )}
      </div>
      <div>
        {PHASE_ORDER.map((phase) => {
          const data = phaseMap.get(phase) || null;
          const pending = isLive && !data;
          return (
            <PhaseRow key={phase} phase={phase} data={data} pending={pending} />
          );
        })}
      </div>
    </div>
  );
}
