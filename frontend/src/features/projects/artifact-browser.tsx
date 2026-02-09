"use client";

import { useState, useEffect } from "react";
import type { FileTreeNode } from "@/lib/types";
import { api } from "@/lib/api";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ChevronRight, ChevronDown, File, Folder } from "lucide-react";
import { cn } from "@/lib/utils";

interface ArtifactBrowserProps {
  projectName: string;
}

export function ArtifactBrowser({ projectName }: ArtifactBrowserProps) {
  const [tree, setTree] = useState<FileTreeNode | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.getArtifacts(projectName).then(setTree).catch(console.error);
  }, [projectName]);

  async function handleFileClick(path: string) {
    setSelectedFile(path);
    setLoading(true);
    try {
      const res = await api.getArtifactContent(projectName, path);
      setContent(res.content);
    } catch {
      setContent("(failed to load file)");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex border rounded-lg h-[500px]">
      {/* File tree panel */}
      <ScrollArea className="w-64 border-r p-2">
        {tree ? (
          <TreeNode
            node={tree}
            depth={0}
            selectedFile={selectedFile}
            onFileClick={handleFileClick}
          />
        ) : (
          <p className="text-xs text-muted-foreground p-2">No files yet</p>
        )}
      </ScrollArea>

      {/* Content panel */}
      <ScrollArea className="flex-1 p-4">
        {selectedFile ? (
          <div>
            <div className="mb-2 text-xs text-muted-foreground font-mono">
              {selectedFile}
            </div>
            {loading ? (
              <p className="text-sm text-muted-foreground">Loading...</p>
            ) : (
              <pre className="text-xs font-mono whitespace-pre-wrap break-words">
                {content}
              </pre>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            Select a file to view its contents
          </p>
        )}
      </ScrollArea>
    </div>
  );
}

function TreeNode({
  node,
  depth,
  selectedFile,
  onFileClick,
}: {
  node: FileTreeNode;
  depth: number;
  selectedFile: string | null;
  onFileClick: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState(depth < 2);

  if (node.type === "file") {
    return (
      <button
        onClick={() => onFileClick(node.path)}
        className={cn(
          "flex items-center gap-1.5 w-full text-left py-0.5 px-1 rounded text-xs hover:bg-muted",
          selectedFile === node.path && "bg-primary/10 text-primary"
        )}
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
      >
        <File className="h-3 w-3 shrink-0" />
        <span className="truncate">{node.name}</span>
      </button>
    );
  }

  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 w-full text-left py-0.5 px-1 rounded text-xs hover:bg-muted font-medium"
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 shrink-0" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0" />
        )}
        <Folder className="h-3 w-3 shrink-0" />
        <span className="truncate">{node.name}</span>
      </button>
      {expanded &&
        node.children?.map((child) => (
          <TreeNode
            key={child.path}
            node={child}
            depth={depth + 1}
            selectedFile={selectedFile}
            onFileClick={onFileClick}
          />
        ))}
    </div>
  );
}
