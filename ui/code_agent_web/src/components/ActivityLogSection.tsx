'use client';

import React, { useState, useEffect, useRef } from 'react';
import { AgentActivityEvent, TokenUsage } from '@/lib/models';
import { Copy, Trash2, XCircle } from 'lucide-react';
import { toast } from 'sonner';

interface ActivityLogSectionProps {
  events: AgentActivityEvent[];
  currentAction: AgentActivityEvent | null;
  tokenUsage: TokenUsage;
  isRunning: boolean;
  hasRun: boolean;
  contextBundle?: Record<string, any>;
  onClear?: () => void;
  onStop?: () => void;
}

interface TerminalLine {
  time: string;
  level: 'INFO' | 'WARN';
  message: string;
}

export default function ActivityLogSection({
  events,
  currentAction,
  tokenUsage,
  isRunning,
  hasRun,
  contextBundle,
  onClear,
  onStop,
}: ActivityLogSectionProps) {
  const [elapsed, setElapsed] = useState('00:00:00');
  const scrollRef = useRef<HTMLDivElement>(null);
  const stopwatchRef = useRef<{ startTime: number; timerId: NodeJS.Timeout | null }>({
    startTime: 0,
    timerId: null,
  });

  // Scroll to bottom on new lines
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [events, currentAction]);

  // Stopwatch effect
  useEffect(() => {
    if (isRunning) {
      if (!stopwatchRef.current.timerId) {
        stopwatchRef.current.startTime = Date.now();
        stopwatchRef.current.timerId = setInterval(() => {
          const diff = Date.now() - stopwatchRef.current.startTime;
          const h = Math.floor(diff / 3600000).toString().padStart(2, '0');
          const m = Math.floor((diff % 3600000) / 60000).toString().padStart(2, '0');
          const s = Math.floor((diff % 60000) / 1000).toString().padStart(2, '0');
          setElapsed(`${h}:${m}:${s}`);
        }, 1000);
      }
    } else {
      if (stopwatchRef.current.timerId) {
        clearInterval(stopwatchRef.current.timerId);
        stopwatchRef.current.timerId = null;
      }
    }

    return () => {
      if (stopwatchRef.current.timerId) {
        clearInterval(stopwatchRef.current.timerId);
        stopwatchRef.current.timerId = null;
      }
    };
  }, [isRunning]);

  // Format timestamp helper
  const formatTime = (iso?: string) => {
    if (!iso) return '';
    try {
      const date = new Date(iso);
      const h = date.getHours().toString().padStart(2, '0');
      const m = date.getMinutes().toString().padStart(2, '0');
      const s = date.getSeconds().toString().padStart(2, '0');
      return `${h}:${m}:${s}`;
    } catch (_) {
      return '';
    }
  };

  // Convert raw events into terminal log lines
  const buildLogLines = (): TerminalLine[] => {
    const lines: TerminalLine[] = [];
    const displayEvents = events.filter((e) => e.type !== 'token');

    displayEvents.forEach((ev) => {
      const titleLower = ev.title.toLowerCase();
      const passed = ev.meta?.passed;
      const isWarning = passed === false || titleLower.includes('fail');
      const level = isWarning ? 'WARN' : 'INFO';
      
      const tool = ev.meta?.tool;
      const message = tool ? `${ev.title} (${tool})` : ev.title;

      lines.push({
        time: formatTime(ev.created_at),
        level,
        message,
      });

      if (ev.type === 'thinking' && ev.detail) {
        lines.push({
          time: '',
          level: 'INFO',
          message: `  ${ev.detail.trim()}`,
        });
      }

      if (Array.isArray(ev.files)) {
        ev.files.forEach((file) => {
          if (file) {
            lines.push({
              time: '',
              level: 'INFO',
              message: `  → ${file}`,
            });
          }
        });
      }
    });

    // Append active/current action
    if (isRunning && currentAction) {
      const activeMsg = currentAction.detail
        ? `${currentAction.title}: ${currentAction.detail}`
        : currentAction.title;
      
      const lastLine = lines[lines.length - 1];
      if (!lastLine || lastLine.message !== activeMsg) {
        lines.push({
          time: formatTime(new Date().toISOString()),
          level: 'INFO',
          message: activeMsg,
        });
      }
    }

    return lines;
  };

  const lines = buildLogLines();

  // Copy helper
  const handleCopy = () => {
    if (lines.length === 0) return;
    const text = lines
      .map((l) => {
        const timePart = l.time ? `${l.time} ` : '';
        const levelPart = l.time ? `[${l.level}] ` : '';
        return `${timePart}${levelPart}${l.message}`;
      })
      .join('\n');
    
    navigator.clipboard.writeText(text);
    toast.success('Activity log copied to clipboard');
  };

  const formatTokenCount = (count: number) => {
    if (count >= 1000) return `${(count / 1000).toFixed(1)}k`;
    return count.toString();
  };

  const getEmptyMessage = () => {
    if (!hasRun) {
      return 'Waiting for execution. Click Start Run to initiate the agent.';
    }
    if (contextBundle && Array.isArray(contextBundle.grep_matches) && contextBundle.grep_matches.length > 0) {
      return 'Discovery completed. Awaiting activity events...';
    }
    return 'Agent initializing...';
  };

  return (
    <div className="flex flex-col space-y-1.5">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold text-slate-400 tracking-wider uppercase">Live Activity Log</h3>
        {tokenUsage.total_tokens > 0 && (
          <span className="text-[10px] text-slate-500 font-mono">
            {formatTokenCount(tokenUsage.total_tokens)} tokens used
          </span>
        )}
      </div>

      <div className="rounded-xl overflow-hidden border border-slate-950/20 shadow-sm flex flex-col">
        {/* Terminal Header */}
        <div className="px-4 py-2 bg-neutral-900 border-b border-neutral-950 flex items-center justify-between">
          <span className="text-xs font-bold text-slate-300 font-mono tracking-wide">console.log</span>
          <div className="flex items-center gap-2">
            <button
              onClick={handleCopy}
              disabled={lines.length === 0}
              className="p-1 rounded-md text-slate-400 hover:text-white hover:bg-neutral-800 transition-colors disabled:opacity-20"
              title="Copy to clipboard"
            >
              <Copy size={13} />
            </button>
            {onClear && (
              <button
                onClick={onClear}
                disabled={lines.length === 0}
                className="p-1 rounded-md text-slate-400 hover:text-white hover:bg-neutral-800 transition-colors disabled:opacity-20"
                title="Clear logs"
              >
                <Trash2 size={13} />
              </button>
            )}
          </div>
        </div>

        {/* Terminal Body */}
        <div 
          ref={scrollRef}
          className="bg-neutral-950 text-slate-200 p-4 font-mono text-[11px] h-52 overflow-y-auto pr-2 select-text leading-5"
        >
          {lines.length === 0 ? (
            <p className="text-slate-500 italic pr-2 select-none">{getEmptyMessage()}</p>
          ) : (
            lines.map((line, idx) => {
              const isWarn = line.level === 'WARN';
              return (
                <div key={idx} className="whitespace-pre-wrap select-text">
                  {line.time && <span className="text-slate-600 select-none mr-2">{line.time}</span>}
                  {line.time && (
                    <span className={`font-bold mr-2 select-none ${isWarn ? 'text-amber-500' : 'text-sky-500'}`}>
                      [{line.level}]
                    </span>
                  )}
                  <span className={line.time ? 'text-slate-200' : 'text-slate-400'}>{line.message}</span>
                </div>
              );
            })
          )}
        </div>

        {/* Live Running Indicator Footer */}
        {isRunning && (
          <div className="px-4 py-3 bg-blue-600 text-white flex items-center justify-between gap-3 shadow-inner">
            <div className="flex items-center gap-2.5 min-w-0">
              {/* Waveform bouncing bars animation */}
              <div className="flex items-end gap-[2px] h-3.5 w-5 flex-shrink-0 mb-0.5">
                <span className="w-[3px] rounded-full bg-white/95 animate-bar-1" />
                <span className="w-[3px] rounded-full bg-white/95 animate-bar-2" />
                <span className="w-[3px] rounded-full bg-white/95 animate-bar-3" />
                <span className="w-[3px] rounded-full bg-white/95 animate-bar-4" />
              </div>
              <span className="text-xs font-bold truncate leading-none">
                {currentAction?.title || 'Agent Executing Pipeline...'}
              </span>
            </div>
            <div className="flex items-center gap-3 flex-shrink-0">
              <span className="font-mono text-xs font-semibold select-none leading-none mt-[1px]">{elapsed}</span>
              {onStop && (
                <button
                  onClick={onStop}
                  className="bg-white text-blue-800 hover:bg-slate-50 font-bold text-[10px] uppercase px-3 py-1.5 rounded-lg transition-colors flex items-center gap-1 shadow-xs"
                >
                  <XCircle size={10} />
                  <span>Stop watching</span>
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Inject Bar Keyframe Styles */}
      <style jsx global>{`
        @keyframes bounce-bar {
          0%, 100% { height: 4px; }
          50% { height: 14px; }
        }
        .animate-bar-1 { animation: bounce-bar 0.8s ease-in-out infinite; }
        .animate-bar-2 { animation: bounce-bar 0.8s ease-in-out infinite 0.2s; }
        .animate-bar-3 { animation: bounce-bar 0.8s ease-in-out infinite 0.4s; }
        .animate-bar-4 { animation: bounce-bar 0.8s ease-in-out infinite 0.6s; }
      `}</style>
    </div>
  );
}
