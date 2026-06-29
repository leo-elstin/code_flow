'use client';

import React from 'react';
import { POSession, POSessionStatus } from '@/lib/po-api-client';
import { Plus } from 'lucide-react';

interface StatusDotProps {
  status: POSessionStatus;
}

function StatusDot({ status }: StatusDotProps) {
  const colorMap: Record<POSessionStatus, string> = {
    classifying: 'bg-yellow-400',
    brainstorming: 'bg-blue-400',
    waiting_for_reply: 'bg-blue-500',
    researching: 'bg-purple-400',
    drafting: 'bg-purple-500',
    awaiting_review: 'bg-indigo-400',
    approved: 'bg-green-500',
    rejected: 'bg-red-500',
    failed: 'bg-red-400',
  };
  return (
    <span
      className={`w-2 h-2 rounded-full shrink-0 ${colorMap[status] || 'bg-slate-400'}`}
    />
  );
}

interface POSessionListProps {
  sessions: POSession[];
  onSelect: (id: string) => void;
  onNewSession?: () => void;
  selectedSessionId?: string | null;
}

export default function POSessionList({
  sessions,
  onSelect,
  onNewSession,
  selectedSessionId,
}: POSessionListProps) {
  return (
    <div className="space-y-0.5">
      <div className="flex items-center justify-between px-3 py-2">
        <span className="text-[10px] font-bold text-slate-500 tracking-wider uppercase">
          PO Sessions
        </span>
        {onNewSession && (
          <button
            onClick={onNewSession}
            className="text-slate-500 hover:text-white transition-colors"
            title="New PO session"
          >
            <Plus size={14} />
          </button>
        )}
      </div>

      {sessions.length === 0 ? (
        <div className="px-3 py-2 text-xs text-slate-500 italic">No sessions yet</div>
      ) : (
        sessions.map((s) => {
          const isSelected = s.session_id === selectedSessionId;
          return (
            <button
              key={s.session_id}
              onClick={() => onSelect(s.session_id)}
              className={`w-full text-left px-3 py-2 rounded-lg transition-all ${
                isSelected
                  ? 'bg-slate-900 text-white shadow-sm ring-1 ring-white/5'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/40'
              }`}
            >
              <div className="flex items-center gap-2">
                <StatusDot status={s.status} />
                <span className="text-xs truncate flex-1">
                  {s.initial_context.slice(0, 60)}
                  {s.initial_context.length > 60 ? '…' : ''}
                </span>
                {s.status === 'awaiting_review' && (
                  <span className="text-[10px] bg-indigo-500/20 text-indigo-300 px-1.5 py-0.5 rounded shrink-0 font-medium">
                    Review
                  </span>
                )}
              </div>
            </button>
          );
        })
      )}
    </div>
  );
}
