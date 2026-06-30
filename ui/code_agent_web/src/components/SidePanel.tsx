'use client';

import React, { useState } from 'react';
import { ProjectSummary, TicketSummary } from '@/lib/models';
import { getTopLevelTickets, getChildren, classifyIssueType } from '@/lib/jira-hierarchy';
import TicketTypeBadge from '@/components/TicketTypeBadge';
import {
  Search,
  RefreshCw,
  Plus,
  Sparkles,
  Bug,
  ExternalLink,
  Trash2,
  HelpCircle,
  Lightbulb,
  ChevronRight,
  ChevronDown,
  Play
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';

interface SidePanelProps {
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
  onOpenChildren: (id: number) => void;
  onRunEpic: (id: number) => void;
}

export default function SidePanel({
  selectedProject,
  tickets,
  selectedTicketId,
  isLoadingTickets,
  isSyncingJira,
  onSelectTicket,
  onRefreshTickets,
  onCreateTicket,
  onSyncJiraTickets,
  onDeleteTicket,
  onOpenChildren,
  onRunEpic,
}: SidePanelProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [collapsedEpics, setCollapsedEpics] = useState<Set<number>>(new Set());
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [ticketTitle, setTicketTitle] = useState('');
  const [ticketType, setTicketType] = useState('feature');
  const [ticketDescription, setTicketDescription] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState('');

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    const title = ticketTitle.trim();
    if (!title) {
      setError('Title is required');
      return;
    }

    setIsCreating(true);
    try {
      await onCreateTicket(title, ticketType, ticketDescription.trim() || undefined);
      setTicketTitle('');
      setTicketDescription('');
      setTicketType('feature');
      setIsDialogOpen(false);
    } catch (err: any) {
      setError(err.message || 'Failed to create ticket');
    } finally {
      setIsCreating(false);
    }
  };

  const handleDelete = async (e: React.MouseEvent, ticketId: number) => {
    e.stopPropagation(); // Prevent selecting the ticket
    if (confirm('Are you sure you want to delete this ticket? This cannot be undone.')) {
      try {
        await onDeleteTicket(ticketId);
      } catch (err: any) {
        alert(err.message || 'Failed to delete ticket');
      }
    }
  };

  // When searching, match across all tickets (incl. nested children) shown flat.
  // Otherwise, group Epics (with inline-expandable children) above a "No epic" section.
  const searchResults = searchQuery
    ? tickets.filter((ticket) => {
        const query = searchQuery.toLowerCase();
        return (
          ticket.title.toLowerCase().includes(query) ||
          ticket.id.toString().includes(query) ||
          (ticket.jira_key && ticket.jira_key.toLowerCase().includes(query))
        );
      })
    : [];
  const topLevel = getTopLevelTickets(tickets);
  const epicTickets = topLevel.filter((t) => classifyIssueType(t) === 'epic');
  const otherTickets = topLevel.filter((t) => classifyIssueType(t) !== 'epic');

  const getStatusBadge = (status: string) => {
    let classes = '';
    let label = status;

    switch (status) {
      case 'completed':
        classes = 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20';
        label = 'Done';
        break;
      case 'failed':
      case 'rejected':
        classes = 'bg-rose-500/10 text-rose-400 border-rose-500/20';
        label = status === 'failed' ? 'Failed' : 'Rejected';
        break;
      case 'awaiting_approval':
        classes = 'bg-indigo-500/15 text-indigo-300 border-indigo-500/20 animate-pulse';
        label = 'Needs Review';
        break;
      case 'developing':
      case 'planning':
        classes = 'bg-amber-500/10 text-amber-400 border-amber-500/20';
        label = 'In Progress';
        break;
      case 'verifying':
      case 'qa':
        classes = 'bg-sky-500/10 text-sky-400 border-sky-500/20';
        label = 'Verifying';
        break;
      case 'pending':
      default:
        classes = 'bg-slate-800/80 text-slate-400 border-slate-700/50';
        label = 'Todo';
        break;
    }

    return (
      <Badge variant="outline" className={`text-[10px] px-2 py-0.5 font-semibold tracking-wide rounded-md ${classes}`}>
        {label}
      </Badge>
    );
  };

  const toggleEpic = (id: number) =>
    setCollapsedEpics((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const idLabel = (t: TicketSummary) => t.jira_key || `T-${t.id}`;

  const priorityPill = (ticket: TicketSummary) => {
    const p = ticket.jira_priority;
    if (!p) return <span className="text-xs text-slate-300">—</span>;
    const lp = p.toLowerCase();
    const cls = /high|highest|critical|blocker|urgent/.test(lp)
      ? 'text-rose-600'
      : /low|lowest|trivial|minor/.test(lp)
      ? 'text-slate-400'
      : 'text-amber-600';
    return <span className={`text-xs font-medium ${cls}`}>{p}</span>;
  };

  const renderLeaf = (ticket: TicketSummary) => {
    const isSelected = ticket.id === selectedTicketId;
    const childCount = getChildren(tickets, ticket).length;
    return (
      <div
        key={ticket.id}
        onClick={() => onSelectTicket(ticket.id)}
        className={`group relative flex items-center px-3 py-2.5 rounded-lg border text-left cursor-pointer transition-all ${
          isSelected
            ? 'bg-blue-50/70 border-blue-500/60 shadow-sm ring-1 ring-blue-500/10'
            : 'bg-white hover:bg-slate-50 border-slate-200/60 hover:border-slate-300/80 shadow-xs'
        }`}
      >
        <div className={`w-16 text-xs font-semibold font-mono flex-shrink-0 ${ticket.jira_key ? 'text-blue-700' : 'text-slate-500'}`}>
          {idLabel(ticket)}
        </div>
        <div className="flex-1 min-w-0 px-2">
          <div className="flex items-center gap-1.5">
            <TicketTypeBadge ticket={ticket} />
            <h4 className={`text-xs truncate font-medium ${isSelected ? 'text-blue-900 font-semibold' : 'text-slate-700'}`}>
              {ticket.title}
            </h4>
          </div>
        </div>
        <div className="w-24 flex-shrink-0 text-left">{getStatusBadge(ticket.status)}</div>
        <div className="w-16 flex-shrink-0 text-left pr-1">{priorityPill(ticket)}</div>
        {childCount > 0 && classifyIssueType(ticket) !== 'epic' && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onOpenChildren(ticket.id);
            }}
            className="ml-1 flex items-center gap-0.5 flex-shrink-0 text-slate-400 hover:text-blue-600 hover:bg-blue-50 rounded-md px-1 py-1 transition-colors"
            title={`View ${childCount} child ticket${childCount === 1 ? '' : 's'}`}
          >
            <span className="text-[9px] font-bold tabular-nums">{childCount}</span>
            <ChevronRight size={14} />
          </button>
        )}
        {ticket.source !== 'jira' && (
          <button
            onClick={(e) => handleDelete(e, ticket.id)}
            className="absolute right-2 opacity-0 group-hover:opacity-100 transition-opacity p-1 text-slate-400 hover:text-rose-500 hover:bg-rose-50 rounded-md bg-white"
            title="Delete ticket"
          >
            <Trash2 size={12} />
          </button>
        )}
      </div>
    );
  };

  const renderEpic = (epic: TicketSummary) => {
    const isSelected = epic.id === selectedTicketId;
    const children = getChildren(tickets, epic);
    const done = children.filter((c) => c.status === 'completed').length;
    const expanded = !collapsedEpics.has(epic.id);
    return (
      <div key={epic.id} className="space-y-1">
        <div
          onClick={() => onSelectTicket(epic.id)}
          className={`group relative flex items-center px-3 py-2.5 rounded-lg border cursor-pointer transition-all ${
            isSelected
              ? 'bg-blue-50/70 border-blue-500/60 shadow-sm ring-1 ring-blue-500/10'
              : 'bg-white hover:bg-slate-50 border-slate-200/60 hover:border-slate-300/80 shadow-xs'
          }`}
        >
          {children.length > 0 ? (
            <button
              onClick={(e) => {
                e.stopPropagation();
                toggleEpic(epic.id);
              }}
              className="mr-1 flex-shrink-0 text-slate-400 hover:text-blue-600"
              title={expanded ? 'Collapse' : 'Expand'}
            >
              {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
            </button>
          ) : (
            <span className="mr-1 w-[15px] flex-shrink-0" />
          )}
          <div className={`w-16 text-xs font-semibold font-mono flex-shrink-0 ${epic.jira_key ? 'text-blue-700' : 'text-slate-500'}`}>
            {idLabel(epic)}
          </div>
          <div className="flex-1 min-w-0 px-2">
            <div className="flex items-center gap-1.5">
              <TicketTypeBadge ticket={epic} />
              <h4 className={`text-xs truncate font-semibold ${isSelected ? 'text-blue-900' : 'text-slate-800'}`}>
                {epic.title}
              </h4>
            </div>
          </div>
          {children.length > 0 && (
            <span className="text-[10px] font-bold tabular-nums text-slate-500 mr-2 flex-shrink-0">
              {done}/{children.length}
            </span>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation();
              onRunEpic(epic.id);
            }}
            className="flex items-center gap-1 flex-shrink-0 px-2.5 py-1 rounded-md bg-slate-900 hover:bg-slate-800 text-white text-[11px] font-semibold transition-colors"
            title="Execute epic"
          >
            <Play size={11} />
            <span>Run</span>
          </button>
        </div>
        {expanded && children.length > 0 && (
          <div className="ml-3 pl-2 border-l border-slate-200 space-y-1">
            {children.map(renderLeaf)}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full bg-slate-900/15 border-r border-slate-200/80">
      {/* Header bar */}
      <div className="p-4 flex items-center justify-between gap-2 border-b border-slate-200/60 bg-white/40 backdrop-blur-sm">
        <div className="min-w-0">
          <h2 className="text-sm font-bold text-slate-800 tracking-wide uppercase">Ticket Queue</h2>
          {selectedProject && (
            <p className="text-[10px] text-slate-500 truncate font-mono mt-0.5">{selectedProject.name}</p>
          )}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {isLoadingTickets ? (
            <RefreshCw size={14} className="text-slate-400 animate-spin" />
          ) : (
            <button
              onClick={onRefreshTickets}
              disabled={!selectedProject}
              className="text-slate-400 hover:text-slate-700 p-1.5 rounded-lg hover:bg-slate-100/80 transition-colors disabled:opacity-30 disabled:pointer-events-none"
              title="Refresh Queue"
            >
              <RefreshCw size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Filter and actions bar */}
      <div className="p-3 bg-white/20 border-b border-slate-200/60 flex items-center gap-2">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-3 top-2.5 text-slate-400" />
          <Input
            placeholder="Search tickets..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            disabled={!selectedProject}
            className="pl-9 h-8 bg-white/80 border-slate-200 text-xs text-slate-800 placeholder:text-slate-400 focus-visible:ring-blue-500/30"
          />
        </div>
        <Button
          onClick={onSyncJiraTickets}
          disabled={!selectedProject || isSyncingJira}
          variant="outline"
          size="sm"
          className="h-8 border-slate-200 text-slate-600 hover:text-slate-800 hover:bg-slate-50 flex items-center gap-1.5 text-xs font-semibold px-2.5"
          title="Sync from Jira"
        >
          {isSyncingJira ? (
            <RefreshCw size={12} className="animate-spin" />
          ) : (
            <ExternalLink size={12} className="text-blue-500" />
          )}
          <span>Sync</span>
        </Button>
        <Button
          onClick={() => setIsDialogOpen(true)}
          disabled={!selectedProject}
          size="sm"
          className="h-8 bg-blue-600 hover:bg-blue-500 text-white font-semibold text-xs px-2.5 flex items-center gap-1"
        >
          <Plus size={14} />
          <span>Add</span>
        </Button>
      </div>

      {/* Table Headers */}
      <div className="px-5 py-2 text-[10px] font-bold text-slate-400 tracking-wider flex border-b border-slate-200/50 select-none">
        <div className="w-16">ID</div>
        <div className="flex-1 px-2">TASK NAME</div>
        <div className="w-24 text-left">STATUS</div>
        <div className="w-16 text-left">PRIORITY</div>
      </div>

      {/* Ticket List */}
      <div className="flex-1 min-h-0 bg-slate-50/50">
        <ScrollArea className="h-full">
          {!selectedProject ? (
            <div className="flex flex-col items-center justify-center p-8 text-center h-48">
              <HelpCircle size={24} className="text-slate-300 mb-2" />
              <p className="text-xs text-slate-400 font-medium">Select a project in the sidebar to view tickets.</p>
            </div>
          ) : searchQuery ? (
            searchResults.length === 0 ? (
              <div className="p-8 text-center text-xs text-slate-400 font-medium">No tickets found in queue.</div>
            ) : (
              <div className="p-2 space-y-1">{searchResults.map(renderLeaf)}</div>
            )
          ) : topLevel.length === 0 ? (
            <div className="p-8 text-center text-xs text-slate-400 font-medium">No tickets found in queue.</div>
          ) : (
            <div className="p-2 space-y-1">
              {epicTickets.map(renderEpic)}
              {otherTickets.length > 0 && (
                <>
                  {epicTickets.length > 0 && (
                    <div className="px-2 pt-3 pb-1 text-[10px] font-bold text-slate-400 tracking-wider">NO EPIC</div>
                  )}
                  {otherTickets.map(renderLeaf)}
                </>
              )}
            </div>
          )}
        </ScrollArea>
      </div>

      {/* Create Ticket Dialog */}
      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogContent className="sm:max-w-[500px] bg-slate-900 border-slate-800 text-slate-100">
          <form onSubmit={handleCreateSubmit}>
            <DialogHeader>
              <DialogTitle className="text-white text-base">New Ticket</DialogTitle>
              <DialogDescription className="text-slate-400 text-xs">
                Create a local task for the AI agent to implement in the project codebase.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4 py-4">
              {/* Ticket Type Toggle */}
              <div className="space-y-1.5">
                <label className="text-[10px] font-bold text-slate-400 tracking-wide uppercase">Ticket Type</label>
                <div className="flex gap-3">
                  <button
                    type="button"
                    onClick={() => setTicketType('feature')}
                    className={`flex-1 flex items-center justify-center gap-2 py-2.5 border rounded-lg text-xs font-semibold transition-all ${
                      ticketType === 'feature'
                        ? 'bg-blue-600/10 border-blue-500 text-blue-400 ring-1 ring-blue-500/20'
                        : 'bg-slate-950/40 border-slate-800 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    <Sparkles size={14} />
                    <span>Feature / Task</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setTicketType('bug')}
                    className={`flex-1 flex items-center justify-center gap-2 py-2.5 border rounded-lg text-xs font-semibold transition-all ${
                      ticketType === 'bug'
                        ? 'bg-rose-600/10 border-rose-500 text-rose-400 ring-1 ring-rose-500/20'
                        : 'bg-slate-950/40 border-slate-800 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    <Bug size={14} />
                    <span>Bug / Fix</span>
                  </button>
                </div>
              </div>

              {/* Title */}
              <div className="flex flex-col gap-1.5">
                <label className="text-[10px] font-bold text-slate-400 tracking-wide uppercase">Ticket Title</label>
                <Input
                  id="ticket-title"
                  placeholder="e.g. Integrate auth token validation middleware"
                  value={ticketTitle}
                  onChange={(e) => setTicketTitle(e.target.value)}
                  className="bg-slate-950 border-slate-800 text-slate-100 placeholder:text-slate-600 focus-visible:ring-blue-500 text-xs"
                />
              </div>

              {/* Description */}
              <div className="flex flex-col gap-1.5">
                <label className="text-[10px] font-bold text-slate-400 tracking-wide uppercase">Description & Context (Optional)</label>
                <Textarea
                  id="ticket-desc"
                  placeholder="Provide implementation details, acceptance criteria, or target files..."
                  value={ticketDescription}
                  onChange={(e) => setTicketDescription(e.target.value)}
                  className="bg-slate-950 border-slate-800 text-slate-100 placeholder:text-slate-600 focus-visible:ring-blue-500 text-xs min-h-[90px]"
                />
              </div>

              {/* Tip Box */}
              <div className="bg-slate-950 border border-slate-800/80 p-3 rounded-lg flex gap-2.5">
                <Lightbulb size={16} className="text-yellow-500 flex-shrink-0 mt-0.5" />
                <p className="text-[10px] text-slate-400 leading-normal">
                  Include explicit acceptance criteria and filenames in the title or description. This aids the planning agent in building a precise execution graph.
                </p>
              </div>

              {error && <div className="text-xs text-red-400 font-medium bg-red-950/20 border border-red-900/40 p-2 rounded-lg">{error}</div>}
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setIsDialogOpen(false)}
                className="border-slate-800 hover:bg-slate-800 hover:text-slate-100 text-xs"
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={isCreating}
                className="bg-blue-600 hover:bg-blue-500 text-white font-semibold text-xs"
              >
                {isCreating ? 'Creating...' : 'Create Ticket'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
