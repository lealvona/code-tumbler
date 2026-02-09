"use client";

import { useEffect } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { ProjectList } from "@/features/projects/project-list";

export default function ProjectsPage() {
  const projects = useStore((s) => s.projects);
  const setProjects = useStore((s) => s.setProjects);

  function refresh() {
    api.listProjects().then(setProjects).catch(console.error);
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <div className="p-6">
      <ProjectList projects={projects} onRefresh={refresh} />
    </div>
  );
}
