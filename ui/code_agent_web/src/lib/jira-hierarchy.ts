import { TicketSummary } from '@/lib/models';

export type JiraTypeKind = 'epic' | 'story' | 'task' | 'subtask' | 'bug' | null;

/**
 * Classify a ticket's Jira issue type into a coarse hierarchy kind.
 * Returns null for tickets without a recognizable Jira issue type (e.g. local tickets).
 */
export function classifyIssueType(ticket: TicketSummary): JiraTypeKind {
  const raw = (ticket.jira_issue_type || '').trim().toLowerCase();
  if (!raw) return null;
  if (raw === 'epic') return 'epic';
  if (raw === 'story') return 'story';
  if (raw === 'sub-task' || raw === 'subtask') return 'subtask';
  if (raw === 'task') return 'task';
  if (raw === 'bug') return 'bug';
  return null;
}

/** Direct children of a ticket: tickets whose jira_parent_key matches this ticket's jira_key. */
export function getChildren(tickets: TicketSummary[], parent: TicketSummary): TicketSummary[] {
  if (!parent.jira_key) return [];
  return tickets.filter((t) => t.jira_parent_key === parent.jira_key);
}

/** Whether a ticket has any child tickets present in the list. */
export function hasChildren(tickets: TicketSummary[], parent: TicketSummary): boolean {
  return getChildren(tickets, parent).length > 0;
}

/**
 * Top-level tickets for the queue: a ticket is nested (hidden) only when its
 * jira_parent_key resolves to another ticket present in the same list.
 * Epics, un-parented Stories/Tasks, and all local tickets stay at the top level.
 */
export function getTopLevelTickets(tickets: TicketSummary[]): TicketSummary[] {
  const presentKeys = new Set<string>();
  for (const t of tickets) {
    if (t.jira_key) presentKeys.add(t.jira_key);
  }
  return tickets.filter(
    (t) => !(t.jira_parent_key && presentKeys.has(t.jira_parent_key)),
  );
}
