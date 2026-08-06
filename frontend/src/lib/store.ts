import { create } from "zustand";
import type {
  ProjectSummary,
  SSEEvent,
  SandboxPhaseEvent,
  ConversationMessage,
  SpecLayer,
} from "./types";

interface StreamingChunk {
  project: string;
  agent: string;
  content: string;
}

// Bounds that keep a long-running session from growing state without limit.
const MAX_STREAM_CHARS = 20_000;   // live preview tail; full text is persisted server-side
const MAX_SANDBOX_PHASES = 40;     // phases across a run
const MAX_SPEC_PROJECTS = 5;       // projects retained in the live spec-layer map

interface AppStore {
  projects: ProjectSummary[];
  setProjects: (projects: ProjectSummary[]) => void;

  events: SSEEvent[];
  addEvent: (event: SSEEvent) => void;
  clearEvents: () => void;

  updateProjectFromEvent: (event: SSEEvent) => void;

  // Streaming chunks — separate from the events array to avoid overflow
  streamingChunk: StreamingChunk | null;
  appendChunk: (project: string, agent: string, chunk: string) => void;
  clearStreamingChunk: () => void;

  // Track which agent is currently thinking (detail = e.g. reasoning-token
  // progress from slow local models)
  thinkingAgent: { project: string; agent: string; detail?: string } | null;
  setThinkingAgent: (project: string, agent: string, detail?: string) => void;
  clearThinkingAgent: () => void;

  // Sandbox verification phases (live updates)
  sandboxPhases: SandboxPhaseEvent[];
  addSandboxPhase: (phase: SandboxPhaseEvent) => void;
  clearSandboxPhases: () => void;
  sandboxActive: { project: string; iteration: number } | null;
  setSandboxActive: (project: string, iteration: number) => void;
  clearSandboxActive: () => void;

  // Conversation cache — persists messages across tab switches
  conversationCache: Record<string, ConversationMessage[]>;
  setConversationCache: (project: string, messages: ConversationMessage[]) => void;

  // Phase-1 spec generation — live animated YAML-layer index per project
  specLayers: Record<string, SpecLayer[]>;
  applySpecLayer: (project: string, layer: SpecLayer) => void;
  clearSpecLayers: (project: string) => void;

  connected: boolean;
  setConnected: (connected: boolean) => void;
}

export const useStore = create<AppStore>((set) => ({
  projects: [],
  setProjects: (projects) => set({ projects }),

  events: [],
  addEvent: (event) =>
    set((state) => ({
      events: [...state.events.slice(-199), event],
    })),
  clearEvents: () => set({ events: [] }),

  updateProjectFromEvent: (event) =>
    set((state) => {
      const projectName = event.data?.project as string | undefined;
      if (!projectName) return state;
      return {
        projects: state.projects.map((p) =>
          p.name === projectName
            ? {
                ...p,
                status:
                  (event.data.phase as ProjectSummary["status"]) ?? p.status,
                last_score:
                  (event.data.score as number | null) ?? p.last_score,
                iteration:
                  (event.data.iteration as number) ?? p.iteration,
              }
            : p
        ),
      };
    }),

  streamingChunk: null,
  appendChunk: (project, agent, chunk) =>
    set((state) => {
      const prev = state.streamingChunk;
      const base =
        prev && prev.project === project && prev.agent === agent ? prev.content : "";
      let content = base + chunk;
      // Keep only the trailing window — this is a live preview; the full text is
      // persisted server-side and re-fetched into the conversation on completion.
      // Prevents an unbounded string (and unbounded markdown re-parse) on long gens.
      if (content.length > MAX_STREAM_CHARS) {
        content = content.slice(content.length - MAX_STREAM_CHARS);
      }
      return { streamingChunk: { project, agent, content } };
    }),
  clearStreamingChunk: () => set({ streamingChunk: null }),

  thinkingAgent: null,
  setThinkingAgent: (project, agent, detail) =>
    set((state) => ({
      thinkingAgent: { project, agent, detail },
      // Reasoning progress updates must not wipe an active output stream.
      streamingChunk: detail ? state.streamingChunk : null,
    })),
  clearThinkingAgent: () => set({ thinkingAgent: null }),

  sandboxPhases: [],
  addSandboxPhase: (phase) =>
    set((state) => ({
      sandboxPhases: [...state.sandboxPhases.slice(-(MAX_SANDBOX_PHASES - 1)), phase],
    })),
  clearSandboxPhases: () => set({ sandboxPhases: [] }),
  sandboxActive: null,
  setSandboxActive: (project, iteration) =>
    set({ sandboxActive: { project, iteration } }),
  clearSandboxActive: () => set({ sandboxActive: null }),

  conversationCache: {},
  setConversationCache: (project, messages) =>
    set((state) => {
      const cache = { ...state.conversationCache, [project]: messages };
      // Evict oldest entries if cache exceeds 5 projects
      const keys = Object.keys(cache);
      if (keys.length > 5) {
        delete cache[keys[0]];
      }
      return { conversationCache: cache };
    }),

  specLayers: {},
  applySpecLayer: (project, layer) =>
    set((state) => {
      const cur = state.specLayers[project] ?? [];
      const idx = cur.findIndex((l) => l.path === layer.path);
      // Rank prevents a late "writing" from downgrading a "done" row.
      const rank: Record<string, number> = { pending: 0, start: 1, writing: 2, done: 3 };
      let next: SpecLayer[];
      if (idx === -1) {
        next = [...cur, layer];
      } else {
        next = cur.slice();
        const existing = next[idx];
        const status =
          (rank[layer.status] ?? 0) >= (rank[existing.status] ?? 0)
            ? layer.status
            : existing.status;
        next[idx] = { ...existing, status, snippet: layer.snippet ?? existing.snippet };
      }
      const map = { ...state.specLayers, [project]: next };
      // Evict oldest projects so the live-layer map stays bounded across a session.
      const keys = Object.keys(map);
      if (keys.length > MAX_SPEC_PROJECTS) {
        for (const k of keys.slice(0, keys.length - MAX_SPEC_PROJECTS)) {
          if (k !== project) delete map[k];
        }
      }
      return { specLayers: map };
    }),
  clearSpecLayers: (project) =>
    set((state) => ({ specLayers: { ...state.specLayers, [project]: [] } })),

  connected: false,
  setConnected: (connected) => set({ connected }),
}));
