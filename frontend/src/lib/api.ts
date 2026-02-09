import type {
  ProjectSummary,
  ProjectStatus,
  ProjectProviders,
  FileTreeNode,
  ProviderInfo,
  UsageData,
  AppConfig,
  GlobalStats,
  CostTimeseriesPoint,
  CostByProvider,
  CostPerIteration,
  ConversationMessage,
} from "./types";

/** Build the backend base URL, bypassing the Next.js rewrite proxy. */
function backendBase(): string {
  if (typeof window === "undefined") return "";  // SSR fallback
  return `${window.location.protocol}//${window.location.hostname}:8000`;
}

async function fetchJSON<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${backendBase()}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API Error ${res.status}: ${text}`);
  }
  return res.json();
}

export const api = {
  health: () => fetchJSON<{ status: string; version: string }>("/api/health"),

  listProjects: () => fetchJSON<ProjectSummary[]>("/api/projects"),

  createProject: (
    name: string,
    requirements: string,
    options?: {
      max_iterations?: number;
      quality_threshold?: number;
      provider_overrides?: Record<string, string>;
      compression?: {
        enabled?: boolean;
        rate?: number;
        preserve_code_blocks?: boolean;
      };
    }
  ) =>
    fetchJSON<{ name: string; status: string }>("/api/projects", {
      method: "POST",
      body: JSON.stringify({ name, requirements, ...options }),
    }),

  getProjectStatus: (name: string) =>
    fetchJSON<ProjectStatus>(`/api/projects/${name}/status`),

  getArtifacts: (name: string) =>
    fetchJSON<FileTreeNode>(`/api/projects/${name}/artifacts`),

  getArtifactContent: (name: string, filePath: string) =>
    fetchJSON<{ path: string; content: string; size: number }>(
      `/api/projects/${name}/artifacts/${encodeURIComponent(filePath)}`
    ),

  startProject: (name: string, providerOverrides?: Record<string, string>) =>
    fetchJSON<{ status: string; project: string }>(
      `/api/projects/${name}/start`,
      {
        method: "POST",
        body: providerOverrides
          ? JSON.stringify({ provider_overrides: providerOverrides })
          : undefined,
      }
    ),

  stopProject: (name: string) =>
    fetchJSON<{ status: string; project: string }>(
      `/api/projects/${name}/stop`,
      { method: "POST" }
    ),

  resetProject: (name: string) =>
    fetchJSON<{ status: string; project: string }>(
      `/api/projects/${name}/reset`,
      { method: "POST" }
    ),

  deleteProject: (name: string) =>
    fetchJSON<{ status: string; project: string }>(
      `/api/projects/${name}`,
      { method: "DELETE" }
    ),

  getUsage: (name: string) =>
    fetchJSON<UsageData>(`/api/projects/${name}/usage`),

  getConversation: (name: string) =>
    fetchJSON<ConversationMessage[]>(`/api/projects/${name}/conversation`),

  getProjectProviders: (name: string) =>
    fetchJSON<ProjectProviders>(`/api/projects/${name}/providers`),

  updateProjectProviders: (name: string, overrides: Record<string, string>) =>
    fetchJSON<{ status: string; overrides: Record<string, string> }>(
      `/api/projects/${name}/providers`,
      {
        method: "PUT",
        body: JSON.stringify({ provider_overrides: overrides }),
      }
    ),

  listProviders: () => fetchJSON<ProviderInfo[]>("/api/providers"),

  listModels: (provider: string) =>
    fetchJSON<{ provider: string; models: string[] }>(
      `/api/providers/${provider}/models`
    ),

  getConfig: () => fetchJSON<AppConfig>("/api/config"),

  updateConfig: (config: Partial<AppConfig>) =>
    fetchJSON<{ status: string }>("/api/config", {
      method: "PUT",
      body: JSON.stringify(config),
    }),

  getGlobalStats: () =>
    fetchJSON<GlobalStats>("/api/analytics/stats"),

  getCostTimeseries: (project?: string) =>
    fetchJSON<CostTimeseriesPoint[]>(
      `/api/analytics/cost-timeseries${project ? `?project=${project}` : ""}`
    ),

  getCostByProvider: () =>
    fetchJSON<CostByProvider[]>("/api/analytics/cost-by-provider"),

  getCostPerIteration: (project: string) =>
    fetchJSON<CostPerIteration[]>(
      `/api/analytics/cost-per-iteration?project=${project}`
    ),
};
