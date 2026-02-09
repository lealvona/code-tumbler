"use client";

import { useState, useMemo } from "react";
import { Badge } from "@/components/ui/badge";
import { ChevronRight, ChevronDown, FileCode, FolderOpen } from "lucide-react";

interface FileEntry {
  path: string;
  content: string;
}

const EXT_LANG: Record<string, string> = {
  ts: "TypeScript",
  tsx: "TSX",
  js: "JavaScript",
  jsx: "JSX",
  py: "Python",
  rs: "Rust",
  go: "Go",
  java: "Java",
  css: "CSS",
  html: "HTML",
  json: "JSON",
  yaml: "YAML",
  yml: "YAML",
  md: "Markdown",
  sh: "Shell",
  sql: "SQL",
  toml: "TOML",
  dockerfile: "Docker",
};

function getLang(path: string): string | null {
  const name = path.split("/").pop()?.toLowerCase() ?? "";
  if (name === "dockerfile") return "Docker";
  const ext = name.split(".").pop() ?? "";
  return EXT_LANG[ext] ?? null;
}

export function FileManifestViewer({ files }: { files: FileEntry[] }) {
  const [allExpanded, setAllExpanded] = useState(false);

  // Group files by directory for summary
  const dirCount = useMemo(() => {
    const dirs = new Set(
      files.map((f) => {
        const parts = f.path.split("/");
        return parts.length > 1 ? parts.slice(0, -1).join("/") : ".";
      })
    );
    return dirs.size;
  }, [files]);

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between px-1 pb-1">
        <div className="flex items-center gap-2">
          <FolderOpen className="h-3.5 w-3.5 text-emerald-500" />
          <span className="text-xs text-emerald-700 dark:text-emerald-300 font-medium">
            {files.length} file{files.length !== 1 ? "s" : ""} in {dirCount}{" "}
            director{dirCount !== 1 ? "ies" : "y"}
          </span>
        </div>
        <button
          className="text-[10px] text-emerald-600 dark:text-emerald-400 hover:underline"
          onClick={() => setAllExpanded(!allExpanded)}
        >
          {allExpanded ? "Collapse all" : "Expand all"}
        </button>
      </div>
      <div className="rounded-md border border-emerald-200/60 dark:border-emerald-800/40 overflow-hidden">
        {files.map((file) => (
          <FileRowControlled
            key={file.path}
            file={file}
            forceExpanded={allExpanded}
          />
        ))}
      </div>
    </div>
  );
}

/** Variant that responds to the parent's expand-all toggle. */
function FileRowControlled({
  file,
  forceExpanded,
}: {
  file: FileEntry;
  forceExpanded: boolean;
}) {
  const [localToggle, setLocalToggle] = useState<boolean | null>(null);
  const expanded = localToggle ?? forceExpanded;
  const lineCount = file.content.split("\n").length;
  const lang = getLang(file.path);

  // Reset local toggle when parent toggle changes
  const [prevForce, setPrevForce] = useState(forceExpanded);
  if (forceExpanded !== prevForce) {
    setPrevForce(forceExpanded);
    setLocalToggle(null);
  }

  return (
    <div className="border-b border-emerald-200/50 dark:border-emerald-800/30 last:border-b-0">
      <button
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-emerald-100/50 dark:hover:bg-emerald-900/20 transition-colors"
        onClick={() => setLocalToggle(!expanded)}
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400 shrink-0" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400 shrink-0" />
        )}
        <FileCode className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
        <span className="text-xs font-mono text-emerald-900 dark:text-emerald-100 truncate">
          {file.path}
        </span>
        {lang && (
          <Badge
            variant="outline"
            className="text-[10px] px-1.5 py-0 h-4 border-emerald-300 dark:border-emerald-700 text-emerald-600 dark:text-emerald-400 shrink-0"
          >
            {lang}
          </Badge>
        )}
        <span className="text-[10px] text-muted-foreground ml-auto shrink-0">
          {lineCount} lines
        </span>
      </button>
      {expanded && (
        <div className="px-3 pb-2">
          <pre className="text-[11px] leading-relaxed bg-black/80 text-green-400 p-3 rounded-md overflow-auto max-h-[400px] whitespace-pre-wrap break-words">
            {file.content}
          </pre>
        </div>
      )}
    </div>
  );
}

/**
 * Try to parse a string as a file manifest (JSON array of {path, content}).
 * Returns the parsed array or null if it's not a valid manifest.
 */
export function tryParseFileManifest(
  content: string
): FileEntry[] | null {
  try {
    const parsed = JSON.parse(content);
    if (
      Array.isArray(parsed) &&
      parsed.length > 0 &&
      parsed.every(
        (item: unknown) =>
          typeof item === "object" &&
          item !== null &&
          "path" in item &&
          "content" in item &&
          typeof (item as FileEntry).path === "string" &&
          typeof (item as FileEntry).content === "string"
      )
    ) {
      return parsed as FileEntry[];
    }
  } catch {
    // Not JSON — that's fine
  }
  return null;
}
