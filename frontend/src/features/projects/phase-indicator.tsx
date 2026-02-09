"use client";

import type { ProjectPhase } from "@/lib/types";
import { cn } from "@/lib/utils";

const phases: { key: ProjectPhase; label: string }[] = [
  { key: "idle", label: "Idle" },
  { key: "planning", label: "Plan" },
  { key: "engineering", label: "Code" },
  { key: "verifying", label: "Verify" },
  { key: "completed", label: "Done" },
];

const phaseOrder: Record<string, number> = {
  idle: 0,
  planning: 1,
  engineering: 2,
  verifying: 3,
  completed: 4,
  failed: -1,
};

export function PhaseIndicator({ phase }: { phase: ProjectPhase }) {
  const currentIdx = phaseOrder[phase] ?? -1;

  if (phase === "failed") {
    return (
      <div className="flex items-center gap-1">
        <span className="text-xs font-medium text-red-500">Failed</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1">
      {phases.map((p, i) => {
        const isActive = i === currentIdx;
        const isPast = i < currentIdx;
        return (
          <div key={p.key} className="flex items-center gap-1">
            {i > 0 && (
              <div
                className={cn(
                  "h-0.5 w-4",
                  isPast ? "bg-primary" : "bg-muted"
                )}
              />
            )}
            <div
              className={cn(
                "flex items-center justify-center rounded-full text-[10px] font-medium",
                isActive
                  ? "h-6 w-6 bg-primary text-primary-foreground"
                  : isPast
                  ? "h-5 w-5 bg-primary/20 text-primary"
                  : "h-5 w-5 bg-muted text-muted-foreground"
              )}
            >
              {i + 1}
            </div>
            {isActive && (
              <span className="text-xs font-medium ml-0.5">{p.label}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}
