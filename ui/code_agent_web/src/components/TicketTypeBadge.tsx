'use client';

import React from 'react';
import { TicketSummary } from '@/lib/models';
import { classifyIssueType, JiraTypeKind } from '@/lib/jira-hierarchy';

const TYPE_STYLES: Record<Exclude<JiraTypeKind, null>, { label: string; classes: string }> = {
  epic: { label: 'Epic', classes: 'bg-purple-100 text-purple-700' },
  story: { label: 'Story', classes: 'bg-emerald-100 text-emerald-700' },
  task: { label: 'Task', classes: 'bg-sky-100 text-sky-700' },
  subtask: { label: 'Sub-task', classes: 'bg-cyan-100 text-cyan-700' },
  bug: { label: 'Bug', classes: 'bg-rose-100 text-rose-700' },
};

/**
 * Small colored badge showing a Jira ticket's issue type (Epic / Story / Task...).
 * Renders nothing for tickets without a recognizable Jira issue type.
 */
export default function TicketTypeBadge({ ticket }: { ticket: TicketSummary }) {
  const kind = classifyIssueType(ticket);
  if (!kind) return null;
  const { label, classes } = TYPE_STYLES[kind];
  return (
    <span
      className={`px-1 py-0 h-4 inline-flex items-center rounded text-[9px] font-bold tracking-wide uppercase flex-shrink-0 ${classes}`}
    >
      {label}
    </span>
  );
}
