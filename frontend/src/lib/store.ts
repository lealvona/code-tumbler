import { create } from "zustand";
import type {
  ProjectSummary,
  SSEEvent,
  SandboxPhaseEvent,
  ConversationMessage,
} from "./types";

interface StreamingChunk {
  project: string;
  agent: string;
  content: string;
}

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

  // Track which agent is currently thinking
  thinkingAgent: { project: string; agent: string } | null;
  setThinkingAgent: (project: string, agent: string) => void;
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
      if (prev && prev.project === project && prev.agent === agent) {
        return { streamingChunk: { project, agent, content: prev.content + chunk } };
      }
      return { streamingChunk: { project, agent, content: chunk } };
    }),
  clearStreamingChunk: () => set({ streamingChunk: null }),

  thinkingAgent: null,
  setThinkingAgent: (project, agent) =>
    set({ thinkingAgent: { project, agent }, streamingChunk: null }),
  clearThinkingAgent: () => set({ thinkingAgent: null }),

  sandboxPhases: [],
  addSandboxPhase: (phase) =>
    set((state) => ({
      sandboxPhases: [...state.sandboxPhases, phase],
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

  connected: false,
  setConnected: (connected) => set({ connected }),
}));
