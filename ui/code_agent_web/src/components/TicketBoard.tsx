'use client';

import React, { useState } from 'react';
import { ProjectSummary, TicketSummary } from '@/lib/models';
import { classifyIssueType } from '@/lib/jira-hierarchy';
import {
  Search, RefreshCw, Plus, ExternalLink, Inbox,
  Loader2, AlertCircle, GitBranch, Zap, ShieldCheck,
  CheckCircle2, XCircle, Sparkles, Bug, HelpCircle, Lightbulb,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog, DialogContent, DialogDescription,
  DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';

// ── Column config ─────────────────────────────────────────────────────────────

interface ColDef {
  id: string;
  label: string;
  Icon: React.ComponentType<{ size?: number; className?: string; 'aria-hidden'?: boolean }>;
  spin?: boolean;
  laneClass?: string;
  labelClass?: string;
  /** extra statuses that also land in this column (e.g. rejected → failed) */
  extraIds?: string[];
}

const COLUMNS: ColDef[] = [
  {
    id: 'pending',
    label: 'Backlog',
    Icon: Inbox,
    labelClass: 'text-neutral-500',
  },
  {
    id: 'planning',
    label: 'Planning',
    Icon: Loader2,
    spin: true,
    labelClass: 'text-[#ec3013]',
  },
  {
    id: 'awaiting_approval',
    label: 'Awaiting approval',
    Icon: AlertCircle,
    labelClass: 'text-amber-600',
    laneClass: 'bg-amber-50/50 border-amber-200/60',
  },
  {
    id: 'developing',
    label: 'Developing',
    Icon: GitBranch,
    labelClass: 'text-violet-600',
  },
  {
    id: 'verifying',
    label: 'Verifying',
    Icon: Zap,
    labelClass: 'text-sky-600',
  },
  {
    id: 'qa',
    label: 'QA',
    Icon: ShieldCheck,
    labelClass: 'text-emerald-600',
  },
  {
    id: 'completed',
    label: 'Done',
    Icon: CheckCircle2,
    labelClass: 'text-emerald-600',
    laneClass: 'bg-emerald-50/40 border-emerald-200/50',
  },
  {
    id: 'failed',
    label: 'Failed',
    Icon: XCircle,
    labelClass: 'text-rose-500',
    laneClass: 'bg-rose-50/40 border-rose-200/40',
    extraIds: ['rejected'],
  },
];

const ACTIVE_STATUSES = new Set(['planning', 'awaiting_approval', 'developing', 'verifying', 'qa']);

const RUN_DOT_COLOR: Record<string, string> = {
  planning: 'bg-[#ec3013]',
  awaiting_approval: 'bg-amber-500',
  developing: 'bg-violet-500',
  verifying: 'bg-sky-500',
  qa: 'bg-emerald-500',
  completed: 'bg-emerald-400',
  failed: 'bg-rose-500',
  rejected: 'bg-neutral-400',
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function getPriorityLevel(ticket: TicketSummary): 'high' | 'medium' | 'low' | null {
  const p = ticket.jira_priority;
  if (!p) return null;
  const lp = p.toLowerCase();
  if (/high|highest|critical|blocker|urgent/.test(lp)) return 'high';
  if (/low|lowest|trivial|minor/.test(lp)) return 'low';
  return 'medium';
}

// ── Sub-components ────────────────────────────────────────────────────────────

function PriorityBars({ level }: { level: 'high' | 'medium' | 'low' | null }) {
  if (!level) return null;
  const filled = level === 'high' ? 3 : level === 'medium' ? 2 : 1;
  const color = level === 'high' ? '#ec3013' : level === 'medium' ? '#f59e0b' : '#a3a3a3';
  return (
    <div className="flex items-end gap-0.5 h-3 flex-shrink-0" title={`${level} priority`}>
      {([4, 7, 10] as number[]).map((h, i) => (
        <div
          key={i}
          className="w-[3px] rounded-sm flex-shrink-0"
          style={{ height: h, background: i < filled ? color : '#e5e5e5' }}
        />
      ))}
    </div>
  );
}

function TypeBadge({ type, issueType }: { type: string; issueType: string | null }) {
  if (issueType === 'epic') {
    return (
      <span className="inline-flex items-center gap-0.5 text-[9px] font-semibold px-1.5 py-0.5 rounded-md bg-violet-50 text-violet-700 border border-violet-200/70 leading-none">
        ⚡ Epic
      </span>
    );
  }
  if (type === 'bug') {
    return (
      <span className="inline-flex items-center gap-0.5 text-[9px] font-semibold px-1.5 py-0.5 rounded-md bg-rose-50 text-rose-600 border border-rose-200/60 leading-none">
        <Bug size={8} aria-hidden />Bug
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-0.5 text-[9px] font-semibold px-1.5 py-0.5 rounded-md bg-[#fff2ef] text-[#ae1800] border border-[#ffc4b8] leading-none">
      <Sparkles size={8} aria-hidden />Feature
    </span>
  );
}

function RunDot({ status }: { status: string }) {
  const color = RUN_DOT_COLOR[status] || 'bg-neutral-300';
  const pulse = ACTIVE_STATUSES.has(status);
  return (
    <span
      className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${color} ${pulse ? 'animate-pulse' : ''}`}
    />
  );
}

// ── Ticket card ───────────────────────────────────────────────────────────────

interface CardProps {
  ticket: TicketSummary;
  isSelected: boolean;
  onClick: () => void;
}

function TicketCard({ ticket, isSelected, onClick }: CardProps) {
  const idLabel = ticket.jira_key || `T-${ticket.id}`;
  const priorityLevel = getPriorityLevel(ticket);
  const issueType = classifyIssueType(ticket);

  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={e => e.key === 'Enter' && onClick()}
      aria-selected={isSelected}
      className={`bg-white border rounded-xl px-3 py-2.5 cursor-pointer transition-all ${
        isSelected
          ? 'border-[#ffc4b8] ring-1 ring-[#ec3013]/15 shadow-md'
          : 'border-neutral-200/70 shadow-sm hover:border-neutral-300 hover:shadow-md hover:-translate-y-px'
      }`}
    >
      {/* Key + priority bars */}
      <div className="flex items-center justify-between mb-1.5">
        <span
          className={`text-[10px] font-mono font-bold leading-none ${
            ticket.source === 'jira' ? 'text-[#ec3013]' : 'text-neutral-400'
          }`}
        >
          {idLabel}
        </span>
        <PriorityBars level={priorityLevel} />
      </div>

      {/* Title */}
      <p className="text-[11px] text-neutral-700 font-medium leading-snug line-clamp-2 mb-2">
        {ticket.title}
      </p>

      {/* Type badge */}
      <TypeBadge type={ticket.ticket_type} issueType={issueType} />

      {/* Run row */}
      {ticket.run_id && (
        <div className="flex items-center gap-1.5 mt-2 pt-1.5 border-t border-neutral-100">
          <RunDot status={ticket.status} />
          <span className="text-[9px] font-mono text-neutral-400 truncate min-w-0">
            {ticket.run_id}
          </span>
        </div>
      )}
    </div>
  );
}

// ── Board column ──────────────────────────────────────────────────────────────

interface ColumnProps {
  col: ColDef;
  cards: TicketSummary[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}

function BoardColumn({ col, cards, selectedId, onSelect }: ColumnProps) {
  const { Icon } = col;
  return (
    <div className="w-[188px] flex-shrink-0 flex flex-col h-full">
      {/* Column header */}
      <div className="flex items-center justify-between mb-2 px-0.5 flex-shrink-0">
        <div className={`flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider ${col.labelClass || 'text-neutral-500'}`}>
          <Icon size={11} className={col.spin ? 'animate-spin' : ''} aria-hidden />
          <span className="truncate">{col.label}</span>
        </div>
        <span className="text-[10px] font-bold text-neutral-500 bg-neutral-100 rounded-full px-1.5 py-px tabular-nums flex-shrink-0">
          {cards.length}
        </span>
      </div>

      {/* Lane */}
      <div
        className={`flex-1 rounded-xl border p-2 overflow-y-auto space-y-2 min-h-0 ${
          col.laneClass || 'bg-neutral-50/70 border-neutral-200/60'
        }`}
      >
        {cards.length === 0 ? (
          <div className="flex items-center justify-center h-10">
            <span className="text-[11px] text-neutral-300">Empty</span>
          </div>
        ) : (
          cards.map(t => (
            <TicketCard
              key={t.id}
              ticket={t}
              isSelected={t.id === selectedId}
              onClick={() => onSelect(t.id)}
            />
          ))
        )}
      </div>
    </div>
  );
}

// ── Pipeline rail ─────────────────────────────────────────────────────────────
// Live count strip across all 8 stages — active stages get a red accent.

function PipelineRail({ columns, cardsByColumn }: { columns: ColDef[]; cardsByColumn: (col: ColDef) => TicketSummary[] }) {
  return (
    <div className="flex-none flex gap-3 px-4 pt-4 pb-1">
      {columns.map((col, i) => {
        const count = cardsByColumn(col).length;
        const isActive = ACTIVE_STATUSES.has(col.id) && count > 0;
        return (
          <div
            key={col.id}
            className={`flex-1 flex flex-col gap-0.5 rounded-xl border px-3 py-2.5 transition-colors ${
              isActive
                ? 'bg-[#fff2ef] border-[#ffc4b8]'
                : 'bg-white border-neutral-200/70 shadow-sm'
            }`}
          >
            <span className="text-[9px] font-mono font-bold text-neutral-400">
              {String(i + 1).padStart(2, '0')}
            </span>
            <span className={`text-2xl font-extrabold leading-none ${isActive ? 'text-[#ec3013]' : 'text-neutral-800'}`}>
              {count}
            </span>
            <span className="text-[9px] font-bold uppercase tracking-wide text-neutral-500 whitespace-nowrap truncate">
              {col.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

interface TicketBoardProps {
  selectedProject: ProjectSummary | null;
  tickets: TicketSummary[];
  selectedTicketId: number | null;
  isLoadingTickets: boolean;
  isSyncingJira: boolean;
  onSelectTicket: (id: number) => void;
  onRefreshTickets: () => void;
  onCreateTicket: (title: string, ticketType: string, description?: string) => Promise<void>;
  onSyncJiraTickets: () => void;
  onDeleteTicket: (id: number) => Promise<void>;
  onRunEpic: (id: number) => void;
}

export default function TicketBoard({
  selectedProject,
  tickets,
  selectedTicketId,
  isLoadingTickets,
  isSyncingJira,
  onSelectTicket,
  onRefreshTickets,
  onCreateTicket,
  onSyncJiraTickets,
}: TicketBoardProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [priorityFilter, setPriorityFilter] = useState('');

  // Dialog state
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [ticketTitle, setTicketTitle] = useState('');
  const [ticketType, setTicketType] = useState('feature');
  const [ticketDescription, setTicketDescription] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState('');

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateError('');
    const title = ticketTitle.trim();
    if (!title) { setCreateError('Title is required'); return; }
    setIsCreating(true);
    try {
      await onCreateTicket(title, ticketType, ticketDescription.trim() || undefined);
      setTicketTitle('');
      setTicketDescription('');
      setTicketType('feature');
      setIsDialogOpen(false);
    } catch (err: any) {
      setCreateError(err.message || 'Failed to create ticket');
    } finally {
      setIsCreating(false);
    }
  };

  const activeCount = tickets.filter(t => ACTIVE_STATUSES.has(t.status)).length;

  const visibleTickets = tickets.filter(t => {
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      if (
        !t.title.toLowerCase().includes(q) &&
        !`t-${t.id}`.includes(q) &&
        !(t.jira_key?.toLowerCase().includes(q))
      ) return false;
    }
    if (typeFilter && t.ticket_type !== typeFilter) return false;
    if (priorityFilter && getPriorityLevel(t) !== priorityFilter) return false;
    return true;
  });

  const getColumnCards = (col: ColDef) => {
    const ids = new Set([col.id, ...(col.extraIds || [])]);
    return visibleTickets.filter(t => ids.has(t.status));
  };

  return (
    <div className="flex flex-col h-full bg-neutral-50/40">
      {/* ── Top bar ── */}
      <div className="flex items-center justify-between px-4 py-3 bg-white border-b border-neutral-200/70 gap-3 flex-shrink-0">
        {/* Left: project + live count */}
        <div className="flex items-center gap-3 min-w-0">
          {selectedProject ? (
            <div className="flex items-center gap-1.5 min-w-0">
              <GitBranch size={14} className="text-neutral-400 flex-shrink-0" aria-hidden />
              <span className="text-sm font-bold text-neutral-800 truncate">{selectedProject.name}</span>
            </div>
          ) : (
            <span className="text-sm font-semibold text-neutral-400">No project selected</span>
          )}
          {activeCount > 0 && (
            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200/70 px-2.5 py-1 rounded-full flex-shrink-0">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse inline-block" />
              {activeCount} running
            </div>
          )}
        </div>

        {/* Right: controls */}
        <div className="flex items-center gap-2 flex-shrink-0">
          {/* Search */}
          <div className="relative">
            <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-neutral-400 pointer-events-none" aria-hidden />
            <Input
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="Search…"
              disabled={!selectedProject}
              className="pl-7 h-9 w-40 text-xs bg-white border-neutral-200 text-neutral-700 placeholder:text-neutral-400 rounded-lg shadow-sm focus-visible:ring-[#ec3013]/30 disabled:opacity-40"
            />
          </div>

          {/* Type filter */}
          <select
            value={typeFilter}
            onChange={e => setTypeFilter(e.target.value)}
            disabled={!selectedProject}
            className="h-9 px-2.5 text-xs border border-neutral-200 rounded-lg bg-white text-neutral-600 shadow-sm disabled:opacity-40 focus:outline-none focus:ring-1 focus:ring-[#ec3013]/40"
            aria-label="Filter by type"
          >
            <option value="">All types</option>
            <option value="feature">Feature</option>
            <option value="bug">Bug</option>
          </select>

          {/* Priority filter */}
          <select
            value={priorityFilter}
            onChange={e => setPriorityFilter(e.target.value)}
            disabled={!selectedProject}
            className="h-9 px-2.5 text-xs border border-neutral-200 rounded-lg bg-white text-neutral-600 shadow-sm disabled:opacity-40 focus:outline-none focus:ring-1 focus:ring-[#ec3013]/40"
            aria-label="Filter by priority"
          >
            <option value="">All priorities</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>

          {/* Sync */}
          <Button
            onClick={onSyncJiraTickets}
            disabled={!selectedProject || isSyncingJira}
            variant="outline"
            size="sm"
            className="h-9 border-neutral-200 text-neutral-600 hover:text-neutral-800 text-xs px-3 gap-1.5 rounded-lg shadow-sm"
          >
            {isSyncingJira
              ? <RefreshCw size={11} className="animate-spin" />
              : <ExternalLink size={11} className="text-[#ec3013]" aria-hidden />}
            Sync
          </Button>

          {/* Refresh */}
          <button
            onClick={onRefreshTickets}
            disabled={!selectedProject || isLoadingTickets}
            className="h-9 w-9 flex items-center justify-center border border-neutral-200 rounded-lg text-neutral-400 hover:text-neutral-600 hover:bg-neutral-50 disabled:opacity-30 disabled:pointer-events-none transition-colors shadow-sm"
            title="Refresh tickets"
            aria-label="Refresh tickets"
          >
            <RefreshCw size={12} className={isLoadingTickets ? 'animate-spin' : ''} aria-hidden />
          </button>

          {/* New ticket */}
          <Button
            onClick={() => setIsDialogOpen(true)}
            disabled={!selectedProject}
            size="sm"
            className="h-9 bg-[#ec3013] hover:bg-[#dd2b0f] text-white font-semibold text-xs px-3.5 gap-1 rounded-lg shadow-sm"
          >
            <Plus size={12} aria-hidden />New ticket
          </Button>
        </div>
      </div>

      {/* ── Board ── */}
      {!selectedProject ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <HelpCircle size={28} className="text-neutral-200 mx-auto mb-2" aria-hidden />
            <p className="text-sm text-neutral-400">Select a project to view the board</p>
          </div>
        </div>
      ) : (
        <>
          <PipelineRail columns={COLUMNS} cardsByColumn={getColumnCards} />
          <div
            className="flex-1 overflow-x-auto overflow-y-hidden px-4 py-4 min-h-0"
            role="region"
            aria-label="Ticket board"
          >
            <div className="flex gap-3 h-full min-w-max">
              {COLUMNS.map(col => (
                <BoardColumn
                  key={col.id}
                  col={col}
                  cards={getColumnCards(col)}
                  selectedId={selectedTicketId}
                  onSelect={onSelectTicket}
                />
              ))}
            </div>
          </div>
        </>
      )}

      {/* ── Create ticket dialog ── */}
      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogContent className="sm:max-w-[500px] bg-white border-neutral-200 text-neutral-900 rounded-xl shadow-lg">
          <form onSubmit={handleCreateSubmit}>
            <DialogHeader>
              <DialogTitle className="text-neutral-900 text-base">New ticket</DialogTitle>
              <DialogDescription className="text-neutral-500 text-xs">
                Create a local task for the AI agent to implement in the project codebase.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4 py-4">
              {/* Type toggle */}
              <div className="space-y-1.5">
                <label className="text-[10px] font-bold text-neutral-500 tracking-wide uppercase">
                  Ticket type
                </label>
                <div className="flex gap-3">
                  {[
                    { value: 'feature', Icon: Sparkles, label: 'Feature / Task', activeClass: 'bg-[#fff2ef] border-[#ec3013] text-[#ec3013] ring-1 ring-[#ec3013]/20' },
                    { value: 'bug', Icon: Bug, label: 'Bug / Fix', activeClass: 'bg-rose-50 border-rose-400 text-rose-600 ring-1 ring-rose-400/20' },
                  ].map(opt => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setTicketType(opt.value)}
                      className={`flex-1 flex items-center justify-center gap-2 py-2.5 border rounded-lg text-xs font-semibold transition-all ${
                        ticketType === opt.value
                          ? opt.activeClass
                          : 'bg-neutral-50 border-neutral-200 text-neutral-500 hover:text-neutral-800'
                      }`}
                    >
                      <opt.Icon size={14} aria-hidden />{opt.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Title */}
              <div className="flex flex-col gap-1.5">
                <label className="text-[10px] font-bold text-neutral-500 tracking-wide uppercase">
                  Title
                </label>
                <Input
                  placeholder="e.g. Integrate auth token validation middleware"
                  value={ticketTitle}
                  onChange={e => setTicketTitle(e.target.value)}
                  className="bg-white border-neutral-200 text-neutral-900 placeholder:text-neutral-400 rounded-lg focus-visible:ring-[#ec3013] text-xs"
                />
              </div>

              {/* Description */}
              <div className="flex flex-col gap-1.5">
                <label className="text-[10px] font-bold text-neutral-500 tracking-wide uppercase">
                  Description & context <span className="normal-case font-normal text-neutral-400">(optional)</span>
                </label>
                <Textarea
                  placeholder="Provide implementation details, acceptance criteria, or target files..."
                  value={ticketDescription}
                  onChange={e => setTicketDescription(e.target.value)}
                  className="bg-white border-neutral-200 text-neutral-900 placeholder:text-neutral-400 rounded-lg focus-visible:ring-[#ec3013] text-xs min-h-[80px]"
                />
              </div>

              {/* Tip */}
              <div className="bg-neutral-50 border border-neutral-200/80 p-3 rounded-lg flex gap-2.5">
                <Lightbulb size={15} className="text-amber-500 flex-shrink-0 mt-0.5" aria-hidden />
                <p className="text-[10px] text-neutral-500 leading-normal">
                  Include explicit acceptance criteria and filenames. This helps the planning agent build a precise execution graph.
                </p>
              </div>

              {createError && (
                <div className="text-xs text-rose-600 font-medium bg-rose-50 border border-rose-200 p-2 rounded-lg">
                  {createError}
                </div>
              )}
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setIsDialogOpen(false)}
                className="border-neutral-200 hover:bg-neutral-100 hover:text-neutral-900 text-xs rounded-lg"
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={isCreating}
                className="bg-[#ec3013] hover:bg-[#dd2b0f] text-white font-semibold text-xs rounded-lg shadow-sm"
              >
                {isCreating ? 'Creating…' : 'Create ticket'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
