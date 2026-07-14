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
    <aside className="w-60 bg-[#201e1d] text-white/70 flex flex-col justify-between select-none">
      <div className="flex flex-col flex-1 min-h-0">
        {/* Brand Header */}
        <div className="h-16 px-5 flex items-center gap-2.5 border-b border-white/8">
          <div className="bg-[#ec3013] p-2 rounded-[9px] text-white shadow-[0_2px_8px_rgba(236,48,19,.35)]">
            <Compass size={18} />
          </div>
          <div>
            <h1 className="font-extrabold text-sm text-white tracking-tight">CodeFlow Agent</h1>
            <p className="text-[10px] text-white/40 font-semibold uppercase tracking-wider">Orchestration Console</p>
          </div>
        </div>

        {/* Scrollable Navigation */}
        <div className="flex-1 overflow-y-auto px-3 py-4 space-y-6">
          {/* Projects Section */}
          <div className="space-y-1">
            <div className="flex items-center justify-between px-3 mb-2">
              <span className="text-[9px] font-bold text-white/40 tracking-wider">PROJECTS</span>
              <button
                onClick={() => setIsDialogOpen(true)}
                className="text-white/40 hover:text-white transition-colors rounded-md p-0.5"
                title="Add project"
              >
                <Plus size={14} />
              </button>
            </div>

            <div className="space-y-0.5">
              {projects.length === 0 ? (
                <div className="px-3 py-2 text-xs text-white/30 italic">No projects loaded</div>
              ) : (
                projects.map((proj) => {
                  const isSelected = proj.id === selectedProjectId;
                  return (
                    <div
                      key={proj.id}
                      className={`group w-full flex items-center gap-1 rounded-lg transition-all ${
                        isSelected ? 'bg-white/8' : 'hover:bg-white/5'
                      }`}
                    >
                      <button
                        onClick={() => onSelectProject(proj.id)}
                        className={`flex-1 min-w-0 flex items-center gap-2.5 px-3 py-2 text-xs font-medium transition-all text-left ${
                          isSelected ? 'text-white font-semibold' : 'text-white/60 group-hover:text-white/90'
                        }`}
                      >
                        <Folder size={14} className={isSelected ? 'text-[#ff9783]' : 'text-white/35'} />
                        <span className="truncate">{proj.name}</span>
                      </button>
                      <button
                        type="button"
                        onClick={(e) => handleRemoveProject(e, proj.id, proj.name)}
                        className="opacity-0 group-hover:opacity-100 focus:opacity-100 p-1.5 mr-1 rounded-md text-white/40 hover:text-rose-400 hover:bg-rose-500/10 transition-all"
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
              <span className="text-[9px] font-bold text-white/40 tracking-wider">WORKFLOWS</span>
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
                      ? 'bg-white/8 text-white font-semibold'
                      : 'text-white/60 hover:text-white/90 hover:bg-white/5'
                  }`}
                >
                  <span className={activeWorkflow === id ? 'text-[#ff9783]' : 'text-white/35'}>
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
      <div className="p-4 border-t border-white/8 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-full overflow-hidden flex-shrink-0 bg-white/10">
            <img
              src="https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=80&auto=format&fit=crop&q=80"
              alt="Avatar"
              className="w-full h-full object-cover"
            />
          </div>
          <div className="min-w-0">
            <p className="text-xs font-semibold text-white truncate">Leo Elstin</p>
            <p className="text-[10px] text-emerald-400 flex items-center gap-1 font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block animate-pulse"></span>
              Online
            </p>
          </div>
        </div>
        <button
          onClick={() => onSelectWorkflow('Settings')}
          className={`p-1.5 rounded-lg transition-colors hover:bg-white/8 ${
            activeWorkflow === 'Settings' ? 'text-white bg-white/8' : 'text-white/50 hover:text-white/80'
          }`}
          title="Settings"
        >
          <Settings size={16} />
        </button>
      </div>

      {/* Add Project Dialog */}
      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogContent className="sm:max-w-[425px] bg-white border-neutral-200 text-neutral-900 rounded-xl shadow-lg">
          <form onSubmit={handleAddSubmit}>
            <DialogHeader>
              <DialogTitle className="text-neutral-900 text-base">Add Project</DialogTitle>
              <DialogDescription className="text-neutral-500 text-xs">
                Select or enter the absolute path to your local Flutter or Dart project directory.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-4 py-4">
              <div className="flex flex-col gap-1.5">
                <label className="text-[11px] font-bold text-neutral-500 tracking-wide uppercase">
                  Project Name (Optional)
                </label>
                <Input
                  id="project-name"
                  placeholder="e.g. My Flutter App"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  className="bg-white border-neutral-200 text-neutral-900 placeholder:text-neutral-400 rounded-lg focus-visible:ring-[#ec3013]"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-[11px] font-bold text-neutral-500 tracking-wide uppercase">
                  Absolute Path
                </label>
                <div className="flex gap-2">
                  <Input
                    id="project-path"
                    placeholder="/Users/leo.e/dev/my-flutter-app"
                    value={projectPath}
                    onChange={(e) => setProjectPath(e.target.value)}
                    className="bg-white border-neutral-200 text-neutral-900 placeholder:text-neutral-400 rounded-lg focus-visible:ring-[#ec3013] font-mono text-xs"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    onClick={handlePickFolder}
                    disabled={isPickingFolder || isAddingProject}
                    className="border-neutral-200 hover:bg-neutral-100 hover:text-neutral-900 shrink-0 px-3 gap-1.5 text-xs font-semibold rounded-lg"
                    title="Browse for folder"
                  >
                    <FolderOpen size={14} className={isPickingFolder ? 'animate-pulse' : ''} />
                    <span>{isPickingFolder ? 'Picking...' : 'Browse'}</span>
                  </Button>
                </div>
                <p className="text-[10px] text-neutral-400">
                  Must be a valid filesystem path containing a{' '}
                  <code className="text-neutral-600">pubspec.yaml</code>.
                </p>
              </div>
              {error && (
                <div className="text-xs text-rose-600 font-medium bg-rose-50 border border-rose-200 p-2 rounded-lg">
                  {error}
                </div>
              )}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setIsDialogOpen(false)}
                className="border-neutral-200 hover:bg-neutral-100 hover:text-neutral-900 rounded-lg"
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={isAddingProject}
                className="bg-[#ec3013] hover:bg-[#dd2b0f] text-white font-semibold rounded-lg shadow-sm"
              >
                {isAddingProject ? 'Adding...' : 'Add Project'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* New PO Context Dialog */}
      <Dialog open={isNewContextOpen} onOpenChange={setIsNewContextOpen}>
        <DialogContent className="sm:max-w-[480px] bg-white border-neutral-200 text-neutral-900 rounded-xl shadow-lg">
          <form onSubmit={handleNewPOSession}>
            <DialogHeader>
              <DialogTitle className="text-neutral-900 text-base flex items-center gap-2">
                <ClipboardList size={16} className="text-[#ec3013]" />
                New PO Session
              </DialogTitle>
              <DialogDescription className="text-neutral-500 text-xs">
                Describe a feature idea or request. The PO agent will ask clarifying questions and
                generate user stories.
              </DialogDescription>
            </DialogHeader>
            <div className="py-4">
              <div className="flex flex-col gap-1.5">
                <label className="text-[11px] font-bold text-neutral-500 tracking-wide uppercase">
                  Initial Context
                </label>
                <textarea
                  placeholder="e.g. I want to add a dark mode toggle to the settings screen"
                  value={contextInput}
                  onChange={(e) => setContextInput(e.target.value)}
                  rows={4}
                  className="w-full resize-none rounded-lg border border-neutral-200 bg-white text-neutral-900 placeholder:text-neutral-400 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[#ec3013]"
                />
              </div>
              {poError && (
                <div className="mt-2 text-xs text-rose-600 font-medium bg-rose-50 border border-rose-200 p-2 rounded-lg">
                  {poError}
                </div>
              )}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setIsNewContextOpen(false)}
                className="border-neutral-200 hover:bg-neutral-100 hover:text-neutral-900 rounded-lg"
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={isStartingSession}
                className="bg-[#ec3013] hover:bg-[#dd2b0f] text-white font-semibold rounded-lg shadow-sm"
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
