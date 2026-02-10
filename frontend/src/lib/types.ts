export type ProjectPhase =
  | "idle"
  | "planning"
  | "engineering"
  | "verifying"
  | "completed"
  | "failed";

export interface ProjectSummary {
  name: string;
  status: ProjectPhase;
  iteration: number;
  last_score: number | null;
  last_update: string | null;
  is_running: boolean;
}

export interface AgentProviderInfo {
  provider: string;
  model: string;
  is_override: boolean;
}

export interface ProjectProviders {
  overrides: Record<string, string>;
  effective: Record<string, AgentProviderInfo & { type: string }>;
}

export interface ProjectStatus {
  name: string;
  status: ProjectPhase;
  current_phase: ProjectPhase;
  iteration: number;
  max_iterations: number;
  quality_threshold: number;
  last_score: number | null;
  last_update: string;
  start_time: string;
  is_running: boolean;
  error?: string;
  providers?: Record<string, AgentProviderInfo>;
}

export interface FileTreeNode {
  name: string;
  path: string;
  type: "file" | "directory";
  children?: FileTreeNode[];
  size?: number;
}

export interface ProviderInfo {
  name: string;
  type: string;
  model: string;
  base_url: string | null;
  is_active: boolean;
  cost_input: number;
  cost_output: number;
  supports_async: boolean;
  concurrency_limit: number;
}

export interface UsageData {
  total_tokens: number;
  total_cost: number;
  by_agent: Record<
    string,
    {
      tokens: number;
      cost: number;
      calls: number;
    }
  >;
  history: Array<{
    timestamp: string;
    agent: string;
    input_tokens: number;
    output_tokens: number;
    cost: number;
  }>;
}

export interface SSEEvent {
  type: string;
  timestamp: string;
  data: Record<string, unknown>;
}

export interface AppConfig {
  active_provider: string;
  agent_providers: Record<string, string>;
  tumbler: {
    max_iterations: number;
    quality_threshold: number;
    project_timeout: number;
    debounce_time: number;
    max_cost_per_project: number;
    prompt_compression: {
      enabled: boolean;
      rate: number;
      preserve_code_blocks: boolean;
    };
  };
}

export interface GlobalStats {
  project_count: number;
  total_cost: number;
  total_tokens: number;
}

export interface CostTimeseriesPoint {
  hour: string;
  cost: number;
  tokens: number;
}

export interface CostByProvider {
  provider: string;
  cost: number;
  tokens: number;
  calls: number;
}

export interface CostPerIteration {
  iteration: number;
  agent: string;
  cost: number;
  tokens: number;
}

export interface ConversationMessage {
  timestamp: string;
  agent: "architect" | "engineer" | "verifier" | "system";
  role: "input" | "output" | "error" | "status" | "sandbox";
  iteration: number;
  content: string;
  metadata?: {
    label?: string;
    score?: number;
    file_count?: number;
    sandbox_phase?: "install" | "build" | "test" | "lint";
    sandbox_status?: "success" | "failed" | "timeout" | "skipped";
    exit_code?: number;
    duration_s?: number;
    commands?: string[];
  };
}

export interface SandboxPhaseEvent {
  project: string;
  phase: "install" | "build" | "test" | "lint";
  status: "success" | "failed" | "timeout" | "skipped";
  iteration: number;
  stdout: string;
  stderr: string;
  exit_code: number;
  duration_s: number;
  commands: string[];
}

export interface AsyncCapabilities {
  supports_async: boolean;
  concurrency_limit: number;
  parallel_generation: boolean;
}

export interface VerificationConfig {
  sandbox_enabled: boolean;
  timeout_install: number;
  timeout_build: number;
  timeout_test: number;
  timeout_lint: number;
  memory_limit: string;
  cpu_limit: number;
  tmpfs_size: string;
  network_install: boolean;
  network_verify: boolean;
}
