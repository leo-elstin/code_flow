'use client';

import React from 'react';
import { CodeAgentExecution } from '@/lib/models';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

interface ExecutionsSectionProps {
  executions: CodeAgentExecution[];
  currentRunId: string | null;
  onSelectRun?: (runId: string) => void;
}

export default function ExecutionsSection({
  executions,
  currentRunId,
  onSelectRun,
}: ExecutionsSectionProps) {
  const formatTokens = (tokens: number) => {
    if (tokens >= 1000000) {
      return `${(tokens / 1000000).toFixed(1)}M`;
    }
    if (tokens >= 1000) {
      return `${(tokens / 1000).toFixed(1)}k`;
    }
    return tokens.toString();
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed':
        return 'text-emerald-500 bg-emerald-500/10 border-emerald-500/20';
      case 'failed':
        return 'text-rose-500 bg-rose-500/10 border-rose-500/20';
      case 'rejected':
        return 'text-amber-500 bg-amber-500/10 border-amber-500/20';
      default:
        return 'text-blue-500 bg-blue-500/10 border-blue-500/20';
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <h3 className="text-xs font-bold text-slate-400 tracking-wider uppercase">Executions</h3>
        <span className="text-[10px] text-slate-500 font-semibold bg-slate-100 px-1.5 py-0.5 rounded">
          {executions.length} attempt{executions.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="space-y-1.5">
        {executions.map((exec) => {
          const isCurrent = exec.run_id === currentRunId;
          const tokens = exec.token_usage?.total_tokens || 0;

          return (
            <Card
              key={exec.run_id}
              onClick={() => !isCurrent && onSelectRun && onSelectRun(exec.run_id)}
              className={`border transition-all select-none rounded-xl ${
                isCurrent
                  ? 'bg-blue-50/50 border-blue-500/40 ring-1 ring-blue-500/5'
                  : onSelectRun
                  ? 'bg-white border-slate-200/60 hover:bg-slate-50 cursor-pointer shadow-xs'
                  : 'bg-white border-slate-200/60 shadow-xs'
              }`}
            >
              <div className="p-3.5 flex items-center gap-3">
                {/* Attempt Badge */}
                <div className="w-8 h-8 rounded-full bg-blue-600/10 text-blue-600 font-bold text-xs flex items-center justify-center flex-shrink-0">
                  #{exec.attempt}
                </div>

                {/* Info block */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className={`text-[9px] font-bold uppercase tracking-wider py-0 px-2 rounded ${getStatusColor(exec.status)}`}>
                      {exec.status}
                    </Badge>
                    {isCurrent && (
                      <span className="text-[9px] font-extrabold text-blue-600 uppercase tracking-widest mt-[1px]">
                        current
                      </span>
                    )}
                  </div>
                  <div className="text-[10px] text-slate-400 font-mono truncate mt-1">
                    {exec.run_id}
                  </div>
                </div>

                {/* Token usage counter */}
                <div className="text-right flex-shrink-0">
                  <div className="text-xs font-bold text-slate-700 font-mono">
                    {formatTokens(tokens)}
                  </div>
                  <div className="text-[9px] font-bold text-slate-400 uppercase tracking-wide">
                    tokens
                  </div>
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
