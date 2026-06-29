'use client';

import React, { useState } from 'react';
import { TicketSummary } from '@/lib/models';
import { getChildren } from '@/lib/jira-hierarchy';
import TicketTypeBadge from '@/components/TicketTypeBadge';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { X, ChevronRight, ChevronDown } from 'lucide-react';

interface TicketChildrenDrawerProps {
  parent: TicketSummary | null;
  tickets: TicketSummary[];
  selectedTicketId: number | null;
  onSelectTicket: (id: number) => void;
  onClose: () => void;
}

function getStatusBadge(status: string) {
  let classes = '';
  let label = status;

  switch (status) {
    case 'completed':
      classes = 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20';
      label = 'Done';
      break;
    case 'failed':
    case 'rejected':
      classes = 'bg-rose-500/10 text-rose-600 border-rose-500/20';
      label = status === 'failed' ? 'Failed' : 'Rejected';
      break;
    case 'awaiting_approval':
      classes = 'bg-indigo-500/15 text-indigo-600 border-indigo-500/20';
      label = 'Needs Review';
      break;
    case 'developing':
    case 'planning':
      classes = 'bg-amber-500/10 text-amber-600 border-amber-500/20';
      label = 'In Progress';
      break;
    case 'verifying':
    case 'qa':
      classes = 'bg-sky-500/10 text-sky-600 border-sky-500/20';
      label = 'Verifying';
      break;
    case 'pending':
    default:
      classes = 'bg-slate-100 text-slate-500 border-slate-200';
      label = 'Todo';
      break;
  }

  return (
    <Badge variant="outline" className={`text-[10px] px-2 py-0.5 font-semibold tracking-wide rounded-md ${classes}`}>
      {label}
    </Badge>
  );
}

function ChildRow({
  ticket,
  tickets,
  depth,
  selectedTicketId,
  onSelectTicket,
}: {
  ticket: TicketSummary;
  tickets: TicketSummary[];
  depth: number;
  selectedTicketId: number | null;
  onSelectTicket: (id: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const grandChildren = getChildren(tickets, ticket);
  const hasGrandChildren = grandChildren.length > 0;
  const isSelected = ticket.id === selectedTicketId;

  return (
    <div>
      <div
        onClick={() => onSelectTicket(ticket.id)}
        style={{ marginLeft: depth * 14 }}
        className={`group relative flex items-center px-3 py-2.5 rounded-lg border text-left cursor-pointer transition-all ${
          isSelected
            ? 'bg-blue-50/70 border-blue-500/60 shadow-sm ring-1 ring-blue-500/10'
            : 'bg-white hover:bg-slate-50 border-slate-200/60 hover:border-slate-300/80 shadow-xs'
        }`}
      >
        {hasGrandChildren ? (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((v) => !v);
            }}
            className="mr-1 flex-shrink-0 text-slate-400 hover:text-blue-600 rounded transition-colors"
            title={expanded ? 'Collapse' : `Expand ${grandChildren.length} child ticket(s)`}
          >
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        ) : (
          <span className="mr-1 w-[14px] flex-shrink-0" />
        )}

        <div className="w-12 text-xs font-semibold font-mono text-slate-500 flex-shrink-0">
          T-{ticket.id}
        </div>

        <div className="flex-1 min-w-0 px-2">
          <div className="flex items-center gap-1.5">
            <TicketTypeBadge ticket={ticket} />
            <h4 className={`text-xs truncate font-medium ${isSelected ? 'text-blue-900 font-semibold' : 'text-slate-700'}`}>
              {ticket.title}
            </h4>
          </div>
          {ticket.jira_key && (
            <span className="text-[9px] font-semibold text-slate-400 font-mono mt-0.5 inline-block">
              {ticket.jira_key}
            </span>
          )}
        </div>

        <div className="flex-shrink-0 text-left">{getStatusBadge(ticket.status)}</div>
      </div>

      {expanded && hasGrandChildren && (
        <div className="mt-1 space-y-1">
          {grandChildren.map((gc) => (
            <ChildRow
              key={gc.id}
              ticket={gc}
              tickets={tickets}
              depth={depth + 1}
              selectedTicketId={selectedTicketId}
              onSelectTicket={onSelectTicket}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Slide-in side panel listing the child tickets assigned to a parent (Epic/Story).
 * Story children can be expanded inline to reveal their child Tasks.
 */
export default function TicketChildrenDrawer({
  parent,
  tickets,
  selectedTicketId,
  onSelectTicket,
  onClose,
}: TicketChildrenDrawerProps) {
  const isOpen = !!parent;
  const children = parent ? getChildren(tickets, parent) : [];

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        className={`fixed inset-0 z-40 bg-slate-900/30 backdrop-blur-[1px] transition-opacity duration-300 ${
          isOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
      />

      {/* Panel */}
      <div
        className={`fixed inset-y-0 right-0 z-50 w-[400px] max-w-[90vw] bg-slate-50 border-l border-slate-200 shadow-2xl flex flex-col transition-transform duration-300 ease-out ${
          isOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {parent && (
          <>
            {/* Header */}
            <div className="p-4 border-b border-slate-200 bg-white flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5">
                  <TicketTypeBadge ticket={parent} />
                  <span className="text-[10px] font-bold text-slate-400 tracking-wide uppercase">
                    Child Tickets ({children.length})
                  </span>
                </div>
                <h3 className="text-sm font-semibold text-slate-800 truncate mt-1">{parent.title}</h3>
                {parent.jira_key && (
                  <span className="text-[10px] font-semibold text-slate-400 font-mono">{parent.jira_key}</span>
                )}
              </div>
              <button
                onClick={onClose}
                className="flex-shrink-0 p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors"
                title="Close"
              >
                <X size={16} />
              </button>
            </div>

            {/* Children list */}
            <div className="flex-1 min-h-0">
              <ScrollArea className="h-full">
                {children.length === 0 ? (
                  <div className="p-8 text-center text-xs text-slate-400 font-medium">
                    No child tickets assigned to this {parent.jira_issue_type || 'ticket'}.
                  </div>
                ) : (
                  <div className="p-3 space-y-1">
                    {children.map((child) => (
                      <ChildRow
                        key={child.id}
                        ticket={child}
                        tickets={tickets}
                        depth={0}
                        selectedTicketId={selectedTicketId}
                        onSelectTicket={onSelectTicket}
                      />
                    ))}
                  </div>
                )}
              </ScrollArea>
            </div>
          </>
        )}
      </div>
    </>
  );
}
