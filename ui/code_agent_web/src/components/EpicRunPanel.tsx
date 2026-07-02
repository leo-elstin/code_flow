'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { TicketSummary, EpicRun } from '@/lib/models';
import { CodeAgentApiClient } from '@/lib/api-client';
import { getChildren } from '@/lib/jira-hierarchy';
import TicketTypeBadge from '@/components/TicketTypeBadge';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { toast } from 'sonner';
import {
  Play, GitBranch, Loader2, Check, X, CheckCircle2, AlertTriangle, Layers, ArrowRight, ExternalLink, Zap,
} from 'lucide-react';

interface EpicRunPanelProps {
  ticket: TicketSummary;
  tickets: TicketSummary[];
  projectId: number | null;
  jiraBaseUrl?: string;
  onSelectChild: (ticketId: number) => void;
}

const ACTIVE = new Set(['planning', 'developing']);
const TERMINAL = new Set(['completed', 'failed', 'rejected']);

interface DisplayChild {
  ticket_id: number;
  jira_key?: string;
  title: string;
  status: string;
  priority?: string;
  run_id?: string;
}

function statusBadge(status: string) {
  let classes = 'bg-slate-100 text-slate-500 border-slate-200';
  let label = 'Todo';
  switch (status) {
    case 'completed':
      classes = 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20'; label = 'Done'; break;
    case 'failed':
      classes = 'bg-rose-500/10 text-rose-600 border-rose-500/20'; label = 'Failed'; break;
    case 'rejected':
      classes = 'bg-rose-500/10 text-rose-600 border-rose-500/20'; label = 'Rejected'; break;
    case 'awaiting_approval':
      classes = 'bg-indigo-500/15 text-indigo-600 border-indigo-500/20'; label = 'Needs Review'; break;
    case 'developing':
    case 'planning':
      classes = 'bg-amber-500/10 text-amber-600 border-amber-500/20'; label = 'In progress'; break;
    case 'verifying':
    case 'qa':
      classes = 'bg-sky-500/10 text-sky-600 border-sky-500/20'; label = 'Verifying'; break;
    case 'pending':
      classes = 'bg-slate-100 text-slate-500 border-slate-200'; label = 'Todo'; break;
  }
  return (
    <Badge variant="outline" className={`text-[10px] px-2 py-0.5 font-semibold tracking-wide rounded-md ${classes}`}>
      {label}
    </Badge>
  );
}

