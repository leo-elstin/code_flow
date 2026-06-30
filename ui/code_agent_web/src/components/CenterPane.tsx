'use client';

import React, { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { TicketSummary, CodeAgentRunStatus } from '@/lib/models';
import { 
  ArrowLeft,
  ChevronDown, 
  RotateCcw,
  CheckCircle,
  XCircle,
  FileCode,
  CheckSquare,
  AlertTriangle,
  Play,
  CornerDownLeft,
  Settings,
  Activity,
  GitBranch,
  History,
  FileText,
  AlertCircle
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import ActivityLogSection from './ActivityLogSection';
import ExecutionsSection from './ExecutionsSection';
import RunTimeline from './RunTimeline';
import ClarificationPanel from './ClarificationPanel';
import { ClarifyAnswer } from '@/lib/models';

interface CenterPaneProps {
  ticket: TicketSummary | null;
  run: CodeAgentRunStatus | null;
  activityEvents: any[];
  currentAction: any;
  tokenUsage: any;
  executions: any[];
  isBusy: boolean;
  isRunLoading: boolean;
  onClearRunView: () => void;
  onStartRun: (ticketId: number) => Promise<void>;
  onApproveRun: (workspaceMode: 'worktree' | 'in_place') => Promise<void>;
  onRejectRun: (feedback?: string) => Promise<void>;
  onRetryRun: () => Promise<void>;
  onRevertRun: () => Promise<void>;
  onResumeRun: () => Promise<void>;
  onMergeRun: () => Promise<void>;
  onClearActivityLogs: () => void;
  onStopWatchingRun: () => void;
  onClarifyRun: (answers: ClarifyAnswer[]) => Promise<void>;
}

export default function CenterPane({
  ticket,
  run,
  activityEvents,
  currentAction,
  tokenUsage,
  executions,
  isBusy,
  isRunLoading,
  onClearRunView,
  onStartRun,
  onApproveRun,
  onRejectRun,
  onRetryRun,
  onRevertRun,
  onResumeRun,
  onMergeRun,
  onClearActivityLogs,
  onStopWatchingRun,
  onClarifyRun,
}: CenterPaneProps) {
  const [activeTab, setActiveTab] = useState('overview');
  const [isApproveOpen, setIsApproveOpen] = useState(false);

  const planMarkdown: string | undefined = run?.plan?.plan_markdown as string | undefined;

  // Auto-switch to Plan tab when plan is ready for approval
  useEffect(() => {
    if (run?.status === 'awaiting_approval' && planMarkdown) {
      setActiveTab('plan');
    }
  }, [run?.status, planMarkdown]);
  const [isRejectOpen, setIsRejectOpen] = useState(false);
  const [isRevertOpen, setIsRevertOpen] = useState(false);
  const [isMergeOpen, setIsMergeOpen] = useState(false);
  const [rejectFeedback, setRejectFeedback] = useState('');
  const [reverting, setReverting] = useState(false);
  const [merging, setMerging] = useState(false);
  const [actionError, setActionError] = useState('');

  if (!ticket) {
    return (
      <div className="flex flex-col items-center justify-center h-full p-8 text-center bg-slate-50/20 select-none">
        <FileText size={48} className="text-slate-300 mb-4" />
        <h3 className="text-sm font-bold text-slate-700">No Ticket Selected</h3>
        <p className="text-xs text-slate-400 max-w-[240px] mt-1.5 leading-normal">
          Select a task from the queue to view implementation plans and run execution.
        </p>
      </div>
    );
  }

  // Parse acceptance criteria
  const criteriaTexts: string[] = [];
  if (run && run.acceptance_criteria && run.acceptance_criteria.length > 0) {
    criteriaTexts.push(...run.acceptance_criteria);
  } else if (run && run.plan && Array.isArray(run.plan.acceptance_criteria)) {
    run.plan.acceptance_criteria.forEach((item: any) => {
      criteriaTexts.push(String(item));
    });
  }

  const isCompleted = ticket.status === 'completed' || run?.status === 'completed';
  const hasGlobalError = run && run.error;
  const hasVerifierReport = run && run.verifier_report;
  
  // Parse verifier blockers and warnings
  const verifierBlockers: string[] = [];
  const verifierWarnings: string[] = [];
  if (hasVerifierReport && run.verifier_report) {
    const report = run.verifier_report;
    const gate = report.deterministic_gate || report;
    
    if (Array.isArray(gate.required_fixes)) {
      gate.required_fixes.forEach((fix: any) => {
        if (fix) verifierBlockers.push(String(fix));
      });
    }
    if (Array.isArray(report.required_fixes)) {
      report.required_fixes.forEach((fix: any) => {
        if (fix && !verifierBlockers.includes(String(fix))) {
          verifierBlockers.push(String(fix));
        }
      });
    }

    if (Array.isArray(gate.issues)) {
      gate.issues.forEach((issue: any) => {
        if (issue && typeof issue === 'object') {
          const reason = issue.reason || '';
          if (reason) {
            if (issue.severity === 'warning') {
              verifierWarnings.push(reason);
            } else {
              verifierBlockers.push(reason);
            }
          }
        }
      });
    }
  }

  const handleStartWork = async () => {
    setActionError('');
    try {
      await onStartRun(ticket.id);
    } catch (err: any) {
      setActionError(err.message || 'Failed to start execution');
    }
  };

  const handleApproveSubmit = async (workspaceMode: 'worktree' | 'in_place') => {
    setActionError('');
    setIsApproveOpen(false);
    try {
      await onApproveRun(workspaceMode);
    } catch (err: any) {
      setActionError(err.message || 'Failed to approve plan');
    }
  };

  const handleRejectSubmit = async () => {
    setActionError('');
    setIsRejectOpen(false);
    try {
      await onRejectRun(rejectFeedback.trim() || undefined);
      setRejectFeedback('');
    } catch (err: any) {
      setActionError(err.message || 'Failed to reject plan');
    }
  };

  const handleRevertSubmit = async () => {
    setActionError('');
    setIsRevertOpen(false);
    setReverting(true);
    try {
      await onRevertRun();
    } catch (err: any) {
      setActionError(err.message || 'Failed to revert run');
    } finally {
      setReverting(false);
    }
  };

  const handleMergeSubmit = async () => {
    setActionError('');
    setIsMergeOpen(false);
    setMerging(true);
    try {
      await onMergeRun();
    } catch (err: any) {
      setActionError(err.message || 'Failed to merge changes');
    } finally {
      setMerging(false);
    }
  };

  // Render progress segments
  const renderProgressBar = () => {
    const activeColor = 'bg-amber-500 shadow-[0_0_8px_rgba(245,158,11,0.5)]';
    const doneColor = 'bg-blue-600';
    const idleColor = 'bg-slate-200';
    const failedColor = 'bg-rose-500';

    let seg1 = idleColor;
    let seg2 = idleColor;
    let seg3 = idleColor;

    if (ticket.status === 'failed' || ticket.status === 'rejected' || (run && run.status === 'failed')) {
      seg1 = failedColor;
    } else if (ticket.status === 'completed' || (run && run.status === 'completed')) {
      seg1 = doneColor;
      seg2 = doneColor;
      seg3 = doneColor;
    } else if (run) {
      const status = run.status;
      seg1 = doneColor;
      
      if (status === 'planning' || status === 'awaiting_approval' || status === 'pending') {
        seg2 = run.is_running ? activeColor : idleColor;
      } else if (status === 'developing') {
        seg2 = run.is_running ? activeColor : doneColor;
      } else if (status === 'verifying' || status === 'qa') {
        seg2 = doneColor;
        seg3 = run.is_running ? activeColor : doneColor;
      } else {
        seg2 = activeColor;
      }
    }

    return (
      <div className="flex gap-1 h-1.5 my-3">
        <div className={`flex-1 rounded-full ${seg1} transition-all duration-300`} title="Planning" />
        <div className={`flex-1 rounded-full ${seg2} transition-all duration-300`} title="Development" />
        <div className={`flex-1 rounded-full ${seg3} transition-all duration-300`} title="Verification / QA" />
      </div>
    );
  };

  // Build files changed list
  const renderFilesList = () => {
    const seenPaths = new Set<string>();
    const items: React.ReactNode[] = [];

    // Extract files from activity events
    if (run && (run.is_running || run.status === 'developing' || run.status === 'verifying')) {
      activityEvents.forEach((ev: any) => {
        if (ev.type === 'tool' && Array.isArray(ev.files)) {
          ev.files.forEach((file: string) => {
            if (file && !seenPaths.has(file)) {
              seenPaths.add(file);
              const tool = ev.meta?.tool || '';
              const action = tool === 'write_file' ? 'Modified' : 'Modified';
              items.push(
                <div key={file} className="font-mono text-xs text-slate-600 hover:text-slate-900 flex items-center gap-1.5 py-0.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-blue-400"></span>
                  <span>{action} &apos;{file}&apos;</span>
                </div>
              );
            }
          });
        }
      });
    }

    // Extract files from run changes
    if (run && run.file_changes) {
      run.file_changes.forEach((change: any) => {
        const path = change.path || 'unknown';
        if (path && !seenPaths.has(path)) {
          seenPaths.add(path);
          const action = change.action || 'modified';
          const isCreated = action.toLowerCase() === 'create' || action.toLowerCase() === 'created';
          items.push(
            <div key={path} className="font-mono text-xs text-slate-600 hover:text-slate-900 flex items-center gap-1.5 py-0.5">
              <span className={`w-1.5 h-1.5 rounded-full ${isCreated ? 'bg-emerald-400' : 'bg-blue-400'}`}></span>
              <span>{isCreated ? 'Created' : 'Modified'} &apos;{path}&apos;</span>
            </div>
          );
        }
      });
    }

    if (items.length === 0) {
      return <p className="text-xs text-slate-400 italic">No files affected yet.</p>;
    }

    return <div className="space-y-1 mt-1">{items}</div>;
  };

  const getPrimaryActionButtonLabel = () => {
    if (isBusy || reverting || merging) return 'Working...';
    if (!run) return 'Start Run';
    if (run.status === 'failed' || run.status === 'rejected') return 'Retry Run';
    if (run.status === 'awaiting_clarification') return 'Answer Questions';
    if (run.status === 'awaiting_approval') return 'Review & Approve Plan';
    if (!run.is_running && ['planning', 'developing', 'verifying', 'qa'].includes(run.status)) {
      return 'Resume Run';
    }
    return 'Start Run';
  };

  const executePrimaryAction = () => {
    if (isBusy || reverting || merging) return;
    if (!run) {
      handleStartWork();
    } else if (run.status === 'failed' || run.status === 'rejected') {
      onRetryRun().catch((err) => setActionError(err.message || 'Failed to retry run'));
    } else if (run.status === 'awaiting_clarification') {
      // scroll to top of overview — the panel is visible there
      setActiveTab('overview');
    } else if (run.status === 'awaiting_approval') {
      setIsApproveOpen(true);
    } else if (!run.is_running && ['planning', 'developing', 'verifying', 'qa'].includes(run.status)) {
      onResumeRun().catch((err) => setActionError(err.message || 'Failed to resume run'));
    } else {
      handleStartWork();
    }
  };

  return (
    <div className="flex flex-col h-full bg-white select-none">
      {/* Top back chevron bar */}
      <div className="p-3 border-b border-slate-100 flex items-center justify-between">
        <button
          onClick={onClearRunView}
          className="p-1 text-slate-400 hover:text-slate-700 rounded-lg hover:bg-slate-100/60 transition-colors"
          title="Back to queue"
        >
          <ArrowLeft size={16} />
        </button>
        {run && (
          <span className="text-[10px] text-slate-400 font-mono tracking-wider">
            RUN: {run.run_id.slice(0, 8)}...
          </span>
        )}
      </div>

      {/* Main Body Layout */}
      <div className="flex-1 overflow-hidden flex flex-col min-h-0">
        <div className="p-6 pb-2">
          {/* Title */}
          <h2 className="text-base font-bold text-slate-800 flex items-center gap-2">
            <span className="text-blue-600 font-mono">T-{ticket.id}</span>
            <span className="truncate">{ticket.title}</span>
          </h2>

          {/* Progress Section */}
          {renderProgressBar()}
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col min-h-0">
          <div className="px-6 border-b border-slate-100 bg-slate-50/50">
            <TabsList className="bg-transparent border-0 h-10 p-0 gap-4">
              <TabsTrigger
                value="overview"
                className="data-[state=active]:bg-transparent data-[state=active]:shadow-none data-[state=active]:border-b-2 data-[state=active]:border-blue-600 rounded-none h-full text-xs font-semibold px-1"
              >
                Overview
              </TabsTrigger>
              <TabsTrigger
                value="plan"
                disabled={!planMarkdown}
                className="data-[state=active]:bg-transparent data-[state=active]:shadow-none data-[state=active]:border-b-2 data-[state=active]:border-blue-600 rounded-none h-full text-xs font-semibold px-1"
              >
                Plan
              </TabsTrigger>
              <TabsTrigger
                value="diffs"
                disabled={!run || !run.diffs || run.diffs.length === 0}
                className="data-[state=active]:bg-transparent data-[state=active]:shadow-none data-[state=active]:border-b-2 data-[state=active]:border-blue-600 rounded-none h-full text-xs font-semibold px-1"
              >
                Code Changes ({run?.diffs?.length || 0})
              </TabsTrigger>
              <TabsTrigger
                value="timeline"
                disabled={activityEvents.length === 0}
                className="data-[state=active]:bg-transparent data-[state=active]:shadow-none data-[state=active]:border-b-2 data-[state=active]:border-blue-600 rounded-none h-full text-xs font-semibold px-1"
              >
                Timeline
              </TabsTrigger>
            </TabsList>
          </div>

          <div className="flex-1 overflow-y-auto p-6 space-y-6 min-h-0">
            <TabsContent value="overview" className="m-0 space-y-6">
              {/* Clarification questions panel */}
              {run && run.status === 'awaiting_clarification' && (run.clarification_questions?.length ?? 0) > 0 && (
                <Card className="border-amber-200 shadow-xs overflow-hidden">
                  <ClarificationPanel
                    questions={run.clarification_questions!}
                    isBusy={isBusy}
                    onSubmit={async (answers) => {
                      setActionError('');
                      try {
                        await onClarifyRun(answers);
                      } catch (err: any) {
                        setActionError(err.message || 'Failed to submit answers');
                      }
                    }}
                    onSkip={() => {
                      // Submit with empty answers to let planner decide
                      setActionError('');
                      onClarifyRun([]).catch((err: any) =>
                        setActionError(err.message || 'Failed to skip')
                      );
                    }}
                  />
                </Card>
              )}

              {/* Failure summary if failed */}
              {(ticket.status === 'failed' || (run && (run.status === 'failed' || run.status === 'rejected'))) && (
                <Card className="bg-rose-50 border-rose-200/60 shadow-xs">
                  <CardContent className="p-4 flex gap-3">
                    <AlertTriangle className="text-rose-500 flex-shrink-0 mt-0.5" size={18} />
                    <div className="space-y-1.5 min-w-0 flex-1">
                      <h4 className="text-xs font-bold text-rose-900">Task Run Failure</h4>
                      {run?.error && (
                        <p className="text-xs text-rose-800 leading-normal font-medium max-h-16 overflow-y-auto pr-1">
                          {run.error}
                        </p>
                      )}

                      {/* Verifier blockers */}
                      {verifierBlockers.length > 0 && (
                        <div className="space-y-1 mt-2">
                          <p className="text-[10px] font-bold text-rose-900 tracking-wide uppercase">Verifier Blockers ({verifierBlockers.length}):</p>
                          <ul className="list-disc pl-4 text-xs text-rose-800 space-y-0.5">
                            {verifierBlockers.map((blocker, idx) => (
                              <li key={idx} className="leading-normal font-medium">{blocker}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Description */}
              <div className="space-y-1.5">
                <h3 className="text-xs font-bold text-slate-400 tracking-wider uppercase">Description</h3>
                <p className="text-xs text-slate-600 leading-relaxed font-medium bg-slate-50/50 p-3 rounded-lg border border-slate-100">
                  {ticket.description || 'No description provided.'}
                </p>
              </div>

              {/* Acceptance Criteria Checklist */}
              <div className="space-y-1.5">
                <h3 className="text-xs font-bold text-slate-400 tracking-wider uppercase">Acceptance Criteria</h3>
                <div className="space-y-2 bg-slate-50/50 p-3 rounded-lg border border-slate-100">
                  {criteriaTexts.length === 0 ? (
                    <p className="text-xs text-slate-400 italic">
                      Acceptance criteria will be generated during planning.
                    </p>
                  ) : (
                    criteriaTexts.map((text, idx) => (
                      <div key={idx} className="flex items-start gap-2.5">
                        <Checkbox
                          id={`criteria-${idx}`}
                          checked={isCompleted}
                          disabled
                          className="mt-0.5 text-blue-600 border-slate-300"
                        />
                        <label
                          htmlFor={`criteria-${idx}`}
                          className={`text-xs leading-normal font-medium ${
                            isCompleted ? 'text-green-600 font-semibold' : 'text-slate-600'
                          }`}
                        >
                          {text}
                        </label>
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* Live Terminal Log View */}
              <ActivityLogSection
                events={activityEvents}
                currentAction={currentAction || run?.current_action}
                tokenUsage={tokenUsage?.total_tokens > 0 ? tokenUsage : (run?.token_usage || { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 })}
                isRunning={run?.is_running || false}
                hasRun={!!run}
                contextBundle={run?.context_bundle}
                onClear={onClearActivityLogs}
                onStop={run?.is_running ? onStopWatchingRun : undefined}
              />

              {/* Executions Attempts Section */}
              {executions.length > 0 && (
                <ExecutionsSection
                  executions={executions}
                  currentRunId={run?.run_id || null}
                />
              )}

              {/* Affected Files List */}
              <div className="space-y-1.5">
                <h3 className="text-xs font-bold text-slate-400 tracking-wider uppercase">Files Affected</h3>
                <div className="bg-slate-50/50 p-3 rounded-lg border border-slate-100">
                  {renderFilesList()}
                </div>
              </div>
            </TabsContent>

            <TabsContent value="plan" className="m-0">
              {planMarkdown ? (
                <article className="prose prose-sm prose-slate max-w-none select-text
                  prose-headings:font-bold prose-headings:text-slate-800
                  prose-h1:text-base prose-h2:text-sm prose-h3:text-xs
                  prose-p:text-xs prose-p:text-slate-600 prose-p:leading-relaxed
                  prose-li:text-xs prose-li:text-slate-600
                  prose-code:text-[11px] prose-code:bg-slate-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-slate-700 prose-code:font-mono prose-code:before:content-none prose-code:after:content-none
                  prose-pre:bg-slate-950 prose-pre:text-slate-100 prose-pre:text-[11px] prose-pre:rounded-lg prose-pre:overflow-x-auto
                  prose-table:text-xs prose-th:text-xs prose-th:font-semibold prose-th:text-slate-700 prose-td:text-xs prose-td:text-slate-600
                  prose-a:text-blue-600 prose-a:no-underline hover:prose-a:underline
                  prose-blockquote:border-blue-300 prose-blockquote:text-slate-500 prose-blockquote:text-xs
                  prose-strong:text-slate-800 prose-strong:font-semibold
                ">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {planMarkdown}
                  </ReactMarkdown>
                </article>
              ) : (
                <p className="text-xs text-slate-400 italic">No plan document available.</p>
              )}
            </TabsContent>

            <TabsContent value="diffs" className="m-0 space-y-4">
              {run?.diffs && run.diffs.map((diffItem, index) => (
                <Card key={index} className="border-slate-200 overflow-hidden bg-slate-950 font-mono text-[11px] shadow-sm">
                  {/* Diff file header */}
                  <div className="px-4 py-2 bg-slate-900 border-b border-slate-800 text-slate-300 flex items-center justify-between font-sans">
                    <span className="font-semibold text-xs flex items-center gap-1.5">
                      <FileCode size={13} className="text-blue-400" />
                      {diffItem.path}
                    </span>
                  </div>
                  {/* Scrollable diff lines */}
                  <CardContent className="p-3 overflow-x-auto text-left leading-normal select-text">
                    <pre className="text-slate-100 leading-5">
                      {diffItem.diff.split('\n').map((line: string, i: number) => {
                        let lineClass = 'text-slate-400';
                        if (line.startsWith('+') && !line.startsWith('+++')) {
                          lineClass = 'text-emerald-400 bg-emerald-950/20 font-medium border-l-2 border-emerald-500 pl-1 -ml-1';
                        } else if (line.startsWith('-') && !line.startsWith('---')) {
                          lineClass = 'text-rose-400 bg-rose-950/20 font-medium border-l-2 border-rose-500 pl-1 -ml-1';
                        } else if (line.startsWith('@@')) {
                          lineClass = 'text-indigo-400 font-bold';
                        }
                        return (
                          <div key={i} className={`${lineClass} whitespace-pre`}>
                            {line}
                          </div>
                        );
                      })}
                    </pre>
                  </CardContent>
                </Card>
              ))}
            </TabsContent>

            <TabsContent value="timeline" className="m-0">
              <RunTimeline
                events={activityEvents}
                runStatus={run?.status ?? ticket.status}
                mergedAt={run?.merge_report?.applied ? run.merge_report.merged_at : undefined}
              />
            </TabsContent>
          </div>
        </Tabs>
      </div>

      {/* Action Errors */}
      {actionError && (
        <div className="mx-6 mb-2 text-xs font-semibold text-rose-500 bg-rose-50 border border-rose-200 p-2 rounded-lg flex gap-2">
          <AlertCircle size={14} className="flex-shrink-0 mt-0.5" />
          <span>{actionError}</span>
        </div>
      )}

      {/* Bottom control actions footer */}
      <div className="p-4 border-t border-slate-100 bg-slate-50/30 flex items-center justify-between gap-3">
        {/* Restart/Revert Button */}
        {run && (
          <Button
            onClick={() => setIsRevertOpen(true)}
            variant="outline"
            disabled={isBusy || reverting || merging}
            className="border-slate-200 hover:bg-rose-50 hover:text-rose-600 hover:border-rose-200 text-xs font-semibold h-10 px-4 flex items-center gap-1.5 flex-shrink-0"
          >
            <RotateCcw size={14} />
            <span>Restart</span>
          </Button>
        )}

        {/* Human-in-the-loop: Reject Action */}
        {run && run.status === 'awaiting_approval' && (
          <Button
            onClick={() => setIsRejectOpen(true)}
            variant="outline"
            disabled={isBusy}
            className="border-rose-200 bg-rose-50/50 hover:bg-rose-50 hover:text-rose-600 text-rose-500 text-xs font-semibold h-10 px-4 flex items-center gap-1.5"
          >
            <XCircle size={14} />
            <span>Reject Plan</span>
          </Button>
        )}

        {/* Primary Executor Action Button */}
        <div className="flex-1 flex gap-2 justify-end">
          {/* Merge to base branch */}
          {run && run.status === 'completed' && run.worktree_path && !run.merge_report?.applied && (
            <Button
              onClick={() => setIsMergeOpen(true)}
              disabled={isBusy || merging}
              className="bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs h-10 px-4 flex items-center gap-1.5 shadow-sm"
            >
              <GitBranch size={14} />
              <span>Merge to Base</span>
            </Button>
          )}

          <Button
            onClick={executePrimaryAction}
            disabled={isBusy || reverting || merging || !!run?.is_running}
            className={`min-w-[120px] text-white font-semibold text-xs h-10 px-4 flex items-center justify-center gap-1.5 shadow-sm ${
              run?.status === 'awaiting_approval'
                ? 'bg-indigo-600 hover:bg-indigo-500'
                : 'bg-blue-600 hover:bg-blue-500'
            }`}
          >
            {!(isBusy || reverting || merging) && (run?.status === 'awaiting_approval' ? <CheckCircle size={14} /> : <Play size={12} />)}
            <span>{getPrimaryActionButtonLabel()}</span>
          </Button>
        </div>
      </div>

      {/* dialogs */}
      {/* Workspace approval modal */}
      <Dialog open={isApproveOpen} onOpenChange={setIsApproveOpen}>
        <DialogContent className="sm:max-w-[480px] bg-slate-900 border-slate-800 text-slate-100">
          <DialogHeader>
            <DialogTitle className="text-white text-base">Plan Approved: Select Workspace Mode</DialogTitle>
            <DialogDescription className="text-slate-400 text-xs">
              Choose how the developer agent should implement the code modifications in your target project.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3.5 py-4">
            {/* Worktree mode (recommended) */}
            <button
              type="button"
              onClick={() => handleApproveSubmit('worktree')}
              className="w-full text-left p-4 rounded-xl border-2 border-blue-500 bg-blue-950/20 hover:bg-blue-950/30 transition-all flex items-start gap-4 ring-1 ring-blue-500/10 cursor-pointer"
            >
              <div className="bg-blue-500/20 p-2.5 rounded-lg text-blue-400 flex-shrink-0">
                <GitBranch size={20} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <h4 className="text-xs font-bold text-white leading-normal">Create Work-Tree</h4>
                  <span className="bg-blue-600 text-white font-bold text-[8px] tracking-wide px-1.5 py-0.5 rounded uppercase">Recommended</span>
                </div>
                <p className="text-[10px] text-slate-400 mt-1 leading-normal">
                  Runs the agent in an isolated Git worktree. Keeps your working checkout clean, prevents lock conflicts, and allows parallel tasks.
                </p>
              </div>
            </button>

            {/* In Place mode */}
            <button
              type="button"
              onClick={() => handleApproveSubmit('in_place')}
              className="w-full text-left p-4 rounded-xl border border-slate-800 bg-slate-950/40 hover:bg-slate-950/60 transition-all flex items-start gap-4 cursor-pointer"
            >
              <div className="bg-slate-800 p-2.5 rounded-lg text-slate-400 flex-shrink-0">
                <FileCode size={20} />
              </div>
              <div className="min-w-0 flex-1">
                <h4 className="text-xs font-bold text-slate-200 leading-normal">Edit Current Codebase</h4>
                <p className="text-[10px] text-slate-400 mt-1 leading-normal">
                  Applies changes directly in-place to your active branch. Best for rapid local edits without worktree environment setup.
                </p>
              </div>
            </button>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setIsApproveOpen(false)}
              className="border-slate-800 hover:bg-slate-800 text-slate-300 text-xs"
            >
              Cancel
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reject FeedBack Modal */}
      <Dialog open={isRejectOpen} onOpenChange={setIsRejectOpen}>
        <DialogContent className="sm:max-w-[450px] bg-slate-900 border-slate-800 text-slate-100">
          <DialogHeader>
            <DialogTitle className="text-white text-base">Reject Implementation Plan</DialogTitle>
            <DialogDescription className="text-slate-400 text-xs">
              Provide specific feedback or modification instructions to guide the planner to redesign the plan.
            </DialogDescription>
          </DialogHeader>
          <div className="py-3">
            <Textarea
              placeholder="e.g. Please avoid modifying utility classes, use the existing helpers in services layer instead..."
              value={rejectFeedback}
              onChange={(e) => setRejectFeedback(e.target.value)}
              className="bg-slate-950 border-slate-800 text-slate-100 placeholder:text-slate-600 focus-visible:ring-blue-500 text-xs min-h-[100px]"
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setIsRejectOpen(false)}
              className="border-slate-800 hover:bg-slate-800 text-slate-300 text-xs"
            >
              Cancel
            </Button>
            <Button
              onClick={handleRejectSubmit}
              className="bg-rose-600 hover:bg-rose-500 text-white font-semibold text-xs"
            >
              Reject Plan
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Revert Warning Modal */}
      <Dialog open={isRevertOpen} onOpenChange={setIsRevertOpen}>
        <DialogContent className="sm:max-w-[400px] bg-slate-900 border-slate-800 text-slate-100">
          <DialogHeader>
            <DialogTitle className="text-white text-base">Revert and Restart Task?</DialogTitle>
            <DialogDescription className="text-slate-400 text-xs leading-normal">
              This will discard all AI-generated code changes, delete the active workspace/branch, and completely restart the agent pipeline from scratch.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              onClick={() => setIsRevertOpen(false)}
              className="border-slate-800 hover:bg-slate-800 text-slate-300 text-xs"
            >
              Cancel
            </Button>
            <Button
              onClick={handleRevertSubmit}
              className="bg-rose-600 hover:bg-rose-500 text-white font-semibold text-xs"
            >
              Revert & Restart
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Merge to base Modal */}
      <Dialog open={isMergeOpen} onOpenChange={setIsMergeOpen}>
        <DialogContent className="sm:max-w-[400px] bg-slate-900 border-slate-800 text-slate-100">
          <DialogHeader>
            <DialogTitle className="text-white text-base">Merge Changes into Base Branch?</DialogTitle>
            <DialogDescription className="text-slate-400 text-xs leading-normal">
              Merge the developer agent worktree branch back into your project&apos;s base branch.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              onClick={() => setIsMergeOpen(false)}
              className="border-slate-800 hover:bg-slate-800 text-slate-300 text-xs"
            >
              Cancel
            </Button>
            <Button
              onClick={handleMergeSubmit}
              className="bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs"
            >
              Merge Branch
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
