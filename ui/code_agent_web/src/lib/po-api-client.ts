import { CodeAgentApiClient } from './api-client';

export type POSessionStatus =
  | 'classifying'
  | 'brainstorming'
  | 'waiting_for_reply'
  | 'researching'
  | 'drafting'
  | 'awaiting_review'
  | 'approved'
  | 'rejected'
  | 'failed';

export interface DraftStory {
  id: string;
  epic: string;
  title: string;
  story: string;
  description: string;
  acceptance_criteria: string[];
  effort: string;
  priority: string;
  depends_on: string[];
  grounding: string[];
}

export interface POSession {
  session_id: string;
  mode: 'brainstorm' | 'intake';
  status: POSessionStatus;
  initial_context: string;
  questions_asked: number;
  readiness_score: number;
  pending_questions: string[];
  requirements_model?: Record<string, any>;
  brief?: Record<string, any>;
  draft_stories?: DraftStory[];
  error?: string;
  created_at?: string;
  updated_at?: string;
}

const _req = async <T>(path: string, options?: RequestInit): Promise<T> => {
  const baseUrl = CodeAgentApiClient.getBaseUrl();
  const url = `${baseUrl}${path}`;
  const headers = { 'Content-Type': 'application/json', ...(options?.headers || {}) };
  const response = await fetch(url, { ...options, headers });
  if (response.status === 204) return {} as T;
  if (!response.ok) {
    let detail = '';
    try {
      const body = await response.json();
      detail = body.detail || body.message || JSON.stringify(body);
    } catch {
      detail = await response.text();
    }
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
};

export async function startPOSession(data: {
  initial_context: string;
  project_id?: number;
  project_path?: string;
  research_depth?: string;
}): Promise<{ session_id: string; status: string }> {
  return _req('/api/po/sessions', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function getPOSession(sessionId: string): Promise<POSession> {
  return _req(`/api/po/sessions/${sessionId}`);
}

export async function listPOSessions(
  projectId?: number
): Promise<{ sessions: POSession[]; total: number }> {
  const params = projectId != null ? `?project_id=${projectId}` : '';
  return _req(`/api/po/sessions${params}`);
}

export async function replyToPOSession(sessionId: string, content: string): Promise<POSession> {
  return _req(`/api/po/sessions/${sessionId}/reply`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  });
}

export async function finalizePOSession(sessionId: string): Promise<POSession> {
  return _req(`/api/po/sessions/${sessionId}/finalize`, { method: 'POST' });
}

export async function approvePOSession(
  sessionId: string,
  storyIds: string[]
): Promise<POSession> {
  return _req(`/api/po/sessions/${sessionId}/approve`, {
    method: 'POST',
    body: JSON.stringify({ story_ids: storyIds }),
  });
}

export async function rejectPOSession(sessionId: string): Promise<POSession> {
  return _req(`/api/po/sessions/${sessionId}/reject`, { method: 'POST' });
}

/**
 * Subscribe to PO session updates by polling every 2 seconds.
 * Returns an unsubscribe function.
 */
export function subscribeToPOSession(
  sessionId: string,
  onMessage: (data: POSession) => void
): () => void {
  const TERMINAL_STATUSES: POSessionStatus[] = ['awaiting_review', 'approved', 'rejected', 'failed'];
  let active = true;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const poll = async () => {
    if (!active) return;
    try {
      const session = await getPOSession(sessionId);
      onMessage(session);
      if (!TERMINAL_STATUSES.includes(session.status)) {
        timer = setTimeout(poll, 2000);
      }
    } catch (err) {
      console.error('PO session poll error:', err);
      if (active) {
        timer = setTimeout(poll, 3000);
      }
    }
  };

  // Start polling after a brief delay
  timer = setTimeout(poll, 500);

  return () => {
    active = false;
    if (timer) clearTimeout(timer);
  };
}
