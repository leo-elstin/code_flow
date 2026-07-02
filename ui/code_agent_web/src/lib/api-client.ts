import {
  ProjectSummary,
  TicketSummary,
  CodeAgentRunStatus,
  CodeAgentExecution,
  ActivitySnapshot,
  ProjectContextConfig,
  AgentsMdStatus,
  ProjectSkillSummary,
  JiraConfig,
  JiraSyncResult,
  JiraStatusCheck,
  EpicRun,
  ClarifyAnswer,
} from './models';

const BASE_URL_KEY = 'code_agent_api_base_url';

export class CodeAgentApiClient {
  private static getStoredBaseUrl(): string {
    if (typeof window !== 'undefined') {
      return localStorage.getItem(BASE_URL_KEY) || 'http://127.0.0.1:8000';
    }
    return 'http://127.0.0.1:8000';
  }

  static getBaseUrl(): string {
    return this.getStoredBaseUrl();
  }

  static saveBaseUrl(url: string): void {
    if (typeof window !== 'undefined') {
      const sanitized = url.endsWith('/') ? url.slice(0, -1) : url;
      localStorage.setItem(BASE_URL_KEY, sanitized);
    }
  }

  private static async _request<T>(path: string, options?: RequestInit): Promise<T> {
    const baseUrl = this.getBaseUrl();
    const url = `${baseUrl}${path}`;
    const headers = {
      'Content-Type': 'application/json',
      ...(options?.headers || {}),
    };

    try {
      const response = await fetch(url, {
        ...options,
        headers,
      });

      if (response.status === 204) {
        return {} as T;
      }

      if (!response.ok) {
        let errorDetail = '';
        try {
          const errBody = await response.json();
          errorDetail = errBody.detail || errBody.message || JSON.stringify(errBody);
        } catch (_) {
          errorDetail = await response.text();
        }
        throw new Error(errorDetail || `Request failed with status ${response.status}`);
      }

      return (await response.json()) as T;
    } catch (error: any) {
      console.error(`API Error on ${path}:`, error);
      throw error;
    }
  }

