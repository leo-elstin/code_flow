'use client';

import React from 'react';
import { AgentActivityEvent } from '@/lib/models';
import {
  CheckCircle,
  XCircle,
  Clock,
  Loader,
  GitBranch,
  Code,
  ShieldCheck,
  Star,
  PauseCircle,
} from 'lucide-react';

interface RunTimelineProps {
  events: AgentActivityEvent[];
  runStatus: string;
  mergedAt?: string;
}

interface Milestone {
  id: string;
  phase: 'planning' | 'approval' | 'dev' | 'verifier' | 'qa' | 'merged';
  label: string;
  sublabel?: string;
  startAt: Date;
  endAt?: Date;
  status: 'done' | 'failed' | 'active' | 'waiting';
  iteration?: number;
  subSteps?: { label: string; at: Date; durationMs?: number }[];
}

function findFirst(events: AgentActivityEvent[], predicate: (e: AgentActivityEvent) => boolean) {
  return events.find(predicate);
}

function toDate(iso: string | undefined): Date | undefined {
  if (!iso) return undefined;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? undefined : d;
}

function durationLabel(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`;
}

function formatTime(d: Date): string {
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function buildMilestones(
  events: AgentActivityEvent[],
  runStatus: string,
  mergedAt?: string,
): Milestone[] {
  const milestones: Milestone[] = [];

  // ── Planning ──────────────────────────────────────────────────────────────
  const planStart = findFirst(events, e => e.phase === 'planner' && e.title === 'Planning started');
  const planReady = findFirst(events, e => e.phase === 'planner' && e.title === 'Plan ready for approval');

  if (planStart) {
    const start = toDate(planStart.created_at)!;
    const end = toDate(planReady?.created_at);
    milestones.push({
      id: 'planning',
      phase: 'planning',
      label: 'Planning',
      startAt: start,
      endAt: end,
      status: end ? 'done' : 'active',
    });
  }

  // ── Approval (awaiting or already done) ───────────────────────────────────
  if (planReady) {
    const approvalStart = toDate(planReady.created_at)!;
    // Approval ends when dev iteration 0 begins
    const devEntry0 = findFirst(events, e =>
      e.phase === 'dev' && (
        e.title === 'Entering development (iteration 0)' ||
        e.title.startsWith('Entering development (iteration 0')
      )
    );
    const approvalEnd = toDate(devEntry0?.created_at);
    const isDone = !!approvalEnd;
    const isWaiting = runStatus === 'awaiting_approval';

    milestones.push({
      id: 'approval',
      phase: 'approval',
      label: 'Awaiting approval',
      startAt: approvalStart,
      endAt: approvalEnd,
      status: isDone ? 'done' : isWaiting ? 'active' : 'waiting',
    });
  }

  // ── Dev + Verifier iterations ─────────────────────────────────────────────
  // Collect all "Entering development" and "Entering verification" events, paired by iteration.
  const devEntries = events.filter(
    e => e.phase === 'dev' && e.title.startsWith('Entering development (iteration')
  );
  const verEntries = events.filter(
    e => e.phase === 'verifier' && e.title.startsWith('Entering verification (iteration')
  );

  // Determine max iteration count from events
  const maxIter = Math.max(devEntries.length, verEntries.length, 1);

  for (let i = 0; i < maxIter; i++) {
    const devEntry = devEntries[i];
    const verEntry = verEntries[i];
    const nextDevEntry = devEntries[i + 1];

    // ── Dev iteration ──────────────────────────────────────────────────────
    if (devEntry) {
      const devStart = toDate(devEntry.created_at)!;
      // Dev ends when verifier starts (same iteration)
      const devEnd = verEntry ? toDate(verEntry.created_at) : undefined;

      const devStarted = findFirst(
        events,
        e =>
          e.phase === 'dev' &&
          e.title === 'Development started (native tool calling)' &&
          toDate(e.created_at)! >= devStart &&
          (!verEntry || toDate(e.created_at)! < toDate(verEntry.created_at)!)
      );

      milestones.push({
        id: `dev-${i}`,
        phase: 'dev',
        label: i === 0 ? 'Development' : `Development (fix ${i})`,
        iteration: i,
        startAt: devStart,
        endAt: devEnd,
        status: devEnd ? 'done' : runStatus === 'developing' ? 'active' : 'waiting',
      });
    }

    // ── Verifier iteration ────────────────────────────────────────────────
    if (verEntry) {
      const verStart = toDate(verEntry.created_at)!;
      // Verifier ends when next dev starts, or QA starts, or run ends
      const qaEntry = findFirst(events, e => e.phase === 'qa' && e.title === 'Entering QA');
      const verEnd = nextDevEntry
        ? toDate(nextDevEntry.created_at)
        : qaEntry
        ? toDate(qaEntry.created_at)
        : undefined;

      // Sub-steps: pub get, build_runner, dart analyze, flutter test, verifier LLM
      const toolsInRange = events.filter(e => {
        if (e.phase !== 'verifier') return false;
        const t = toDate(e.created_at);
        if (!t || t < verStart) return false;
        if (verEnd && t >= verEnd) return false;
        return ['pub get', 'build_runner', 'dart analyze', 'flutter test', 'flutter test failed'].includes(e.title) ||
          (e.title === 'Verification started') ||
          (e.title === 'Completed verifier') ||
          e.title.startsWith('Running ');
      });

      const subSteps = toolsInRange.map((e, idx) => {
        const at = toDate(e.created_at)!;
        const next = toolsInRange[idx + 1];
        const nextAt = next ? toDate(next.created_at) : verEnd;
        return {
          label: e.title,
          at,
          durationMs: nextAt ? nextAt.getTime() - at.getTime() : undefined,
        };
      });

      // Verifier outcome
      const verPassed = findFirst(
        events,
        e =>
          e.phase === 'verifier' &&
          e.title === 'Completed verifier' &&
          toDate(e.created_at)! >= verStart
      );
      const verFailed = runStatus === 'failed' && !nextDevEntry && !qaEntry;

      let verStatus: Milestone['status'] = 'waiting';
      if (verEnd || (runStatus !== 'verifying')) {
        verStatus = verFailed ? 'failed' : 'done';
      } else if (runStatus === 'verifying') {
        verStatus = 'active';
      }

      // Check if verifier passed this iteration (QA follows or next dev doesn't exist but status is completed)
      const isLastIter = !nextDevEntry;
      const passedThisIter = isLastIter && !!qaEntry;

      milestones.push({
        id: `verifier-${i}`,
        phase: 'verifier',
        label: `Verification${maxIter > 1 ? ` (${i + 1}/${maxIter})` : ''}`,
        sublabel: verEnd
          ? passedThisIter
            ? 'Passed'
            : isLastIter && verFailed
            ? 'Failed — exhausted retries'
            : 'Rejected → back to dev'
          : undefined,
        iteration: i,
        startAt: verStart,
        endAt: verEnd,
        status: verStatus,
        subSteps,
      });
    }
  }

  // ── QA ────────────────────────────────────────────────────────────────────
  const qaEntry = findFirst(events, e => e.phase === 'qa' && e.title === 'Entering QA');
  if (qaEntry) {
    const qaStart = toDate(qaEntry.created_at)!;
    const qaEnd = findFirst(
      events,
      e => e.phase === 'qa' && e.title === 'QA report generated'
    );
    const qaEndAt = toDate(qaEnd?.created_at);

    milestones.push({
      id: 'qa',
      phase: 'qa',
      label: 'QA review',
      startAt: qaStart,
      endAt: qaEndAt,
      status: qaEndAt ? 'done' : runStatus === 'qa' ? 'active' : 'waiting',
    });
  }

  // ── Merged ────────────────────────────────────────────────────────────────
  if (mergedAt) {
    const mergedDate = toDate(mergedAt);
    if (mergedDate) {
      milestones.push({
        id: 'merged',
        phase: 'merged',
        label: 'Merged to base',
        startAt: mergedDate,
        status: 'done',
      });
    }
  }

  return milestones;
}

// ── Phase colours ─────────────────────────────────────────────────────────────

const PHASE_STYLES: Record<string, { dot: string; line: string; badge: string }> = {
  planning:  { dot: 'bg-violet-500',  line: 'bg-violet-200',  badge: 'bg-violet-50 text-violet-700 border-violet-200' },
  approval:  { dot: 'bg-amber-400',   line: 'bg-amber-200',   badge: 'bg-amber-50 text-amber-700 border-amber-200' },
  dev:       { dot: 'bg-blue-500',    line: 'bg-blue-200',    badge: 'bg-blue-50 text-blue-700 border-blue-200' },
  verifier:  { dot: 'bg-teal-500',    line: 'bg-teal-200',    badge: 'bg-teal-50 text-teal-700 border-teal-200' },
  qa:        { dot: 'bg-emerald-500', line: 'bg-emerald-200', badge: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  merged:    { dot: 'bg-slate-700',   line: 'bg-slate-200',   badge: 'bg-slate-50 text-slate-700 border-slate-200' },
};

function PhaseIcon({ phase, status, size = 14 }: { phase: string; status: string; size?: number }) {
  const cls = `text-current`;
  if (status === 'active') return <Loader size={size} className={`${cls} animate-spin`} />;
  if (status === 'failed') return <XCircle size={size} className={cls} />;
  if (status === 'waiting') return <Clock size={size} className={`${cls} opacity-40`} />;
  switch (phase) {
    case 'planning':  return <GitBranch size={size} className={cls} />;
    case 'approval':  return <PauseCircle size={size} className={cls} />;
    case 'dev':       return <Code size={size} className={cls} />;
    case 'verifier':  return <ShieldCheck size={size} className={cls} />;
    case 'qa':        return <Star size={size} className={cls} />;
    case 'merged':    return <CheckCircle size={size} className={cls} />;
    default:          return <CheckCircle size={size} className={cls} />;
  }
}

export default function RunTimeline({ events, runStatus, mergedAt }: RunTimelineProps) {
  const milestones = buildMilestones(events, runStatus, mergedAt);

  if (milestones.length === 0) {
    return (
      <div className="flex items-center justify-center h-32 text-xs text-slate-400">
        No timeline data yet.
      </div>
    );
  }

  return (
    <div className="space-y-0">
      {milestones.map((m, idx) => {
        const styles = PHASE_STYLES[m.phase] ?? PHASE_STYLES.dev;
        const isLast = idx === milestones.length - 1;
        const durationMs =
          m.endAt && m.startAt ? m.endAt.getTime() - m.startAt.getTime() : undefined;

        return (
          <div key={m.id} className="flex gap-3">
            {/* Spine */}
            <div className="flex flex-col items-center">
              <div
                className={`w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 border-2 border-white shadow-sm ${
                  m.status === 'failed'
                    ? 'bg-rose-500 text-white'
                    : m.status === 'active'
                    ? styles.dot + ' text-white'
                    : m.status === 'done'
                    ? styles.dot + ' text-white'
                    : 'bg-slate-200 text-slate-400'
                }`}
              >
                <PhaseIcon phase={m.phase} status={m.status} size={13} />
              </div>
              {!isLast && (
                <div className={`w-0.5 flex-1 min-h-4 my-0.5 ${isLast ? 'invisible' : styles.line}`} />
              )}
            </div>

            {/* Content */}
            <div className={`pb-5 flex-1 min-w-0 ${isLast ? 'pb-0' : ''}`}>
              <div className="flex items-start gap-2 flex-wrap">
                <span className="text-xs font-semibold text-slate-800">{m.label}</span>

                {m.sublabel && (
                  <span
                    className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border ${
                      m.status === 'failed'
                        ? 'bg-rose-50 text-rose-700 border-rose-200'
                        : styles.badge
                    }`}
                  >
                    {m.sublabel}
                  </span>
                )}

                {durationMs !== undefined && (
                  <span className="ml-auto text-[10px] font-mono text-slate-400 flex-shrink-0">
                    {durationLabel(durationMs)}
                  </span>
                )}
                {!m.endAt && m.status === 'active' && (
                  <span className="ml-auto text-[10px] text-blue-500 font-medium animate-pulse flex-shrink-0">
                    running…
                  </span>
                )}
              </div>

              <div className="text-[10px] text-slate-400 font-mono mt-0.5">
                {formatTime(m.startAt)}
                {m.endAt && (
                  <span className="text-slate-300"> → {formatTime(m.endAt)}</span>
                )}
              </div>

              {/* Sub-steps for verifier */}
              {m.subSteps && m.subSteps.length > 0 && (
                <div className="mt-2 space-y-1 pl-2 border-l-2 border-slate-100">
                  {m.subSteps.map((step, si) => (
                    <div key={si} className="flex items-center gap-2">
                      <span className="text-[10px] text-slate-500 font-medium truncate">{step.label}</span>
                      <span className="text-[10px] font-mono text-slate-300 ml-auto flex-shrink-0">
                        {formatTime(step.at)}
                        {step.durationMs !== undefined && step.durationMs > 500 && (
                          <span className="text-slate-400 ml-1">({durationLabel(step.durationMs)})</span>
                        )}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
