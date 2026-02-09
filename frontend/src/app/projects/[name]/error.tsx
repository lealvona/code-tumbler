"use client";

import { Button } from "@/components/ui/button";

export default function ProjectError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[50vh] gap-4">
      <h2 className="text-xl font-semibold">Failed to load project</h2>
      <p className="text-sm text-muted-foreground max-w-md text-center">
        {error.message || "Could not load project details."}
      </p>
      <Button onClick={reset}>Try Again</Button>
    </div>
  );
}
