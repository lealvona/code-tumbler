"use client";

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { ConversationMessage, SSEEvent, SandboxPhaseEvent } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { SandboxOutput } from "./sandbox-output";

interface AgentConversationProps {
  projectName: string;
  isRunning: boolean;
}

const AGENT_CONFIG: Record<
  string,
  {
    label: string;
    icon: string;
    bg: string;
    border: string;
    text: string;
    accent: string;
    headerBg: string;
    badgeBg: string;
    badgeText: string;
    ring: string;
    prose: string;
  }
> = {
  system: {
    label: "System",
    icon: "\u2699\ufe0f",
    bg: "bg-slate-100 dark:bg-slate-800/60",
    border:
      "border-l-4 border-l-slate-400 dark:border-l-slate-500 border border-slate-200 dark:border-slate-700",
    text: "text-slate-800 dark:text-slate-200",
    accent: "text-slate-600 dark:text-slate-400",
    headerBg: "bg-slate-200/60 dark:bg-slate-700/40",
    badgeBg: "bg-slate-500",
    badgeText: "text-white",
    ring: "ring-slate-400",
    prose: "prose-slate",
  },
  architect: {
    label: "Architect",
    icon: "\ud83d\udcd0",
    bg: "bg-blue-50 dark:bg-blue-950/40",
    border:
      "border-l-4 border-l-blue-500 dark:border-l-blue-400 border border-blue-200 dark:border-blue-800",
    text: "text-blue-950 dark:text-blue-100",
    accent: "text-blue-600 dark:text-blue-400",
    headerBg: "bg-blue-100/80 dark:bg-blue-900/40",
    badgeBg: "bg-blue-600 dark:bg-blue-500",
    badgeText: "text-white",
    ring: "ring-blue-400",
    prose: "prose-blue",
  },
  engineer: {
    label: "Engineer",
    icon: "\ud83d\udee0\ufe0f",
    bg: "bg-emerald-50 dark:bg-emerald-950/40",
    border:
      "border-l-4 border-l-emerald-500 dark:border-l-emerald-400 border border-emerald-200 dark:border-emerald-800",
    text: "text-emerald-950 dark:text-emerald-100",
    accent: "text-emerald-600 dark:text-emerald-400",
    headerBg: "bg-emerald-100/80 dark:bg-emerald-900/40",
    badgeBg: "bg-emerald-600 dark:bg-emerald-500",
    badgeText: "text-white",
    ring: "ring-emerald-400",
    prose: "prose-emerald",
  },
  verifier: {
    label: "Verifier",
    icon: "\ud83d\udd0d",
    bg: "bg-amber-50 dark:bg-amber-950/40",
    border:
      "border-l-4 border-l-amber-500 dark:border-l-amber-400 border border-amber-200 dark:border-amber-800",
    text: "text-amber-950 dark:text-amber-100",
    accent: "text-amber-600 dark:text-amber-400",
    headerBg: "bg-amber-100/80 dark:bg-amber-900/40",
    badgeBg: "bg-amber-600 dark:bg-amber-500",
    badgeText: "text-white",
    ring: "ring-amber-400",
    prose: "prose-amber",
  },
};

