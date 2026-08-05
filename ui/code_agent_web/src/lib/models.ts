export type DevEngine = 'api' | 'claude_code_cli';

export interface ProjectSummary {
  id: number;
  name: string;
  path: string;
  /** Which engine runs the dev node. "api" (default) or "claude_code_cli" (pilot). */
  dev_engine: DevEngine;
  created_at: string;
  updated_at: string;
}

export interface ClarificationOption {
  id: string;
  label: string;
  description: string;
}

export interface ClarificationQuestion {
  id: string;
  question: string;
  context: string;
  options: ClarificationOption[];
}

export interface ClarifyAnswer {
  question_id: string;
  question: string;
  option_id: string;
  option_label: string;
  option_description: string;
}

export interface TicketSummary {
  id: number;
  project_id: number;
  title: string;
  description?: string;
  ticket_type: 'feature' | 'bug' | string;
  status: 'pending' | 'planning' | 'awaiting_clarification' | 'awaiting_approval' | 'developing' | 'verifying' | 'completed' | 'failed' | 'rejected' | string;
  run_id?: string;
  created_at: string;
  updated_at: string;
  source: 'local' | 'jira' | string;
  jira_key?: string;
  jira_issue_type?: string;
  jira_parent_key?: string;
  jira_status?: string;
  jira_priority?: string;
}

export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cached_tokens: number;
}

export interface CodeAgentExecution {
  run_id: string;
  attempt: number;
  status: string;
  error?: string;
  created_at?: string;
  updated_at?: string;
  parent_run_id?: string;
  root_run_id?: string;
  token_usage: TokenUsage;
}

export interface AgentActivityEvent {
  seq: number;
  type: 'status' | 'thinking' | 'tool' | 'llm' | 'grep' | 'token' | string;
  phase: 'system' | 'planner' | 'developer' | 'verifier' | 'qa' | string;
  title: string;
  detail?: string;
  files: string[];
  meta: Record<string, any>;
  created_at?: string;
}

export interface ActivitySnapshot {
  events: AgentActivityEvent[];
  current_action?: AgentActivityEvent;
  token_usage: TokenUsage;
}

export interface CodeAgentRunStatus {
  run_id: string;
  root_run_id?: string;
  parent_run_id?: string;
  attempt?: number;
  status: string;
  user_request?: string;
  project_path?: string;
  ticket_id?: number;
  worktree_path?: string;
  workspace_mode: 'worktree' | 'in_place';
  iteration?: number;
  plan?: Record<string, any>;
  acceptance_criteria: string[];
  clarification_questions?: ClarificationQuestion[];
  file_changes: Array<{ path: string; action: string; [key: string]: any }>;
  diffs: Array<{ path: string; diff: string; [key: string]: any }>;
  verifier_report?: Record<string, any>;
  qa_report?: Record<string, any>;
  messages: Array<Record<string, any>>;
  error?: string;
  merge_report?: {
    applied: boolean;
    target_branch?: string;
    agent_branch?: string;
    conflict_files?: string[];
    error?: string;
    merged_at?: string;
  };
  merge_preview?: {
    target_branch?: string;
    agent_branch?: string;
    conflict_files?: string[];
  };
  is_running: boolean;
  current_action?: AgentActivityEvent;
  token_usage?: TokenUsage;
  context_bundle?: Record<string, any>;
}

export interface JiraConfig {
  project_id: number;
  jira_jql: string;
  jira_status_mapping: Record<string, string>;
}

export interface JiraSyncResult {
  created: number;
  updated: number;
  total: number;
  errors: string[];
}

export interface JiraStatusCheck {
  configured: boolean;
  base_url?: string;
  user_email?: string;
}

export type LlmProvider = 'anthropic' | 'openai_gateway' | 'openai';

export interface LlmConfig {
  provider: LlmProvider;
  /** Masked preview only (e.g. "sk-…a1b2"); the raw key never leaves the API. */
  api_key_preview?: string | null;
  key_configured: boolean;
  base_url?: string | null;
  chat_model?: string | null;
  dev_model?: string | null;
  /** OpenAI-family only. Unset keeps calls on Chat Completions. */
  reasoning_effort?: string | null;
  reasoning_mode?: string | null;
  /** True when the config came from the backend .env rather than a saved row. */
  from_env?: boolean;
  project_id?: number | null;
  has_override?: boolean | null;
}

export interface UpdateLlmConfig {
  provider: LlmProvider | null;
  /** Omit to keep the stored key; send "" to clear it. */
  api_key?: string | null;
  base_url?: string | null;
  chat_model?: string | null;
  dev_model?: string | null;
  reasoning_effort?: string | null;
  reasoning_mode?: string | null;
}

export interface LlmTestResult {
  ok: boolean;
  provider: string;
  model?: string | null;
  error?: string | null;
}

export interface EpicChildRun {
  ticket_id: number;
  jira_key?: string;
  title?: string;
  run_id?: string;
  status: string;
}

export interface EpicPlan {
  levels: number[][];
  edges?: Record<string, number[]>;
  reasoning?: string;
  had_cycle?: boolean;
  nodes?: Array<{ ticket_id: number; jira_key?: string; title?: string }>;
}

export interface EpicRun {
  epic_run_id: string;
  epic_ticket_id: number;
  epic_jira_key?: string;
  status: string;
  workspace_mode: string;
  integration_branch?: string;
  auto_approve?: boolean;
  plan?: EpicPlan;
  children: EpicChildRun[];
  error?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ProjectSkillSummary {
  id: string;
  name: string;
  path: string;
  description: string;
}

export interface AgentsMdStatus {
  project_id: number;
  exists: boolean;
  filename: string;
  path: string;
  content_preview: string;
}

export interface ProjectContextConfig {
  project_id: number;
  context_text: string;
  planner_skill_ids: string[];
  dev_skill_ids: string[];
}

export interface LogEntry {
  title: string;
  detail: string;
  isError: boolean;
  isUser: boolean;
}

export interface SimulatorDevice {
  udid: string;
  name: string;
  state: string;
  runtime: string;
}

export interface SimulatorUiElement {
  index: number;
  type: string;
  label?: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface SimulatorUiTree {
  root_width: number;
  root_height: number;
  elements: SimulatorUiElement[];
}

export interface WdaStatus {
  state: 'not_started' | 'building' | 'ready' | 'failed';
  error?: string;
}
