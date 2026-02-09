"use client";

import { useEffect, useRef } from "react";
import { useStore } from "@/lib/store";
import { api } from "@/lib/api";
import type { SSEEvent, SandboxPhaseEvent } from "@/lib/types";

export function useSSE() {
  const addEvent = useStore((s) => s.addEvent);
  const updateProjectFromEvent = useStore((s) => s.updateProjectFromEvent);
  const setConnected = useStore((s) => s.setConnected);
  const setProjects = useStore((s) => s.setProjects);
  const appendChunk = useStore((s) => s.appendChunk);
  const clearStreamingChunk = useStore((s) => s.clearStreamingChunk);
  const setThinkingAgent = useStore((s) => s.setThinkingAgent);
  const clearThinkingAgent = useStore((s) => s.clearThinkingAgent);
  const addSandboxPhase = useStore((s) => s.addSandboxPhase);
  const clearSandboxPhases = useStore((s) => s.clearSandboxPhases);
  const setSandboxActive = useStore((s) => s.setSandboxActive);
  const clearSandboxActive = useStore((s) => s.clearSandboxActive);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closingRef = useRef(false);

  useEffect(() => {
    let es: EventSource | null = null;
    closingRef.current = false;

    function connect() {
      if (closingRef.current) return;

      const sseUrl = typeof window !== "undefined"
        ? `${window.location.protocol}//${window.location.hostname}:8000/api/events`
        : "/api/events";
      es = new EventSource(sseUrl);

      es.onopen = () => {
        setConnected(true);
        api.listProjects().then(setProjects).catch(() => {});
      };

      es.onerror = () => {
        setConnected(false);
        if (closingRef.current) return;
        es?.close();
        reconnectTimer.current = setTimeout(connect, 5000);
      };

      // High-frequency streaming events — routed directly to store,
      // bypassing the events array to prevent overflow.
      es.addEventListener("conversation_chunk", (e: MessageEvent) => {
        try {
          const event: SSEEvent = JSON.parse(e.data);
          const project = event.data?.project as string;
          const agent = event.data?.agent as string;
          const chunk = event.data?.chunk as string;
          if (project && agent && chunk) {
            appendChunk(project, agent, chunk);
          }
        } catch { /* ignore */ }
      });

      es.addEventListener("agent_thinking", (e: MessageEvent) => {
        try {
          const event: SSEEvent = JSON.parse(e.data);
          const project = event.data?.project as string;
          const agent = event.data?.agent as string;
          if (project && agent) {
            setThinkingAgent(project, agent);
          }
        } catch { /* ignore */ }
      });

      es.addEventListener("conversation_update", (e: MessageEvent) => {
        try {
          const event: SSEEvent = JSON.parse(e.data);
          clearStreamingChunk();
          clearThinkingAgent();
          addEvent(event);
          updateProjectFromEvent(event);
        } catch { /* ignore */ }
      });

      // Sandbox verification events
      es.addEventListener("sandbox_start", (e: MessageEvent) => {
        try {
          const event: SSEEvent = JSON.parse(e.data);
          const project = event.data?.project as string;
          const iteration = event.data?.iteration as number;
          if (project) {
            clearSandboxPhases();
            setSandboxActive(project, iteration);
          }
          addEvent(event);
        } catch { /* ignore */ }
      });

      es.addEventListener("sandbox_phase", (e: MessageEvent) => {
        try {
          const event: SSEEvent = JSON.parse(e.data);
          addSandboxPhase(event.data as unknown as SandboxPhaseEvent);
          addEvent(event);
        } catch { /* ignore */ }
      });

      // Clear sandbox active state when score arrives (verification LLM report done)
      es.addEventListener("score_update", (e: MessageEvent) => {
        try {
          const event: SSEEvent = JSON.parse(e.data);
          clearSandboxActive();
          addEvent(event);
          updateProjectFromEvent(event);
        } catch { /* ignore */ }
      });

      // All other event types go through the normal events store
      const otherTypes = [
        "phase_change",
        "log",
        "project_complete",
        "project_failed",
        "usage_update",
        "iteration_update",
      ];

      for (const type of otherTypes) {
        es.addEventListener(type, (e: MessageEvent) => {
          try {
            const event: SSEEvent = JSON.parse(e.data);
            addEvent(event);
            updateProjectFromEvent(event);
          } catch { /* ignore */ }
        });
      }
    }

    function handleBeforeUnload() {
      closingRef.current = true;
      es?.close();
    }
    window.addEventListener("beforeunload", handleBeforeUnload);

    connect();

    return () => {
      closingRef.current = true;
      window.removeEventListener("beforeunload", handleBeforeUnload);
      es?.close();
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    };
  }, [
    addEvent, updateProjectFromEvent, setConnected, setProjects,
    appendChunk, clearStreamingChunk, setThinkingAgent, clearThinkingAgent,
    addSandboxPhase, clearSandboxPhases, setSandboxActive, clearSandboxActive,
  ]);
}
