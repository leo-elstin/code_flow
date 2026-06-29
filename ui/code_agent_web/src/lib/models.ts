export interface ProjectSummary {
  id: number;
  name: string;
  path: string;
  created_at: string;
  updated_at: string;
}

export interface TicketSummary {
  id: number;
  project_id: number;
  title: string;
  description?: string;
  ticket_type: 'feature' | 'bug' | string;
  status: 'pending' | 'planning' | 'awaiting_approval' | 'developing' | 'verifying' | 'completed' | 'failed' | 'rejected' | string;
  run_id?: string;
  created_at: string;
  updated_at: string;
  source: 'local' | 'jira' | string;
  jira_key?: string;
  jira_issue_type?: string;
  jira_parent_key?: string;
}

export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
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