function MarkdownContent({
  content,
  className,
}: {
  content: string;
  className?: string;
}) {
  return (
    <div
      className={`prose prose-sm dark:prose-invert max-w-none break-words [overflow-wrap:anywhere]
        prose-headings:mt-3 prose-headings:mb-1 prose-headings:text-sm
        prose-p:my-1 prose-p:leading-relaxed
        prose-ul:my-1 prose-ol:my-1 prose-li:my-0
        prose-pre:bg-black/10 dark:prose-pre:bg-white/10 prose-pre:rounded-md prose-pre:text-xs prose-pre:p-2 prose-pre:whitespace-pre-wrap prose-pre:break-words
        prose-code:text-xs prose-code:bg-black/10 dark:prose-code:bg-white/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-code:break-all
        prose-table:text-xs
        prose-a:text-blue-600 dark:prose-a:text-blue-400
        ${className ?? ""}`}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

function StreamingContent({
  content,
  isNew,
  className,
}: {
  content: string;
  isNew: boolean;
  className?: string;
}) {
  const [displayed, setDisplayed] = useState(isNew ? "" : content);
  const [done, setDone] = useState(!isNew);

  useEffect(() => {
    if (!isNew) {
      setDisplayed(content);
      setDone(true);
      return;
    }

    let i = 0;
    const len = content.length;
    // Stream in chunks for performance
    const chunkSize = Math.max(1, Math.floor(len / 200));
    const interval = setInterval(() => {
      i += chunkSize;
      if (i >= len) {
        setDisplayed(content);
        setDone(true);
        clearInterval(interval);
      } else {
        setDisplayed(content.slice(0, i));
      }
    }, 8);

    return () => clearInterval(interval);
  }, [content, isNew]);

  return (
    <div className="relative">
      <MarkdownContent content={displayed} className={className} />
      {!done && (
        <span className="inline-block w-2 h-4 bg-current animate-pulse ml-0.5 align-text-bottom" />
      )}
    </div>
  );
}

function ThinkingIndicator({ agent }: { agent: string }) {
  const cfg = AGENT_CONFIG[agent] || AGENT_CONFIG.system;
  return (
    <div className={`rounded-lg ${cfg.border} ${cfg.bg} p-4 animate-pulse`}>
      <div className="flex items-center gap-3">
        <span className="text-lg">{cfg.icon}</span>
        <span className={`text-sm font-semibold ${cfg.accent}`}>
          {cfg.label} is thinking...
        </span>
        <div className="flex gap-1">
          <span
            className={`w-2 h-2 rounded-full ${cfg.badgeBg} animate-bounce`}
            style={{ animationDelay: "0ms" }}
          />
          <span
            className={`w-2 h-2 rounded-full ${cfg.badgeBg} animate-bounce`}
            style={{ animationDelay: "150ms" }}
          />
          <span
            className={`w-2 h-2 rounded-full ${cfg.badgeBg} animate-bounce`}
            style={{ animationDelay: "300ms" }}
          />
        </div>
      </div>
    </div>
  );
}

function AgentBubble({
  message,
  isNew,
}: {
  message: ConversationMessage;
  isNew: boolean;
}) {
  const cfg = AGENT_CONFIG[message.agent] || AGENT_CONFIG.system;
  const isError = message.role === "error";
  const isStatus = message.role === "status";

  const label =
    message.metadata?.label || `${message.agent} ${message.role}`;
  const time = new Date(message.timestamp).toLocaleTimeString();

  // Status messages are rendered as compact inline messages
  if (isStatus) {
    return (
      <div
        className={`rounded-md px-4 py-2 flex items-center gap-2 ${cfg.bg} border ${cfg.border} opacity-70 ${isNew ? "animate-in fade-in slide-in-from-bottom-2 duration-500" : ""}`}
      >
        <span className="text-sm">{cfg.icon}</span>
        <span className={`text-xs ${cfg.accent}`}>{message.content}</span>
        <span className="text-[10px] text-muted-foreground font-mono ml-auto">
          {time}
        </span>
      </div>
    );
  }

  return (
    <div
      className={`rounded-lg ${isError ? "border-l-4 border-l-red-500 border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/40" : `${cfg.border} ${cfg.bg}`} overflow-hidden transition-all duration-300 ${isNew ? "animate-in fade-in slide-in-from-bottom-2 duration-500" : ""}`}
    >
      {/* Header bar */}
      <div
        className={`${isError ? "bg-red-100/80 dark:bg-red-900/40" : cfg.headerBg} px-4 py-2 flex items-center justify-between`}
      >
        <div className="flex items-center gap-2">
          <span className="text-base">{isError ? "\u26a0\ufe0f" : cfg.icon}</span>
          <span className={`text-sm font-bold ${isError ? "text-red-600 dark:text-red-400" : cfg.accent}`}>
            {cfg.label}
          </span>
          <Badge
            className={`${isError ? "bg-red-600 text-white" : `${cfg.badgeBg} ${cfg.badgeText}`} text-[10px] px-2 py-0 h-5 border-0`}
          >
            {label}
          </Badge>
          {message.iteration > 0 && (
            <Badge
              variant="outline"
              className={`text-[10px] px-2 py-0 h-5 ${cfg.accent} border-current`}
            >
              Iter {message.iteration}
            </Badge>
          )}
          {message.metadata?.score !== undefined && (
            <Badge
              className={`text-[10px] px-2 py-0 h-5 border-0 ${
                message.metadata.score >= 8
                  ? "bg-green-600 text-white"
                  : message.metadata.score >= 5
                    ? "bg-yellow-600 text-white"
                    : "bg-red-600 text-white"
              }`}
            >
              Score: {message.metadata.score}/10
            </Badge>
          )}
          {message.metadata?.file_count !== undefined && (
            <Badge
              variant="outline"
              className={`text-[10px] px-2 py-0 h-5 ${cfg.accent} border-current`}
            >
              {message.metadata.file_count} files
            </Badge>
          )}
        </div>
        <span className="text-[10px] text-muted-foreground font-mono">
          {time}
        </span>
      </div>

      {/* Content */}
      <div className="px-4 py-3 max-h-[600px] overflow-auto">
        <StreamingContent
          content={message.content}
          isNew={isNew}
          className={isError ? "text-red-800 dark:text-red-200" : cfg.text}
        />
      </div>
    </div>
  );
}

function StreamingBubble({
  agent,
  content,
}: {
  agent: string;
  content: string;
}) {
  const cfg = AGENT_CONFIG[agent] || AGENT_CONFIG.system;
  return (
    <div
      className={`rounded-lg ${cfg.border} ${cfg.bg} overflow-hidden animate-in fade-in slide-in-from-bottom-2 duration-500`}
    >
      <div
        className={`${cfg.headerBg} px-4 py-2 flex items-center justify-between`}
      >
        <div className="flex items-center gap-2">
          <span className="text-base">{cfg.icon}</span>
          <span className={`text-sm font-bold ${cfg.accent}`}>
            {cfg.label}
          </span>
          <Badge
            className={`${cfg.badgeBg} ${cfg.badgeText} text-[10px] px-2 py-0 h-5 border-0`}
          >
            Generating...
          </Badge>
        </div>
        <span className="inline-block w-2 h-4 bg-current animate-pulse ml-0.5 align-text-bottom opacity-60" />
      </div>
      <div className="px-4 py-3 max-h-[600px] overflow-auto">
        <MarkdownContent content={content} className={cfg.text} />
        <span className="inline-block w-2 h-4 bg-current animate-pulse ml-0.5 align-text-bottom" />
      </div>
    </div>
  );
}

export function AgentConversation({
  projectName,
  isRunning,
}: AgentConversationProps) {
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [prevCount, setPrevCount] = useState(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const isNearBottomRef = useRef(true);

  // Read streaming state directly from the store (decoupled from events array)
  const streamingChunk = useStore((s) => s.streamingChunk);
  const thinkingAgent = useStore((s) => s.thinkingAgent);
  const events = useStore((s) => s.events);
  const lastEventCountRef = useRef(0);

  // Sandbox live state
  const sandboxPhases = useStore((s) => s.sandboxPhases);
  const sandboxActive = useStore((s) => s.sandboxActive);
  const activeSandbox =
    sandboxActive && sandboxActive.project === projectName
      ? sandboxActive
      : null;

  // Derived: is this project currently streaming / thinking?
  const activeStreaming =
    streamingChunk && streamingChunk.project === projectName
      ? streamingChunk
      : null;
  const activeThinking =
    thinkingAgent && thinkingAgent.project === projectName
      ? thinkingAgent
      : null;

  const fetchConversation = useCallback(() => {
    api
      .getConversation(projectName)
      .then((msgs) => {
        setPrevCount(msgs.length);
        setMessages(msgs);
      })
      .catch(() => {});
  }, [projectName]);

  // Fetch on mount and periodically when running (but not while streaming)
  useEffect(() => {
    fetchConversation();
    if (isRunning && !activeStreaming) {
      const interval = setInterval(fetchConversation, 3000);
      return () => clearInterval(interval);
    }
  }, [projectName, isRunning, activeStreaming, fetchConversation]);

  // Refetch conversation when a conversation_update event arrives for this project
  useEffect(() => {
    if (events.length === 0) return;
    if (events.length === lastEventCountRef.current) return;

    const newEvents = events.slice(lastEventCountRef.current);
    lastEventCountRef.current = events.length;

    const hasUpdate = newEvents.some(
      (evt: SSEEvent) =>
        evt.type === "conversation_update" &&
        evt.data?.project === projectName
    );
    if (hasUpdate) {
      fetchConversation();
    }
  }, [events, projectName, fetchConversation]);

  // Track whether user has scrolled up from the bottom
  const handleScroll = useCallback(() => {
    const vp = viewportRef.current;
    if (!vp) return;
    // Consider "near bottom" if within 80px of the bottom edge
    const threshold = 80;
    isNearBottomRef.current =
      vp.scrollHeight - vp.scrollTop - vp.clientHeight < threshold;
  }, []);

  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp) return;
    vp.addEventListener("scroll", handleScroll, { passive: true });
    return () => vp.removeEventListener("scroll", handleScroll);
  }, [handleScroll]);

  // Auto-scroll only when user is already at the bottom
  useEffect(() => {
    if (isNearBottomRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages.length, activeThinking, activeStreaming]);

  // Group consecutive role="sandbox" messages (same iteration) into SandboxOutput blocks
  type RenderItem =
    | { kind: "message"; msg: ConversationMessage; index: number }
    | { kind: "sandbox"; phases: SandboxPhaseEvent[]; iteration: number; key: string };

  const renderItems = useMemo<RenderItem[]>(() => {
    const items: RenderItem[] = [];
    let i = 0;
    while (i < messages.length) {
      const msg = messages[i];
      if (msg.role === "sandbox" && msg.metadata?.sandbox_phase) {
        const iteration = msg.iteration;
        const phases: SandboxPhaseEvent[] = [];
        while (
          i < messages.length &&
          messages[i].role === "sandbox" &&
          messages[i].iteration === iteration &&
          messages[i].metadata?.sandbox_phase
        ) {
          const m = messages[i];
          phases.push({
            project: projectName,
            phase: m.metadata!.sandbox_phase!,
            status: m.metadata!.sandbox_status || "skipped",
            iteration: m.iteration,
            stdout: m.content,
            stderr: "",
            exit_code: m.metadata!.exit_code ?? 0,
            duration_s: m.metadata!.duration_s ?? 0,
            commands: m.metadata!.commands ?? [],
          });
          i++;
        }
        items.push({
          kind: "sandbox",
          phases,
          iteration,
          key: `sandbox-${iteration}-${phases[0]?.phase}`,
        });
      } else {
        items.push({ kind: "message", msg, index: i });
        i++;
      }
    }
    return items;
  }, [messages, projectName]);

  if (messages.length === 0 && !activeThinking && !activeStreaming) {
    return (
      <Card>
        <CardContent className="pt-6">
          <p className="text-sm text-muted-foreground text-center">
            {isRunning
              ? "Waiting for agent activity..."
              : "No conversation yet. Start the project to see agent interactions."}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center justify-between">
          <span>Agent Conversation</span>
          <div className="flex gap-4 text-xs font-normal">
            {Object.entries(AGENT_CONFIG).map(([key, cfg]) => (
              <span key={key} className="flex items-center gap-1.5">
                <span className="text-sm">{cfg.icon}</span>
                <span className={`font-medium ${cfg.accent}`}>
                  {cfg.label}
                </span>
              </span>
            ))}
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-[600px] pr-4" viewportRef={viewportRef}>
          <div className="space-y-3">
            {renderItems.map((item) =>
              item.kind === "sandbox" ? (
                <SandboxOutput
                  key={item.key}
                  phases={item.phases}
                  isLive={false}
                />
              ) : (
                <AgentBubble
                  key={`${item.msg.timestamp}-${item.index}`}
                  message={item.msg}
                  isNew={
                    item.index >= prevCount - 1 &&
                    item.index === messages.length - 1
                  }
                />
              )
            )}
            {activeSandbox && isRunning && (
              <SandboxOutput phases={sandboxPhases} isLive={true} />
            )}
            {activeThinking && isRunning && !activeStreaming && (
              <ThinkingIndicator agent={activeThinking.agent} />
            )}
            {activeStreaming && isRunning && (
              <StreamingBubble
                agent={activeStreaming.agent}
                content={activeStreaming.content}
              />
            )}
            <div ref={bottomRef} />
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
