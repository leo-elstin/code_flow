'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { ProjectSummary } from '@/lib/models';
import { POSession } from '@/lib/po-api-client';
import {
  startPOSession,
  listPOSessions,
} from '@/lib/po-api-client';
import {
  Folder,
  FolderOpen,
  Plus,
  Settings,
  ListTodo,
  Ticket,
  ScrollText,
  Compass,
  Trash2,
  ClipboardList,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { CodeAgentApiClient } from '@/lib/api-client';
import POSessionList from './POSessionList';

interface SidebarProps {
  projects: ProjectSummary[];
  selectedProjectId: number | null;
  activeWorkflow: string;
  onSelectProject: (id: number) => void;
  onSelectWorkflow: (workflow: string) => void;
  onAddProject: (path: string, name?: string) => Promise<void>;
  onRemoveProject: (id: number) => Promise<void>;
  isAddingProject: boolean;
}

export default function Sidebar({
  projects,
  selectedProjectId,
  activeWorkflow,
  onSelectProject,
  onSelectWorkflow,
  onAddProject,
  onRemoveProject,
  isAddingProject,
}: SidebarProps) {
  const router = useRouter();
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [projectPath, setProjectPath] = useState('');
  const [projectName, setProjectName] = useState('');
  const [error, setError] = useState('');
  const [isPickingFolder, setIsPickingFolder] = useState(false);

  // PO Sessions state
  const [poSessions, setPoSessions] = useState<POSession[]>([]);
  const [isNewContextOpen, setIsNewContextOpen] = useState(false);
  const [contextInput, setContextInput] = useState('');
  const [isStartingSession, setIsStartingSession] = useState(false);
  const [poError, setPoError] = useState('');
  const [selectedPoSessionId, setSelectedPoSessionId] = useState<string | null>(null);

  // Load PO sessions when project changes
  useEffect(() => {
    const load = async () => {
      try {
        const { sessions } = await listPOSessions(selectedProjectId || undefined);
        setPoSessions(sessions);
      } catch {
        // Silently ignore — backend may not be running
      }
    };
    load();
    // Poll every 5s for new sessions
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [selectedProjectId]);

  const handlePickFolder = async () => {
    setError('');
    setIsPickingFolder(true);
    try {
      const pickedPath = await CodeAgentApiClient.pickProjectFolder(projectPath.trim() || undefined);
      if (pickedPath) {
        setProjectPath(pickedPath);
        if (!projectName.trim()) {
          setProjectName(pickedPath.split('/').filter(Boolean).pop() || '');
        }
      }
    } catch (err: any) {
      setError(err.message || 'Failed to open folder picker. Ensure the backend API is running.');
    } finally {
      setIsPickingFolder(false);
    }
  };

  const handleAddSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    const trimmedPath = projectPath.trim();
    if (!trimmedPath) {
      setError('Project path is required');
      return;
    }

    try {
      await onAddProject(trimmedPath, projectName.trim() || undefined);
      setProjectPath('');
      setProjectName('');
      setIsDialogOpen(false);
    } catch (err: any) {
      setError(err.message || 'Failed to add project. Ensure the path is correct and accessible.');
    }
  };

  const handleRemoveProject = async (e: React.MouseEvent, projectId: number, projectName: string) => {
    e.stopPropagation();
    if (!confirm(`Remove "${projectName}" from the list? Its tickets will also be removed.`)) {
      return;
    }
    try {
      await onRemoveProject(projectId);
    } catch (err: any) {
      alert(err.message || 'Failed to remove project');
    }
  };

  const handleNewPOSession = async (e: React.FormEvent) => {
    e.preventDefault();
    setPoError('');
    const trimmed = contextInput.trim();
    if (!trimmed) {
      setPoError('Please enter some context');
      return;
    }
    setIsStartingSession(true);
    try {
      const selectedProject = projects.find((p) => p.id === selectedProjectId);
      const { session_id } = await startPOSession({
        initial_context: trimmed,
        project_id: selectedProjectId || undefined,
        project_path: selectedProject?.path || '',
        research_depth: 'codebase',
      });
      setContextInput('');
      setIsNewContextOpen(false);
      setSelectedPoSessionId(session_id);
      router.push(`/po/${session_id}`);
      // Refresh list
      const { sessions } = await listPOSessions(selectedProjectId || undefined);
      setPoSessions(sessions);
    } catch (err: any) {
      setPoError(err.message || 'Failed to start session');
    } finally {
      setIsStartingSession(false);
    }
  };

  const handleSelectPOSession = (sessionId: string) => {
    setSelectedPoSessionId(sessionId);
    router.push(`/po/${sessionId}`);
  };

  return (
    <aside className="w-60 bg-slate-950 text-slate-200 border-r border-slate-900 flex flex-col justify-between select-none">
      <div className="flex flex-col flex-1 min-h-0">
        {/* Brand Header */}
        <div className="h-16 px-6 border-b border-slate-900/50 flex items-center gap-3">
          <div className="bg-blue-600/20 p-2 rounded-lg text-blue-400">
            <Compass size={18} className="animate-spin-slow" />
          </div>
          <div>
            <h1 className="font-bold text-sm text-white tracking-wide">CodeFlow Agent</h1>
            <p className="text-[10px] text-slate-500 font-medium">Orchestration Web Console</p>
          </div>
        </div>

        {/* Scrollable Navigation */}
        <div className="flex-1 overflow-y-auto px-3 py-4 space-y-6 scrollbar-thin scrollbar-thumb-slate-900">
          {/* Projects Section */}
          <div className="space-y-1">
            <div className="flex items-center justify-between px-3 mb-2">
              <span className="text-[10px] font-bold text-slate-500 tracking-wider">PROJECTS</span>
              <button
                onClick={() => setIsDialogOpen(true)}
                className="text-slate-500 hover:text-white transition-colors"
                title="Add project"
              >
                <Plus size={14} />
              </button>
            </div>

            <div className="space-y-0.5">
              {projects.length === 0 ? (
                <div className="px-3 py-2 text-xs text-slate-500 italic">No projects loaded</div>
              ) : (
                projects.map((proj) => {
                  const isSelected = proj.id === selectedProjectId;
                  return (
                    <div
                      key={proj.id}
                      className={`group w-full flex items-center gap-1 rounded-lg transition-all ${
                        isSelected
                          ? 'bg-slate-900 shadow-sm ring-1 ring-white/5'
                          : 'hover:bg-slate-900/40'
                      }`}
                    >
                      <button
                        onClick={() => onSelectProject(proj.id)}
                        className={`flex-1 min-w-0 flex items-center gap-2.5 px-3 py-2 text-xs font-medium transition-all text-left ${
                          isSelected
                            ? 'text-white font-semibold'
                            : 'text-slate-400 group-hover:text-slate-200'
                        }`}
                      >
                        <Folder size={14} className={isSelected ? 'text-blue-400' : 'text-slate-500'} />
                        <span className="truncate">{proj.name}</span>
                      </button>
                      <button
                        type="button"
                        onClick={(e) => handleRemoveProject(e, proj.id, proj.name)}
                        className="opacity-0 group-hover:opacity-100 focus:opacity-100 p-1.5 mr-1 rounded-md text-slate-500 hover:text-rose-400 hover:bg-rose-950/30 transition-all"
                        title="Remove project"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* Workflows Section */}
          <div className="space-y-1">
            <div className="px-3 mb-2">
              <span className="text-[10px] font-bold text-slate-500 tracking-wider">WORKFLOWS</span>
            </div>

            <div className="space-y-0.5">
              {[
                { id: 'My Tasks', icon: <ListTodo size={14} /> },
                { id: 'All Tickets', icon: <Ticket size={14} /> },
                { id: 'Agent Logs', icon: <ScrollText size={14} /> },
                { id: 'Settings', icon: <Settings size={14} /> },
              ].map(({ id, icon }) => (
                <button
                  key={id}
                  onClick={() => onSelectWorkflow(id)}
                  className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs font-medium transition-all text-left ${
                    activeWorkflow === id
                      ? 'bg-slate-900 text-white shadow-sm ring-1 ring-white/5 font-semibold'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/40'
                  }`}
                >
                  <span className={activeWorkflow === id ? 'text-blue-400' : 'text-slate-500'}>
                    {icon}
                  </span>
                  <span>{id}</span>
                </button>
              ))}
            </div>
          </div>

          {/* PO Sessions Section */}
          <div className="space-y-0.5">
            <POSessionList
              sessions={poSessions}
              onSelect={handleSelectPOSession}
              onNewSession={() => setIsNewContextOpen(true)}
              selectedSessionId={selectedPoSessionId}
            />
          </div>
        </div>
      </div>

      {/* Profile Section at Bottom */}
      <div className="p-4 border-t border-slate-900 flex items-center justify-between gap-3 bg-slate-950/40">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-full border border-slate-800 overflow-hidden flex-shrink-0 bg-slate-800">
            <img
              src="https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=80&auto=format&fit=crop&q=80"
              alt="Avatar"
              className="w-full h-full object-cover"
            />
          </div>
          <div className="min-w-0">
            <p className="text-xs font-semibold text-white truncate">Leo Elstin</p>
            <p className="text-[10px] text-green-500 flex items-center gap-1 font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500 inline-block animate-pulse"></span>
              Online
            </p>
          </div>
        </div>
        <button
          onClick={() => onSelectWorkflow('Settings')}
          className={`p-1.5 rounded-lg transition-colors hover:bg-slate-900 ${
            activeWorkflow === 'Settings' ? 'text-white bg-slate-900' : 'text-slate-400 hover:text-slate-200'
          }`}
          title="Settings"
        >
          <Settings size={16} />
        </button>
      </div>

      {/* Add Project Dialog */}
      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogContent className="sm:max-w-[425px] bg-slate-900 border-slate-800 text-slate-100">
          <form onSubmit={handleAddSubmit}>
            <DialogHeader>
              <DialogTitle className="text-white text-base">Add Project</DialogTitle>
              <DialogDescription className="text-slate-400 text-xs">
                Select or enter the absolute path to your local Flutter or Dart project directory.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-4 py-4">
              <div className="flex flex-col gap-1.5">
                <label className="text-[11px] font-bold text-slate-400 tracking-wide uppercase">
                  Project Name (Optional)
                </label>
                <Input
                  id="project-name"
                  placeholder="e.g. My Flutter App"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  className="bg-slate-950 border-slate-800 text-slate-100 placeholder:text-slate-600 focus-visible:ring-blue-500"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-[11px] font-bold text-slate-400 tracking-wide uppercase">
                  Absolute Path
                </label>
                <div className="flex gap-2">
                  <Input
                    id="project-path"
                    placeholder="/Users/leo.e/dev/my-flutter-app"
                    value={projectPath}
                    onChange={(e) => setProjectPath(e.target.value)}
                    className="bg-slate-950 border-slate-800 text-slate-100 placeholder:text-slate-600 focus-visible:ring-blue-500 font-mono text-xs"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    onClick={handlePickFolder}
                    disabled={isPickingFolder || isAddingProject}
                    className="border-slate-800 hover:bg-slate-800 hover:text-slate-100 shrink-0 px-3 gap-1.5 text-xs font-semibold"
                    title="Browse for folder"
                  >
                    <FolderOpen size={14} className={isPickingFolder ? 'animate-pulse' : ''} />
                    <span>{isPickingFolder ? 'Picking...' : 'Browse'}</span>
                  </Button>
                </div>
                <p className="text-[10px] text-slate-500">
                  Must be a valid filesystem path containing a{' '}
                  <code className="text-slate-400">pubspec.yaml</code>.
                </p>
              </div>
              {error && (
                <div className="text-xs text-red-400 font-medium bg-red-950/20 border border-red-900/40 p-2 rounded-lg">
                  {error}
                </div>
              )}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setIsDialogOpen(false)}
                className="border-slate-800 hover:bg-slate-800 hover:text-slate-100"
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={isAddingProject}
                className="bg-blue-600 hover:bg-blue-500 text-white font-semibold"
              >
                {isAddingProject ? 'Adding...' : 'Add Project'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* New PO Context Dialog */}
      <Dialog open={isNewContextOpen} onOpenChange={setIsNewContextOpen}>
        <DialogContent className="sm:max-w-[480px] bg-slate-900 border-slate-800 text-slate-100">
          <form onSubmit={handleNewPOSession}>
            <DialogHeader>
              <DialogTitle className="text-white text-base flex items-center gap-2">
                <ClipboardList size={16} className="text-indigo-400" />
                New PO Session
              </DialogTitle>
              <DialogDescription className="text-slate-400 text-xs">
                Describe a feature idea or request. The PO agent will ask clarifying questions and
                generate user stories.
              </DialogDescription>
            </DialogHeader>
            <div className="py-4">
              <div className="flex flex-col gap-1.5">
                <label className="text-[11px] font-bold text-slate-400 tracking-wide uppercase">
                  Initial Context
                </label>
                <textarea
                  placeholder="e.g. I want to add a dark mode toggle to the settings screen"
                  value={contextInput}
                  onChange={(e) => setContextInput(e.target.value)}
                  rows={4}
                  className="w-full resize-none rounded-lg border border-slate-800 bg-slate-950 text-slate-100 placeholder:text-slate-600 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
              {poError && (
                <div className="mt-2 text-xs text-red-400 font-medium bg-red-950/20 border border-red-900/40 p-2 rounded-lg">
                  {poError}
                </div>
              )}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setIsNewContextOpen(false)}
                className="border-slate-800 hover:bg-slate-800 hover:text-slate-100"
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={isStartingSession}
                className="bg-indigo-600 hover:bg-indigo-500 text-white font-semibold"
              >
                {isStartingSession ? 'Starting...' : 'Start Session'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </aside>
  );
}