  static async pickProjectFolder(initialDir?: string): Promise<string | null> {
    const baseUrl = this.getBaseUrl();
    const url = `${baseUrl}/api/code-agent/pick-folder`;
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ initial_dir: initialDir || undefined }),
    });

    if (response.status === 204) {
      return null;
    }

    if (!response.ok) {
      let errorDetail = '';
      try {
        const errBody = await response.json();
        errorDetail = errBody.detail || errBody.message || JSON.stringify(errBody);
      } catch (_) {
        errorDetail = await response.text();
      }
      throw new Error(errorDetail || `Request failed with status ${response.status}`);
    }

    const data = (await response.json()) as { path: string };
    return data.path;
  }

  static async listProjects(): Promise<ProjectSummary[]> {
    const data = await this._request<{ projects: ProjectSummary[] }>('/api/code-agent/projects');
    return data.projects || [];
  }

  static async createProject(path: string, name?: string): Promise<ProjectSummary> {
    return this._request<ProjectSummary>('/api/code-agent/projects', {
      method: 'POST',
      body: JSON.stringify({ path, name }),
    });
  }

  static async deleteProject(projectId: number): Promise<void> {
    await this._request<void>(`/api/code-agent/projects/${projectId}`, {
      method: 'DELETE',
    });
  }

  static async listTickets(projectId: number): Promise<TicketSummary[]> {
    const data = await this._request<{ tickets: TicketSummary[] }>(`/api/code-agent/projects/${projectId}/tickets`);
    return data.tickets || [];
  }

  static async createTicket(
    projectId: number,
    title: string,
    ticketType: string,
    description?: string
  ): Promise<TicketSummary> {
    return this._request<TicketSummary>(`/api/code-agent/projects/${projectId}/tickets`, {
      method: 'POST',
      body: JSON.stringify({
        title,
        ticket_type: ticketType,
        description,
      }),
    });
  }

  static async deleteTicket(ticketId: number): Promise<void> {
    await this._request<void>(`/api/code-agent/tickets/${ticketId}`, {
      method: 'DELETE',
    });
  }

  static async startRun(request: string, projectPath: string, ticketId?: number): Promise<string> {
    const data = await this._request<{ run_id: string }>('/api/code-agent/run', {
      method: 'POST',
      body: JSON.stringify({
        request,
        project_path: projectPath,
        ticket_id: ticketId,
      }),
    });
    return data.run_id;
  }

  static async getRun(runId: string): Promise<CodeAgentRunStatus> {
    return this._request<CodeAgentRunStatus>(`/api/code-agent/runs/${runId}`);
  }

  static async listRuns(projectPath?: string, limit = 50): Promise<any[]> {
    const params = new URLSearchParams({ limit: String(limit) });
    if (projectPath) {
      params.append('project_path', projectPath);
    }
    const data = await this._request<{ runs: any[] }>(`/api/code-agent/runs?${params.toString()}`);
    return data.runs || [];
  }

  static async clarifyRun(runId: string, answers: ClarifyAnswer[]): Promise<CodeAgentRunStatus> {
    return this._request<CodeAgentRunStatus>(`/api/code-agent/runs/${runId}/clarify`, {
      method: 'POST',
      body: JSON.stringify({ answers }),
    });
  }

  static async approveRun(runId: string, workspaceMode: 'worktree' | 'in_place'): Promise<CodeAgentRunStatus> {
    return this._request<CodeAgentRunStatus>(`/api/code-agent/runs/${runId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ workspace_mode: workspaceMode }),
    });
  }

  static async rejectRun(runId: string, feedback?: string): Promise<CodeAgentRunStatus> {
    return this._request<CodeAgentRunStatus>(`/api/code-agent/runs/${runId}/reject`, {
      method: 'POST',
      body: JSON.stringify({ feedback }),
    });
  }

  static async retryRun(runId: string): Promise<CodeAgentRunStatus> {
    return this._request<CodeAgentRunStatus>(`/api/code-agent/runs/${runId}/retry`, {
      method: 'POST',
    });
  }

  static async revertRun(runId: string): Promise<CodeAgentRunStatus> {
    return this._request<CodeAgentRunStatus>(`/api/code-agent/runs/${runId}/revert`, {
      method: 'POST',
    });
  }

  static async resumeRun(runId: string): Promise<CodeAgentRunStatus> {
    return this._request<CodeAgentRunStatus>(`/api/code-agent/runs/${runId}/resume`, {
      method: 'POST',
    });
  }

  static async mergeRun(runId: string): Promise<any> {
    return this._request<any>(`/api/code-agent/runs/${runId}/merge`, {
      method: 'POST',
    });
  }

  static async listExecutions(runId: string): Promise<CodeAgentExecution[]> {
    const data = await this._request<{ executions: CodeAgentExecution[] }>(`/api/code-agent/runs/${runId}/executions`);
    return data.executions || [];
  }

  static async fetchActivity(runId: string, afterSeq = 0): Promise<ActivitySnapshot> {
    return this._request<ActivitySnapshot>(`/api/code-agent/runs/${runId}/activity?after_seq=${afterSeq}`);
  }

  static async getProjectContext(projectId: number): Promise<ProjectContextConfig> {
    return this._request<ProjectContextConfig>(`/api/code-agent/projects/${projectId}/context`);
  }

  static async updateProjectContext(
    projectId: number,
    contextText: string,
    plannerSkillIds: string[],
    devSkillIds: string[]
  ): Promise<ProjectContextConfig> {
    return this._request<ProjectContextConfig>(`/api/code-agent/projects/${projectId}/context`, {
      method: 'PUT',
      body: JSON.stringify({
        context_text: contextText,
        planner_skill_ids: plannerSkillIds,
        dev_skill_ids: devSkillIds,
      }),
    });
  }

  static async generateProjectContext(projectId: number, hints?: string): Promise<string> {
    const data = await this._request<{ context_text: string }>(`/api/code-agent/projects/${projectId}/context/generate`, {
      method: 'POST',
      body: JSON.stringify({ hints }),
    });
    return data.context_text || '';
  }

  static async getAgentsMdStatus(projectId: number): Promise<AgentsMdStatus> {
    return this._request<AgentsMdStatus>(`/api/code-agent/projects/${projectId}/agents-md`);
  }

  static async generateAgentsMd(projectId: number, hints?: string): Promise<AgentsMdStatus> {
    return this._request<AgentsMdStatus>(`/api/code-agent/projects/${projectId}/agents-md/generate`, {
      method: 'POST',
      body: JSON.stringify({ hints }),
    });
  }

  static async listProjectSkills(projectId: number): Promise<ProjectSkillSummary[]> {
    const data = await this._request<{ skills: ProjectSkillSummary[] }>(`/api/code-agent/projects/${projectId}/skills`);
    return data.skills || [];
  }

  static async getJiraStatus(): Promise<JiraStatusCheck> {
    return this._request<JiraStatusCheck>('/api/code-agent/jira/status');
  }

  static async getJiraConfig(projectId: number): Promise<JiraConfig> {
    return this._request<JiraConfig>(`/api/code-agent/projects/${projectId}/jira/config`);
  }

  static async updateJiraConfig(
    projectId: number,
    jql?: string,
    statusMapping?: Record<string, string>
  ): Promise<JiraConfig> {
    return this._request<JiraConfig>(`/api/code-agent/projects/${projectId}/jira/config`, {
      method: 'PUT',
      body: JSON.stringify({
        jira_jql: jql,
        jira_status_mapping: statusMapping,
      }),
    });
  }

  static async syncJiraTickets(projectId: number): Promise<JiraSyncResult> {
    return this._request<JiraSyncResult>(`/api/code-agent/projects/${projectId}/jira/sync`, {
      method: 'POST',
    });
  }

  static async getJiraTransitions(projectId: number, issueKey: string): Promise<any> {
    return this._request<any>(
      `/api/code-agent/projects/${projectId}/jira/transitions?issue_key=${encodeURIComponent(issueKey)}`
    );
  }

  // -- Epic-level execution --------------------------------------------------

  static async startEpicRun(
    ticketId: number,
    autoApprove?: boolean
  ): Promise<{ epic_run_id: string; status: string }> {
    return this._request<{ epic_run_id: string; status: string }>(
      `/api/code-agent/epics/${ticketId}/run`,
      {
        method: 'POST',
        ...(autoApprove === undefined
          ? {}
          : {
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ auto_approve: autoApprove }),
            }),
      }
    );
  }

  static async getEpicRun(epicRunId: string): Promise<EpicRun> {
    return this._request<EpicRun>(`/api/code-agent/epics/${epicRunId}`);
  }

  static async approveEpicRun(
    epicRunId: string,
    workspaceMode: 'worktree' | 'in_place' = 'worktree'
  ): Promise<EpicRun> {
    return this._request<EpicRun>(`/api/code-agent/epics/${epicRunId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ workspace_mode: workspaceMode }),
    });
  }

  static async rejectEpicRun(epicRunId: string): Promise<EpicRun> {
    return this._request<EpicRun>(`/api/code-agent/epics/${epicRunId}/reject`, {
      method: 'POST',
    });
  }

  /** Continue a failed epic from where it stopped — re-runs only the stories
   * that did not complete, preserving the integration branch. */
  static async resumeEpicRun(epicRunId: string): Promise<EpicRun> {
    return this._request<EpicRun>(`/api/code-agent/epics/${epicRunId}/resume`, {
      method: 'POST',
    });
  }

  static async listEpicRuns(projectId?: number, limit = 50): Promise<EpicRun[]> {
    const params = new URLSearchParams({ limit: String(limit) });
    if (projectId != null) params.append('project_id', String(projectId));
    const data = await this._request<{ epic_runs: EpicRun[] }>(
      `/api/code-agent/epics?${params.toString()}`
    );
    return data.epic_runs || [];
  }
}