export default function EpicRunPanel({ ticket, tickets, projectId, jiraBaseUrl, onSelectChild }: EpicRunPanelProps) {
  const [epicRun, setEpicRun] = useState<EpicRun | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [autoMode, setAutoMode] = useState(false);
  const pollRef = useRef<NodeJS.Timeout | null>(null);

  const childTickets = getChildren(tickets, ticket);

  const loadLatest = useCallback(async () => {
    if (projectId == null) return;
    try {
      const runs = await CodeAgentApiClient.listEpicRuns(projectId);
      setEpicRun(runs.find((r) => r.epic_ticket_id === ticket.id) || null);
    } catch {
      setEpicRun(null);
    }
  }, [projectId, ticket.id]);

  useEffect(() => {
    setEpicRun(null);
    loadLatest();
  }, [loadLatest]);

  // Poll while the epic run is actively planning or executing.
  useEffect(() => {
    const stop = () => {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    };
    if (epicRun && ACTIVE.has(epicRun.status)) {
      if (pollRef.current) return;
      pollRef.current = setInterval(async () => {
        try {
          setEpicRun(await CodeAgentApiClient.getEpicRun(epicRun.epic_run_id));
        } catch (e) {
          console.error('Epic poll error', e);
        }
      }, 2500);
    } else {
      stop();
    }
    return stop;
  }, [epicRun?.epic_run_id, epicRun?.status]);

  const handleStart = async () => {
    setIsBusy(true);
    try {
      const { epic_run_id } = await CodeAgentApiClient.startEpicRun(ticket.id, autoMode || undefined);
      setEpicRun(await CodeAgentApiClient.getEpicRun(epic_run_id));
      toast.success(autoMode ? 'Epic started in full-auto mode' : 'Epic planning started');
    } catch (err: any) {
      toast.error(err.message || 'Failed to start epic run');
    } finally {
      setIsBusy(false);
    }
  };

  const handleApprove = async () => {
    if (!epicRun) return;
    setIsBusy(true);
    try {
      setEpicRun(await CodeAgentApiClient.approveEpicRun(epicRun.epic_run_id));
      toast.success('Epic approved — running stories');
    } catch (err: any) {
      toast.error(err.message || 'Approve failed');
    } finally {
      setIsBusy(false);
    }
  };

  const handleReject = async () => {
    if (!epicRun) return;
    setIsBusy(true);
    try {
      setEpicRun(await CodeAgentApiClient.rejectEpicRun(epicRun.epic_run_id));
    } catch (err: any) {
      toast.error(err.message || 'Reject failed');
    } finally {
      setIsBusy(false);
    }
  };

  const handleResume = async () => {
    if (!epicRun) return;
    setIsBusy(true);
    try {
      setEpicRun(await CodeAgentApiClient.resumeEpicRun(epicRun.epic_run_id));
      toast.success('Epic resumed — continuing remaining stories');
    } catch (err: any) {
      toast.error(err.message || 'Resume failed');
    } finally {
      setIsBusy(false);
    }
  };

  // Unify the story list: prefer the epic-run's children (live run statuses),
  // otherwise fall back to the synced child tickets so they show before a run.
  const priorityFor = (tid: number) => tickets.find((t) => t.id === tid)?.jira_priority;
  const displayChildren: DisplayChild[] = epicRun?.children?.length
    ? epicRun.children.map((c) => ({
        ticket_id: c.ticket_id,
        jira_key: c.jira_key || tickets.find((t) => t.id === c.ticket_id)?.jira_key,
        title: c.title || tickets.find((t) => t.id === c.ticket_id)?.title || `Ticket ${c.ticket_id}`,
        status: c.status,
        priority: priorityFor(c.ticket_id),
        run_id: c.run_id,
      }))
    : childTickets.map((t) => ({
        ticket_id: t.id,
        jira_key: t.jira_key,
        title: t.title,
        status: t.status,
        priority: t.jira_priority,
        run_id: t.run_id,
      }));

  const total = displayChildren.length;
  const done = displayChildren.filter((c) => c.status === 'completed').length;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  const status = epicRun?.status;
  const isPlanning = status === 'planning';
  const awaiting = status === 'awaiting_approval';
  const running = status === 'developing';
  const doneAll = status === 'completed';
  const failed = status === 'failed' || status === 'rejected';
  // Resume only applies to a genuinely failed run that has a dependency plan to
  // continue from (a planning-phase failure has no levels to resume).
  const canResume = status === 'failed' && (epicRun?.plan?.levels?.length ?? 0) > 0;
  const canExecute = !epicRun || (!!status && TERMINAL.has(status));
  const jiraHref = jiraBaseUrl && ticket.jira_key ? `${jiraBaseUrl}/browse/${ticket.jira_key}` : null;

  return (
    <div className="h-full flex flex-col bg-white">
      {/* Header */}
      <div className="px-6 py-4 border-b border-slate-200">
        <div className="flex items-center gap-2">
          {ticket.jira_key && (
            <span className="text-xs font-semibold font-mono text-slate-500">{ticket.jira_key}</span>
          )}
          <TicketTypeBadge ticket={ticket} />
          {status && <span className="ml-1">{statusBadge(status)}</span>}
          {epicRun?.auto_approve && (
            <Badge variant="outline" className="text-[10px] px-2 py-0.5 font-semibold tracking-wide rounded-md bg-violet-500/10 text-violet-600 border-violet-500/20">
              <Zap size={10} className="mr-1" /> Auto
            </Badge>
          )}
          <div className="flex-1" />
          {jiraHref && (
            <a
              href={jiraHref}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-700"
            >
              Open in Jira <ExternalLink size={12} />
            </a>
          )}
        </div>
        <h2 className="text-lg font-semibold text-slate-800 mt-1.5">{ticket.title}</h2>

        <div className="flex items-center gap-2 mt-2">
          {ticket.jira_status && (
            <span className="text-xs px-2 py-0.5 rounded-md bg-amber-50 text-amber-700 border border-amber-200">
              Jira status: {ticket.jira_status}
            </span>
          )}
          <span className="text-xs px-2 py-0.5 rounded-md bg-slate-100 text-slate-600 border border-slate-200">
            {total} {total === 1 ? 'story' : 'stories'}
          </span>
        </div>

        {total > 0 && (
          <div className="flex items-center gap-3 mt-3">
            <div className="flex-1 h-2 rounded-full bg-slate-200 overflow-hidden">
              <div className="h-full bg-emerald-500 transition-all" style={{ width: `${pct}%` }} />
            </div>
            <span className="text-xs text-slate-500 flex-shrink-0">{done} of {total} done</span>
          </div>
        )}
      </div>

      <ScrollArea className="flex-1 min-h-0">
        <div className="px-6 py-5 space-y-5">
          {isPlanning && (
            <div className="flex items-center gap-2 text-sm text-amber-600">
              <Loader2 size={15} className="animate-spin" />
              Planning execution order…
            </div>
          )}

          {/* Dependency plan */}
          {epicRun?.plan?.levels && epicRun.plan.levels.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-2">
                <Layers size={14} className="text-slate-400" />
                <h3 className="text-xs font-bold uppercase tracking-wide text-slate-500">Execution plan</h3>
              </div>
              {epicRun.plan.reasoning && (
                <p className="text-xs text-slate-500 mb-3 leading-relaxed">{epicRun.plan.reasoning}</p>
              )}
              <div className="space-y-2">
                {epicRun.plan.levels.map((level, i) => (
                  <div key={i} className="flex items-start gap-2">
                    <span className="mt-1 text-[10px] font-bold text-slate-400 w-12 flex-shrink-0">L{i + 1}</span>
                    <div className="flex flex-wrap gap-1.5">
                      {level.map((tid) => {
                        const dc = displayChildren.find((c) => c.ticket_id === tid);
                        return (
                          <span key={tid} className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-slate-200 bg-white text-xs text-slate-700">
                            {dc?.jira_key && <span className="font-mono text-[10px] text-slate-400">{dc.jira_key}</span>}
                            <span className="truncate max-w-[180px]">{dc?.title || `Ticket ${tid}`}</span>
                          </span>
                        );
                      })}
                      {level.length > 1 && (
                        <span className="inline-flex items-center text-[10px] text-emerald-600 font-medium px-1">parallel</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Status banners */}
          {running && (
            <div className="flex items-center gap-2 text-sm text-amber-600">
              <Loader2 size={15} className="animate-spin" /> Executing stories…
            </div>
          )}
          {doneAll && (
            <div className="flex items-center gap-2 text-sm text-emerald-600">
              <CheckCircle2 size={15} /> All stories completed.
            </div>
          )}
          {failed && epicRun?.error && (
            <div className="flex items-start gap-2 text-sm text-rose-600 bg-rose-500/5 border border-rose-500/20 rounded-lg p-3">
              <AlertTriangle size={15} className="mt-0.5 flex-shrink-0" />
              <span>{epicRun.error}</span>
            </div>
          )}

          {epicRun?.integration_branch && (
            <div className="flex items-center gap-1.5 text-xs text-slate-400">
              <GitBranch size={12} />
              <span className="font-mono">{epicRun.integration_branch}</span>
            </div>
          )}

          {/* Stories in this epic */}
          <div>
            <h3 className="text-xs font-bold uppercase tracking-wide text-slate-500 mb-2">Stories in this epic</h3>
            {total === 0 ? (
              <p className="text-sm text-slate-500">
                No child stories found. Sync from Jira so this epic&apos;s stories are imported.
              </p>
            ) : (
              <div className="space-y-1.5">
                {displayChildren.map((c) => (
                  <div
                    key={c.ticket_id}
                    onClick={() => c.run_id && onSelectChild(c.ticket_id)}
                    className={`flex items-center gap-2 px-3 py-2 rounded-lg border border-slate-200/70 bg-white ${
                      c.run_id ? 'cursor-pointer hover:bg-slate-50' : ''
                    }`}
                  >
                    {c.jira_key && <span className="text-[10px] font-mono text-slate-400 w-16 flex-shrink-0">{c.jira_key}</span>}
                    <span className="flex-1 min-w-0 truncate text-xs text-slate-700">{c.title}</span>
                    {statusBadge(c.status)}
                    {c.run_id && <ArrowRight size={13} className="text-slate-300 flex-shrink-0" />}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </ScrollArea>

      {/* Footer actions */}
      <div className="px-6 py-4 border-t border-slate-200">
        {awaiting ? (
          <div className="flex items-center gap-2">
            <button
              onClick={handleApprove}
              disabled={isBusy}
              className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-3 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold disabled:opacity-60 transition-colors"
            >
              {isBusy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
              Approve &amp; run
            </button>
            <button
              onClick={handleReject}
              disabled={isBusy}
              className="inline-flex items-center gap-2 px-4 py-3 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 text-sm font-semibold disabled:opacity-60 transition-colors"
            >
              <X size={15} /> Reject
            </button>
          </div>
        ) : canResume ? (
          <div className="space-y-2">
            <button
              onClick={handleResume}
              disabled={isBusy}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-3 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold disabled:opacity-60 transition-colors"
            >
              {isBusy ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
              Resume epic
            </button>
            <p className="text-xs text-slate-400">
              Continues from where it stopped — keeps the {done} completed{' '}
              {done === 1 ? 'story' : 'stories'}, re-runs the rest.
            </p>
            <button
              onClick={handleStart}
              disabled={isBusy}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 text-sm font-semibold disabled:opacity-60 transition-colors"
            >
              Restart from scratch
            </button>
          </div>
        ) : canExecute && total > 0 ? (
          <>
            <label className="flex items-center gap-2 mb-3 text-xs text-slate-600 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={autoMode}
                onChange={(e) => setAutoMode(e.target.checked)}
                className="rounded border-slate-300"
              />
              <Zap size={12} className="text-violet-500 flex-shrink-0" />
              Full auto — skip plan approval, auto-retry failed stories
            </label>
            <button
              onClick={handleStart}
              disabled={isBusy}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-3 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold disabled:opacity-60 transition-colors"
            >
              {isBusy ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
              Execute epic
            </button>
            <p className="text-xs text-slate-400 mt-2">
              {doneAll
                ? 'Re-runs every story under this epic.'
                : failed
                ? 'Re-runs the epic from the start.'
                : `Runs the ${Math.max(total - done, 0)} unstarted ${total - done === 1 ? 'story' : 'stories'}. Or select a single story to run it on its own.`}
            </p>
          </>
        ) : running || isPlanning ? (
          <div className="flex items-center justify-center gap-2 text-sm text-slate-400">
            <Loader2 size={15} className="animate-spin" /> Epic in progress…
          </div>
        ) : null}
      </div>
    </div>
  );
}
