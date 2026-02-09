"use client";

import { useState } from "react";
import Link from "next/link";
import type { ProjectSummary } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useToast } from "@/hooks/use-toast";
import { PhaseIndicator } from "./phase-indicator";

const statusColors: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  idle: "secondary",
  planning: "default",
  engineering: "default",
  verifying: "default",
  completed: "outline",
  failed: "destructive",
};

interface ProjectCardProps {
  project: ProjectSummary;
  onRefresh: () => void;
}

export function ProjectCard({ project, onRefresh }: ProjectCardProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const { toast } = useToast();

  async function handleDelete() {
    setDeleting(true);
    try {
      await api.deleteProject(project.name);
      toast({ title: "Project deleted", description: `${project.name} has been deleted.` });
      onRefresh();
    } catch (e) {
      toast({
        variant: "destructive",
        title: "Failed to delete",
        description: e instanceof Error ? e.message : "Unknown error",
      });
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  return (
    <>
      <Link href={`/projects/${project.name}`}>
        <Card className="hover:border-primary/50 transition-colors cursor-pointer h-full">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm truncate">{project.name}</CardTitle>
              <div className="flex items-center gap-1">
                <Badge variant={statusColors[project.status] ?? "secondary"}>
                  {project.status}
                </Badge>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 text-muted-foreground hover:text-destructive"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setConfirmDelete(true);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            <PhaseIndicator phase={project.status} />
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>Iteration: {project.iteration}</span>
              {project.last_score !== null && (
                <span>Score: {project.last_score}/10</span>
              )}
            </div>
            {project.last_update && (
              <p className="text-[10px] text-muted-foreground">
                Updated: {new Date(project.last_update).toLocaleString()}
              </p>
            )}
          </CardContent>
        </Card>
      </Link>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Project</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Are you sure you want to delete <strong>{project.name}</strong>? This will permanently remove all files, artifacts, and history. This action cannot be undone.
          </p>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="outline" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
              {deleting ? "Deleting..." : "Delete"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
