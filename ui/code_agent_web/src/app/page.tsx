'use client';

import React, { useState, useEffect, useRef } from 'react';
import { 
  ProjectSummary, 
  TicketSummary, 
  CodeAgentRunStatus, 
  CodeAgentExecution, 
  AgentActivityEvent, 
  TokenUsage, 
  JiraConfig, 
  JiraStatusCheck,
  AgentsMdStatus,
  ProjectContextConfig
} from '@/lib/models';
import { CodeAgentApiClient } from '@/lib/api-client';
import Sidebar from '@/components/Sidebar';
import TicketBoard from '@/components/TicketBoard';
import TicketChildrenDrawer from '@/components/TicketChildrenDrawer';
import CenterPane from '@/components/CenterPane';
import EpicRunPanel from '@/components/EpicRunPanel';
import SettingsScreen from '@/components/SettingsScreen';
import AgentLogsView from '@/components/AgentLogsView';
import SimulatorView from '@/components/SimulatorView';
import { classifyIssueType } from '@/lib/jira-hierarchy';
import { Toaster, toast } from 'sonner';

export default function Home() {
  // Navigation & Project Select States
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [activeWorkflow, setActiveWorkflow] = useState<string>('All Tickets');
  const [isAddingProject, setIsAddingProject] = useState(false);

  // Tickets States
  const [tickets, setTickets] = useState<TicketSummary[]>([]);
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null);
  const [isLoadingTickets, setIsLoadingTickets] = useState(false);
  const [childrenDrawerParentId, setChildrenDrawerParentId] = useState<number | null>(null);

  // Run & Execution details
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<CodeAgentRunStatus | null>(null);
  const [executions, setExecutions] = useState<CodeAgentExecution[]>([]);
  const [activityEvents, setActivityEvents] = useState<AgentActivityEvent[]>([]);
  const [lastSeq, setLastSeq] = useState(0);
  const [currentAction, setCurrentAction] = useState<AgentActivityEvent | null>(null);
  const [tokenUsage, setTokenUsage] = useState<TokenUsage>({ prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cached_tokens: 0 });

  // Settings states
  const [apiBaseUrl, setApiBaseUrl] = useState('http://127.0.0.1:8000');
  const [jiraStatus, setJiraStatus] = useState<JiraStatusCheck | null>(null);
  const [jiraConfig, setJiraConfig] = useState<JiraConfig | null>(null);
  const [agentsMdStatus, setAgentsMdStatus] = useState<AgentsMdStatus | null>(null);
  const [projectContext, setProjectContext] = useState<ProjectContextConfig | null>(null);
  const [isGeneratingAgentsMd, setIsGeneratingAgentsMd] = useState(false);
  const [isGeneratingContext, setIsGeneratingContext] = useState(false);
  const [isSavingContext, setIsSavingContext] = useState(false);
  const [isSyncingJira, setIsSyncingJira] = useState(false);

  // Global loading/busy indicator
  const [isBusy, setIsBusy] = useState(false);
  const [isRunLoading, setIsRunLoading] = useState(false);
  // Bumped to force the EpicRunPanel to reload after starting an epic from the queue.
  const [epicReloadNonce, setEpicReloadNonce] = useState(0);

  // Polling ref
  const pollingIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const selectedTicketIdRef = useRef<number | null>(null);

  // Sync refs to avoid stale closures in polling loops
  useEffect(() => {
    selectedTicketIdRef.current = selectedTicketId;
  }, [selectedTicketId]);

  // Load preferences from localStorage on mount
  useEffect(() => {
    const storedUrl = CodeAgentApiClient.getBaseUrl();
    setApiBaseUrl(storedUrl);

    const loadInitialData = async () => {
      try {
        const loadedProjects = await CodeAgentApiClient.listProjects();
        setProjects(loadedProjects);

        const savedProjIdStr = localStorage.getItem('code_agent_selected_project_id');
        if (savedProjIdStr) {
          const projId = Number(savedProjIdStr);
          if (loadedProjects.some(p => p.id === projId)) {
            setSelectedProjectId(projId);
            loadTicketsForProject(projId);
          }
        }

        const savedTicketIdStr = localStorage.getItem('code_agent_selected_ticket_id');
        if (savedTicketIdStr) {
          const tId = Number(savedTicketIdStr);
          setSelectedTicketId(tId);
        }

        // Fetch global Jira credentials config status
        const status = await CodeAgentApiClient.getJiraStatus();
        setJiraStatus(status);
      } catch (err: any) {
        toast.error('Failed to load initial data. Ensure the backend API is running.');
      }
    };

    loadInitialData();
  }, []);

  // Polling loop controller
  useEffect(() => {
    const run = runStatus;
    const isPollingActive = run && !['completed', 'failed', 'rejected'].includes(run.status) && run.status !== 'awaiting_approval';

    if (activeRunId && isPollingActive) {
      startPolling(activeRunId);
    } else {
      stopPolling();
    }

    return () => {
      stopPolling();
    };
  }, [activeRunId, runStatus]);

  // Board polling: keep the kanban columns live whenever ANY ticket is in an
  // active status (planning → qa). The single-run polling loop above only fires
  // for a selected run, so epic children — which advance in the background —
  // never refreshed the board without this. Depends on a derived boolean so it
  // only (re)arms when activity starts/stops, not on every poll.
  const hasActiveTickets = tickets.some((t) =>
    ['planning', 'awaiting_approval', 'developing', 'verifying', 'qa'].includes(t.status)
  );
  useEffect(() => {
    if (!(selectedProjectId && hasActiveTickets)) return;
    const id = setInterval(async () => {
      try {
        setTickets(await CodeAgentApiClient.listTickets(selectedProjectId));
      } catch (e) {
        console.error('Board ticket poll error:', e);
      }
    }, 3000);
    return () => clearInterval(id);
  }, [hasActiveTickets, selectedProjectId]);

  const startPolling = (runId: string) => {
    if (pollingIntervalRef.current) return;

    pollingIntervalRef.current = setInterval(async () => {
      try {
        // Poll status
        const status = await CodeAgentApiClient.getRun(runId);
        setRunStatus(status);

        // Fetch execution attempts
        const execs = await CodeAgentApiClient.listExecutions(runId);
        setExecutions(execs);

        // Poll incremental activity logs
        const activity = await CodeAgentApiClient.fetchActivity(runId, lastSeq);
        if (activity.events && activity.events.length > 0) {
          setActivityEvents((prev) => {
            const merged = [...prev, ...activity.events];
            // Remove duplicates based on sequence identifier
            const seen = new Set();
            return merged.filter((item) => {
              if (seen.has(item.seq)) return false;
              seen.add(item.seq);
              return true;
            });
          });

          const maxSeq = Math.max(...activity.events.map(e => e.seq));
          if (maxSeq > lastSeq) {
            setLastSeq(maxSeq);
          }
        }

        if (activity.current_action) {
          setCurrentAction(activity.current_action);
        }

        if (activity.token_usage) {
          setTokenUsage(activity.token_usage);
        }

        // If the ticket status in backend changed, reload tickets to update badge in SidePanel
        if (selectedProjectId) {
          const freshTickets = await CodeAgentApiClient.listTickets(selectedProjectId);
          setTickets(freshTickets);
        }
      } catch (error) {
        console.error('Polling error:', error);
      }
    }, 2000);
  };

  const stopPolling = () => {
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
    }
  };

  // Action methods
  const loadTicketsForProject = async (projId: number) => {
    setIsLoadingTickets(true);
    try {
      const list = await CodeAgentApiClient.listTickets(projId);
      setTickets(list);
    } catch (err: any) {
      toast.error('Failed to load tickets for selected project');
    } finally {
      setIsLoadingTickets(false);
    }
  };

  const handleSelectProject = (id: number) => {
    setSelectedProjectId(id);
    localStorage.setItem('code_agent_selected_project_id', String(id));
    setSelectedTicketId(null);
    localStorage.removeItem('code_agent_selected_ticket_id');
    setRunStatus(null);
    setActiveRunId(null);
    setExecutions([]);
    setActivityEvents([]);
    setLastSeq(0);
    setCurrentAction(null);
    setTokenUsage({ prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cached_tokens: 0 });
    
    // Trigger loads
    loadTicketsForProject(id);
    handleLoadJiraConfig(id);
    handleLoadAgentsMdStatus(id);
    handleLoadProjectContext(id);
  };

  const handleSelectTicket = async (id: number) => {
    setSelectedTicketId(id);
    localStorage.setItem('code_agent_selected_ticket_id', String(id));
    setRunStatus(null);
    setActiveRunId(null);
    setExecutions([]);
    setActivityEvents([]);
    setLastSeq(0);
    setCurrentAction(null);
    setTokenUsage({ prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cached_tokens: 0 });

    const ticket = tickets.find(t => t.id === id);
    if (ticket && ticket.run_id) {
      setIsRunLoading(true);
      setActiveRunId(ticket.run_id);
      try {
        const runData = await CodeAgentApiClient.getRun(ticket.run_id);
        setRunStatus(runData);

        const execs = await CodeAgentApiClient.listExecutions(ticket.run_id);
        setExecutions(execs);

        const activity = await CodeAgentApiClient.fetchActivity(ticket.run_id, 0);
        setActivityEvents(activity.events || []);
        if (activity.events && activity.events.length > 0) {
          setLastSeq(Math.max(...activity.events.map(e => e.seq)));
        }
        setCurrentAction(activity.current_action || null);
        setTokenUsage(activity.token_usage || { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cached_tokens: 0 });
      } catch (err: any) {
        console.error('Error fetching ticket run data:', err);
      } finally {
        setIsRunLoading(false);
      }
    }
  };

  const handleSelectWorkflow = (wf: string) => {
    setActiveWorkflow(wf);
  };

  const handleAddProject = async (path: string, name?: string) => {
    setIsAddingProject(true);
    try {
      const newProj = await CodeAgentApiClient.createProject(path, name);
      setProjects(prev => [...prev, newProj]);
      handleSelectProject(newProj.id);
      toast.success(`Project "${newProj.name}" added successfully`);
    } catch (err: any) {
      throw new Error(err.message || 'Failed to add project');
    } finally {
      setIsAddingProject(false);
    }
  };

  const handleRemoveProject = async (projectId: number) => {
    await CodeAgentApiClient.deleteProject(projectId);
    setProjects(prev => prev.filter(p => p.id !== projectId));

    if (selectedProjectId === projectId) {
      setSelectedProjectId(null);
      localStorage.removeItem('code_agent_selected_project_id');
      setSelectedTicketId(null);
      localStorage.removeItem('code_agent_selected_ticket_id');
      setTickets([]);
      setRunStatus(null);
      setActiveRunId(null);
      setExecutions([]);
      setActivityEvents([]);
      setLastSeq(0);
      setCurrentAction(null);
      setTokenUsage({ prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cached_tokens: 0 });
      setJiraConfig(null);
      setAgentsMdStatus(null);
      setProjectContext(null);
    }

    toast.success('Project removed');
  };

  const handleCreateTicket = async (title: string, type: string, description?: string) => {
    if (!selectedProjectId) return;
    try {
      await CodeAgentApiClient.createTicket(selectedProjectId, title, type, description);
      await loadTicketsForProject(selectedProjectId);
      toast.success('Ticket created successfully');
    } catch (err: any) {
      throw new Error(err.message || 'Failed to create ticket');
    }
  };

  const handleDeleteTicket = async (id: number) => {
    try {
      await CodeAgentApiClient.deleteTicket(id);
      if (selectedTicketId === id) {
        setSelectedTicketId(null);
        localStorage.removeItem('code_agent_selected_ticket_id');
        setRunStatus(null);
        setActiveRunId(null);
      }
      if (selectedProjectId) {
        await loadTicketsForProject(selectedProjectId);
      }
      toast.success('Ticket deleted');
    } catch (err: any) {
      throw new Error(err.message || 'Failed to delete ticket');
    }
  };

  const handleSyncJiraTickets = async (projectId: number) => {
    setIsSyncingJira(true);
    try {
      const res = await CodeAgentApiClient.syncJiraTickets(projectId);
      await loadTicketsForProject(projectId);
      toast.success(`Jira Sync complete. Synced ${res.total} tickets (${res.created} created, ${res.updated} updated).`);
    } catch (err: any) {
      toast.error(err.message || 'Jira sync failed');
    } finally {
      setIsSyncingJira(false);
    }
  };

  const handleRunEpic = async (ticketId: number) => {
    try {
      await CodeAgentApiClient.startEpicRun(ticketId);
    } catch (err: any) {
      toast.error(err.message || 'Failed to start epic run');
      return;
    }
    if (selectedTicketId !== ticketId) {
      await handleSelectTicket(ticketId);
    }
    setEpicReloadNonce((n) => n + 1);
    toast.success('Epic planning started');
  };

  const handleStartRun = async (ticketId: number, autoApprove?: boolean) => {
    const project = projects.find(p => p.id === selectedProjectId);
    const ticket = tickets.find(t => t.id === ticketId);
    if (!project || !ticket) return;

    setIsBusy(true);
    setActivityEvents([]);
    setLastSeq(0);
    setCurrentAction(null);
    setTokenUsage({ prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cached_tokens: 0 });

    try {
      const runId = await CodeAgentApiClient.startRun(
        ticket.description || ticket.title,
        project.path,
        ticket.id,
        autoApprove
      );
      setActiveRunId(runId);
      
      // Reload tickets to fetch ticket status updates
      await loadTicketsForProject(project.id);
      
      // Fetch fresh run status
      const runData = await CodeAgentApiClient.getRun(runId);
      setRunStatus(runData);

      toast.success('Agent task run started');
    } catch (err: any) {
      throw new Error(err.message || 'Failed to execute task');
    } finally {
      setIsBusy(false);
    }
  };

  const handleApproveRun = async (workspaceMode: 'worktree' | 'in_place') => {
    if (!activeRunId) return;
    setIsBusy(true);
    try {
      const runData = await CodeAgentApiClient.approveRun(activeRunId, workspaceMode);
      setRunStatus(runData);
      toast.success('Plan approved. Continuing execution.');
    } catch (err: any) {
      throw new Error(err.message || 'Approve action failed');
    } finally {
      setIsBusy(false);
    }
  };

  const handleRejectRun = async (feedback?: string) => {
    if (!activeRunId) return;
    setIsBusy(true);
    try {
      const runData = await CodeAgentApiClient.rejectRun(activeRunId, feedback);
      setRunStatus(runData);
      toast.success('Plan rejected. Agent planning state updated.');
    } catch (err: any) {
      throw new Error(err.message || 'Reject action failed');
    } finally {
      setIsBusy(false);
    }
  };

  const handleRetryRun = async () => {
    if (!activeRunId) return;
    setIsBusy(true);
    try {
      const runData = await CodeAgentApiClient.retryRun(activeRunId);
      setRunStatus(runData);
      toast.success('Task execution retried');
    } catch (err: any) {
      throw new Error(err.message || 'Retry action failed');
    } finally {
      setIsBusy(false);
    }
  };

  const handleRevertRun = async () => {
    if (!activeRunId) return;
    try {
      const runData = await CodeAgentApiClient.revertRun(activeRunId);
      setRunStatus(runData);
      setExecutions([]);
      setActivityEvents([]);
      setLastSeq(0);
      setCurrentAction(null);
      setTokenUsage({ prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cached_tokens: 0 });
      
      if (selectedProjectId) {
        await loadTicketsForProject(selectedProjectId);
      }
      toast.success('Workspace reverted successfully');
    } catch (err: any) {
      throw new Error(err.message || 'Revert action failed');
    }
  };

  const handleResumeRun = async () => {
    if (!activeRunId) return;
    setIsBusy(true);
    try {
      const runData = await CodeAgentApiClient.resumeRun(activeRunId);
      setRunStatus(runData);
      toast.success('Task execution resumed');
    } catch (err: any) {
      throw new Error(err.message || 'Resume action failed');
    } finally {
      setIsBusy(false);
    }
  };

  const handleClarifyRun = async (answers: import('@/lib/models').ClarifyAnswer[]) => {
    if (!activeRunId) return;
    setIsBusy(true);
    try {
      const runData = await CodeAgentApiClient.clarifyRun(activeRunId, answers);
      setRunStatus(runData);
      if (answers.length === 0) {
        toast.success('Skipped — planner will decide');
      } else {
        toast.success('Answers submitted — generating final plan…');
      }
    } catch (err: any) {
      throw new Error(err.message || 'Failed to submit clarification answers');
    } finally {
      setIsBusy(false);
    }
  };

  const handleMergeRun = async () => {
    if (!activeRunId) return;
    try {
      const res = await CodeAgentApiClient.mergeRun(activeRunId);
      if (res.applied) {
        toast.success(`Merged successfully into branch: ${res.target_branch}`);
        // Fetch fresh run status
        const runData = await CodeAgentApiClient.getRun(activeRunId);
        setRunStatus(runData);
      } else {
        toast.error(`Merge conflict in files: ${res.conflict_files?.join(', ') || 'unknown'}`);
      }
    } catch (err: any) {
      throw new Error(err.message || 'Merge action failed');
    }
  };

  const handleLoadJiraConfig = async (projectId: number) => {
    try {
      const config = await CodeAgentApiClient.getJiraConfig(projectId);
      setJiraConfig(config);
    } catch (err) {
      setJiraConfig(null);
    }
  };

  const handleUpdateJiraConfig = async (projectId: number, jql?: string, mapping?: Record<string, string>) => {
    try {
      const config = await CodeAgentApiClient.updateJiraConfig(projectId, jql, mapping);
      setJiraConfig(config);
    } catch (err: any) {
      throw new Error(err.message || 'Failed to update Jira config');
    }
  };

  const handleLoadAgentsMdStatus = async (projectId: number) => {
    try {
      const status = await CodeAgentApiClient.getAgentsMdStatus(projectId);
      setAgentsMdStatus(status);
    } catch (err) {
      setAgentsMdStatus(null);
    }
  };

  const handleGenerateAgentsMd = async (projectId: number, hints?: string) => {
    setIsGeneratingAgentsMd(true);
    try {
      const status = await CodeAgentApiClient.generateAgentsMd(projectId, hints);
      setAgentsMdStatus(status);
    } catch (err: any) {
      throw new Error(err.message || 'Failed to generate AGENTS.md');
    } finally {
      setIsGeneratingAgentsMd(false);
    }
  };

  const handleLoadProjectContext = async (projectId: number) => {
    try {
      const ctx = await CodeAgentApiClient.getProjectContext(projectId);
      setProjectContext(ctx);
    } catch (err) {
      setProjectContext(null);
    }
  };

  const handleSaveProjectContext = async (projectId: number, text: string, pSkills: string[], dSkills: string[]) => {
    setIsSavingContext(true);
    try {
      const ctx = await CodeAgentApiClient.updateProjectContext(projectId, text, pSkills, dSkills);
      setProjectContext(ctx);
    } catch (err: any) {
      throw new Error(err.message || 'Failed to save context text');
    } finally {
      setIsSavingContext(false);
    }
  };

  const handleGenerateProjectContext = async (projectId: number, hints?: string) => {
    setIsGeneratingContext(true);
    try {
      const generated = await CodeAgentApiClient.generateProjectContext(projectId, hints);
      return generated;
    } catch (err: any) {
      throw new Error(err.message || 'Failed to generate context text');
    } finally {
      setIsGeneratingContext(false);
    }
  };

  const handleClearActivityLogs = () => {
    setActivityEvents([]);
  };

  const handleStopWatchingRun = () => {
    stopPolling();
    // Simulate updating VM state watch flags
    if (runStatus) {
      setRunStatus({ ...runStatus, is_running: false });
    }
  };

  const handleSelectRun = async (runId: string) => {
    setIsRunLoading(true);
    setActiveRunId(runId);
    try {
      const runData = await CodeAgentApiClient.getRun(runId);
      setRunStatus(runData);

      const execs = await CodeAgentApiClient.listExecutions(runId);
      setExecutions(execs);

      const activity = await CodeAgentApiClient.fetchActivity(runId, 0);
      setActivityEvents(activity.events || []);
      if (activity.events && activity.events.length > 0) {
        setLastSeq(Math.max(...activity.events.map(e => e.seq)));
      }
      setCurrentAction(activity.current_action || null);
      setTokenUsage(activity.token_usage || { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cached_tokens: 0 });
    } catch (err) {
      console.error(err);
    } finally {
      setIsRunLoading(false);
    }
  };

  const handleSaveBaseUrl = (url: string) => {
    CodeAgentApiClient.saveBaseUrl(url);
    setApiBaseUrl(url);
  };

  const selectedProject = projects.find(p => p.id === selectedProjectId) || null;
  const selectedTicket = tickets.find(t => t.id === selectedTicketId) || null;
  const selectedIsEpic = selectedTicket ? classifyIssueType(selectedTicket) === 'epic' : false;
  const childrenDrawerParent = tickets.find(t => t.id === childrenDrawerParentId) || null;

  const handleSelectTicketFromDrawer = (id: number) => {
    handleSelectTicket(id);
    setChildrenDrawerParentId(null);
  };

  return (
    <div className="flex h-screen bg-slate-100 overflow-hidden font-sans">
      <Toaster position="top-right" theme="dark" />

      {/* Slide-in panel: child tickets of a tapped Epic/Story */}
      <TicketChildrenDrawer
        parent={childrenDrawerParent}
        tickets={tickets}
        selectedTicketId={selectedTicketId}
        onSelectTicket={handleSelectTicketFromDrawer}
        onClose={() => setChildrenDrawerParentId(null)}
      />

      
      {/* Column 1: Sidebar Nav (Slate-950) */}
      <Sidebar
        projects={projects}
        selectedProjectId={selectedProjectId}
        activeWorkflow={activeWorkflow}
        onSelectProject={handleSelectProject}
        onSelectWorkflow={handleSelectWorkflow}
        onAddProject={handleAddProject}
        onRemoveProject={handleRemoveProject}
        isAddingProject={isAddingProject}
      />

      {/* Main Workspace Frame */}
      <div className="flex-1 flex overflow-hidden min-w-0">
        {activeWorkflow === 'Settings' ? (
          /* Settings Fullscreen Screen */
          <SettingsScreen
            selectedProject={selectedProject}
            apiBaseUrl={apiBaseUrl}
            jiraStatus={jiraStatus}
            jiraConfig={jiraConfig}
            agentsMdStatus={agentsMdStatus}
            projectContext={projectContext}
            isGeneratingAgentsMd={isGeneratingAgentsMd}
            isGeneratingContext={isGeneratingContext}
            isSavingContext={isSavingContext}
            isSyncingJira={isSyncingJira}
            onSaveBaseUrl={handleSaveBaseUrl}
            onLoadJiraConfig={handleLoadJiraConfig}
            onUpdateJiraConfig={handleUpdateJiraConfig}
            onSyncJiraTickets={handleSyncJiraTickets}
            onLoadAgentsMdStatus={handleLoadAgentsMdStatus}
            onGenerateAgentsMd={handleGenerateAgentsMd}
            onLoadProjectContext={handleLoadProjectContext}
            onSaveProjectContext={handleSaveProjectContext}
            onGenerateProjectContext={handleGenerateProjectContext}
          />
        ) : activeWorkflow === 'Agent Logs' ? (
          /* Full Logs Screen */
          <AgentLogsView
            ticket={selectedTicket}
            run={runStatus}
            activityEvents={activityEvents}
            currentAction={currentAction}
            tokenUsage={tokenUsage}
            onClear={handleClearActivityLogs}
            onStop={handleStopWatchingRun}
          />
        ) : activeWorkflow === 'Simulator' ? (
          /* Full Simulator Screen */
          <SimulatorView />
        ) : (
          /* Main Multi-Pane View (All Tickets / My Tasks) */
          <div className="flex-1 relative overflow-hidden min-w-0">
            {/* Board fills the full area */}
            <div className="w-full h-full">
              <TicketBoard
                selectedProject={selectedProject}
                tickets={tickets}
                selectedTicketId={selectedTicketId}
                isLoadingTickets={isLoadingTickets}
                isSyncingJira={isSyncingJira}
                onSelectTicket={handleSelectTicket}
                onRefreshTickets={() => selectedProjectId && loadTicketsForProject(selectedProjectId)}
                onCreateTicket={handleCreateTicket}
                onSyncJiraTickets={() => selectedProjectId && handleSyncJiraTickets(selectedProjectId)}
                onDeleteTicket={handleDeleteTicket}
                onRunEpic={handleRunEpic}
              />
            </div>

            {/* Dim backdrop — click outside to close */}
            <div
              className={`absolute inset-0 z-10 bg-slate-900/20 backdrop-blur-[1px] transition-opacity duration-300 ${
                selectedTicket ? 'opacity-100' : 'opacity-0 pointer-events-none'
              }`}
              onClick={() => {
                setSelectedTicketId(null);
                localStorage.removeItem('code_agent_selected_ticket_id');
                setRunStatus(null);
                setActiveRunId(null);
              }}
            />

            {/* Sliding drawer — overlays from the right. Epics get a wider 60vw
                panel (dependency levels + long status text need the room); the
                per-ticket run view keeps the narrower fixed width. */}
            <div
              className={`absolute inset-y-0 right-0 z-20 ${
                selectedIsEpic ? 'w-[60vw] min-w-[640px]' : 'w-[620px] max-w-[85vw]'
              } bg-white border-l border-slate-200/80 shadow-2xl overflow-hidden flex flex-col transition-transform duration-300 ease-in-out ${
                selectedTicket ? 'translate-x-0' : 'translate-x-full pointer-events-none'
              }`}
            >
              {selectedTicket && (
                selectedIsEpic ? (
                  <EpicRunPanel
                    key={`epic-${selectedTicket.id}-${epicReloadNonce}`}
                    ticket={selectedTicket}
                    tickets={tickets}
                    projectId={selectedProjectId}
                    jiraBaseUrl={jiraStatus?.base_url || undefined}
                    onSelectChild={handleSelectTicket}
                    onEpicProgress={async () => {
                      if (!selectedProjectId) return;
                      try {
                        setTickets(await CodeAgentApiClient.listTickets(selectedProjectId));
                      } catch (e) {
                        console.error('Epic progress ticket refresh error:', e);
                      }
                    }}
                  />
                ) : (
                  <CenterPane
                    ticket={selectedTicket}
                    run={runStatus}
                    activityEvents={activityEvents}
                    currentAction={currentAction}
                    tokenUsage={tokenUsage}
                    executions={executions}
                    isBusy={isBusy}
                    isRunLoading={isRunLoading}
                    onClearRunView={() => {
                      setSelectedTicketId(null);
                      localStorage.removeItem('code_agent_selected_ticket_id');
                      setRunStatus(null);
                      setActiveRunId(null);
                    }}
                    onStartRun={handleStartRun}
                    onApproveRun={handleApproveRun}
                    onRejectRun={handleRejectRun}
                    onRetryRun={handleRetryRun}
                    onRevertRun={handleRevertRun}
                    onResumeRun={handleResumeRun}
                    onMergeRun={handleMergeRun}
                    onClarifyRun={handleClarifyRun}
                    onClearActivityLogs={handleClearActivityLogs}
                    onStopWatchingRun={handleStopWatchingRun}
                  />
                )
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
