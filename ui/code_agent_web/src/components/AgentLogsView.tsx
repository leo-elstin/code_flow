'use client';

import React from 'react';
import { TicketSummary, CodeAgentRunStatus, AgentActivityEvent, TokenUsage } from '@/lib/models';
import { FileText, ScrollText } from 'lucide-react';
import ActivityLogSection from './ActivityLogSection';

interface AgentLogsViewProps {
  ticket: TicketSummary | null;
  run: CodeAgentRunStatus | null;
  activityEvents: AgentActivityEvent[];
  currentAction: AgentActivityEvent | null;
  tokenUsage: TokenUsage;
  onClear: () => void;
  onStop?: () => void;
}

export default function AgentLogsView({
  ticket,
  run,
  activityEvents,
  currentAction,
  tokenUsage,
  onClear,
  onStop,
}: AgentLogsViewProps) {
  return (
    <div className="flex-1 bg-white p-6 md:p-8 overflow-y-auto h-full space-y-6 select-none">
      <div>
        <h2 className="text-xl font-bold text-slate-800 flex items-center gap-2">
          <ScrollText size={22} className="text-slate-600" />
          <span>Agent Logs</span>
        </h2>
        <p className="text-xs text-slate-400 font-medium">
          {ticket 
            ? `Active log trace for Task T-${ticket.id}: ${ticket.title}` 
            : 'Select a task from the queue to view details and monitor server activity logs.'}
        </p>
      </div>

      {!run ? (
        <div className="flex flex-col items-center justify-center p-12 border border-dashed border-slate-200 rounded-xl h-64 text-center">
          <FileText size={32} className="text-slate-300 mb-2" />
          <p className="text-xs text-slate-500 font-medium leading-normal max-w-[280px]">
            {ticket 
              ? 'No active run trace is registered for this task. Start execution from the Overview tab.'
              : 'Please select a task from the side panel first.'}
          </p>
        </div>
      ) : (
        <div className="bg-slate-50/50 p-4 border border-slate-100 rounded-xl">
          <ActivityLogSection
            events={activityEvents}
            currentAction={currentAction || run.current_action || null}
            tokenUsage={tokenUsage}
            isRunning={run.is_running}
            hasRun={true}
            contextBundle={run.context_bundle}
            onClear={onClear}
            onStop={run.is_running ? onStop : undefined}
          />
        </div>
      )}
    </div>
  );
}
