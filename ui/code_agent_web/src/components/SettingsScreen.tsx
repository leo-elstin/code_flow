'use client';

import React, { useState, useEffect } from 'react';
import { 
  ProjectSummary, 
  JiraConfig, 
  JiraStatusCheck, 
  AgentsMdStatus,
  ProjectContextConfig
} from '@/lib/models';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { 
  Settings, 
  FileText, 
  RefreshCw, 
  Sparkles, 
  Database,
  Link,
  CheckCircle,
  XCircle,
  HelpCircle,
  Save,
  AlertCircle
} from 'lucide-react';
import { toast } from 'sonner';
import LlmProviderSettings from '@/components/LlmProviderSettings';

interface SettingsScreenProps {
  selectedProject: ProjectSummary | null;
  apiBaseUrl: string;
  jiraStatus: JiraStatusCheck | null;
  jiraConfig: JiraConfig | null;
  agentsMdStatus: AgentsMdStatus | null;
  projectContext: ProjectContextConfig | null;
  isGeneratingAgentsMd: boolean;
  isGeneratingContext: boolean;
  isSavingContext: boolean;
  isSyncingJira: boolean;
  onSaveBaseUrl: (url: string) => void;
  onLoadJiraConfig: (projectId: number) => Promise<void>;
  onUpdateJiraConfig: (projectId: number, jql?: string, mapping?: Record<string, string>) => Promise<void>;
  onSyncJiraTickets: (projectId: number) => Promise<void>;
  onLoadAgentsMdStatus: (projectId: number) => Promise<void>;
  onGenerateAgentsMd: (projectId: number, hints?: string) => Promise<void>;
  onLoadProjectContext: (projectId: number) => Promise<void>;
  onSaveProjectContext: (projectId: number, contextText: string, plannerSkills: string[], devSkills: string[]) => Promise<void>;
  onGenerateProjectContext: (projectId: number, hints?: string) => Promise<string>;
}

export default function SettingsScreen({
  selectedProject,
  apiBaseUrl,
  jiraStatus,
  jiraConfig,
  agentsMdStatus,
  projectContext,
  isGeneratingAgentsMd,
  isGeneratingContext,
  isSavingContext,
  isSyncingJira,
  onSaveBaseUrl,
  onLoadJiraConfig,
  onUpdateJiraConfig,
  onSyncJiraTickets,
  onLoadAgentsMdStatus,
  onGenerateAgentsMd,
  onLoadProjectContext,
  onSaveProjectContext,
  onGenerateProjectContext,
}: SettingsScreenProps) {
  // Local states
  const [baseUrlInput, setBaseUrlInput] = useState(apiBaseUrl);
  const [activeSubTab, setActiveSubTab] = useState('api');
  
  // Jira state
  const [jiraJql, setJiraJql] = useState('');
  const [jiraMapping, setJiraMapping] = useState<Record<string, string>>({
    developing: 'In Progress',
    completed: 'Done',
    failed: 'To Do',
  });
  
  // Agents.md state
  const [agentsMdHints, setAgentsMdHints] = useState('');
  
  // Context state
  const [contextText, setContextText] = useState('');
  const [contextHints, setContextHints] = useState('');

  // Load configuration on selected project change
  useEffect(() => {
    if (selectedProject) {
      onLoadJiraConfig(selectedProject.id);
      onLoadAgentsMdStatus(selectedProject.id);
      onLoadProjectContext(selectedProject.id);
    }
  }, [selectedProject]);

  // Sync state values
  useEffect(() => {
    if (jiraConfig) {
      setJiraJql(jiraConfig.jira_jql);
      if (jiraConfig.jira_status_mapping) {
        setJiraMapping({
          developing: jiraConfig.jira_status_mapping.developing || 'In Progress',
          completed: jiraConfig.jira_status_mapping.completed || 'Done',
          failed: jiraConfig.jira_status_mapping.failed || 'To Do',
        });
      }
    }
  }, [jiraConfig]);

  useEffect(() => {
    if (projectContext) {
      setContextText(projectContext.context_text);
    }
  }, [projectContext]);

  const handleSaveBaseUrl = (e: React.FormEvent) => {
    e.preventDefault();
    const url = baseUrlInput.trim();
    if (!url) return;
    onSaveBaseUrl(url);
    toast.success('API Base URL updated');
  };

  const handleSaveJiraJql = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedProject) return;
    try {
      await onUpdateJiraConfig(selectedProject.id, jiraJql.trim(), jiraMapping);
      toast.success('Jira JQL filter updated');
    } catch (err: any) {
      toast.error(err.message || 'Failed to update Jira filter');
    }
  };

  const handleSaveJiraMapping = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedProject) return;
    try {
      await onUpdateJiraConfig(selectedProject.id, jiraJql, jiraMapping);
      toast.success('Jira status mapping updated');
    } catch (err: any) {
      toast.error(err.message || 'Failed to update Jira status mapping');
    }
  };

  const handleJiraSync = async () => {
    if (!selectedProject) return;
    try {
      await onSyncJiraTickets(selectedProject.id);
      toast.success('Jira tickets synchronized successfully');
    } catch (err: any) {
      toast.error(err.message || 'Jira synchronization failed');
    }
  };

  const handleGenerateAgentsMd = async () => {
    if (!selectedProject) return;
    try {
      await onGenerateAgentsMd(selectedProject.id, agentsMdHints.trim() || undefined);
      toast.success('AGENTS.md rules file generated');
      setAgentsMdHints('');
    } catch (err: any) {
      toast.error(err.message || 'Failed to generate AGENTS.md');
    }
  };

  const handleSaveContext = async () => {
    if (!selectedProject) return;
    try {
      await onSaveProjectContext(selectedProject.id, contextText, [], []);
      toast.success('Project context updated');
    } catch (err: any) {
      toast.error(err.message || 'Failed to save project context');
    }
  };

  const handleGenerateContext = async () => {
    if (!selectedProject) return;
    try {
      const generated = await onGenerateProjectContext(selectedProject.id, contextHints.trim() || undefined);
      setContextText(generated);
      setContextHints('');
      toast.success('Context text draft generated');
    } catch (err: any) {
      toast.error(err.message || 'Failed to generate context');
    }
  };

  return (
    <div className="flex-1 bg-slate-50/30 overflow-y-auto h-full p-8 select-none">
      <div className="max-w-4xl mx-auto space-y-6">
        <div>
          <h2 className="text-xl font-bold text-slate-800 flex items-center gap-2">
            <Settings size={22} className="text-slate-600" />
            <span>Settings</span>
          </h2>
          <p className="text-xs text-slate-400 font-medium">Configure API connections, Jira credentials, and context behaviors.</p>
        </div>

        <Tabs value={activeSubTab} onValueChange={setActiveSubTab} className="space-y-4">
          <TabsList className="bg-slate-200/50 p-1 rounded-xl">
            <TabsTrigger value="api" className="text-xs font-semibold px-4 rounded-lg data-[state=active]:bg-white data-[state=active]:text-slate-800">
              API Connection
            </TabsTrigger>
            <TabsTrigger value="llm" className="text-xs font-semibold px-4 rounded-lg data-[state=active]:bg-white data-[state=active]:text-slate-800">
              LLM Provider
            </TabsTrigger>
            <TabsTrigger value="jira" className="text-xs font-semibold px-4 rounded-lg data-[state=active]:bg-white data-[state=active]:text-slate-800">
              Jira Integration
            </TabsTrigger>
            <TabsTrigger value="agents-md" disabled={!selectedProject} className="text-xs font-semibold px-4 rounded-lg data-[state=active]:bg-white data-[state=active]:text-slate-800">
              AI Guide (AGENTS.md)
            </TabsTrigger>
            <TabsTrigger value="context" disabled={!selectedProject} className="text-xs font-semibold px-4 rounded-lg data-[state=active]:bg-white data-[state=active]:text-slate-800">
              Module Context
            </TabsTrigger>
          </TabsList>

          {/* API Connection Tab */}
          <TabsContent value="api">
            <Card className="border-slate-200/80 shadow-xs rounded-xl">
              <CardHeader>
                <CardTitle className="text-sm font-bold text-slate-800 flex items-center gap-2">
                  <Link size={16} className="text-blue-500" />
                  <span>Backend Connection API</span>
                </CardTitle>
                <CardDescription className="text-[11px] text-slate-400">
                  Endpoint URL configuration for linking to your tester-rag-api local backend service.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <form onSubmit={handleSaveBaseUrl} className="flex gap-3 max-w-lg">
                  <Input
                    placeholder="http://127.0.0.1:8000"
                    value={baseUrlInput}
                    onChange={(e) => setBaseUrlInput(e.target.value)}
                    className="bg-white border-slate-200 text-xs text-slate-800 placeholder:text-slate-400 focus-visible:ring-blue-500"
                  />
                  <Button type="submit" className="bg-blue-600 hover:bg-blue-500 font-semibold text-xs px-4 flex items-center gap-1">
                    <Save size={13} />
                    <span>Save</span>
                  </Button>
                </form>
              </CardContent>
            </Card>
          </TabsContent>

          {/* LLM Provider Tab */}
          <TabsContent value="llm">
            <LlmProviderSettings selectedProject={selectedProject} />
          </TabsContent>

          {/* Jira Integration Tab */}
          <TabsContent value="jira" className="space-y-4">
            {/* Global credentials check card */}
            <Card className="border-slate-200/80 shadow-xs rounded-xl">
              <CardHeader>
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <CardTitle className="text-sm font-bold text-slate-800 flex items-center gap-2">
                      <Database size={16} className="text-blue-500" />
                      <span>Jira Cloud Global Settings</span>
                    </CardTitle>
                    <CardDescription className="text-[11px] text-slate-400">
                      Credentials status derived from your backend local `.env` variables.
                    </CardDescription>
                  </div>
                  {jiraStatus?.configured ? (
                    <Badge className="bg-emerald-500/10 border-emerald-500/20 text-emerald-500 font-bold hover:bg-emerald-500/10 flex items-center gap-1 rounded">
                      <CheckCircle size={10} />
                      <span>Connected</span>
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="bg-rose-50 border-rose-200 text-rose-500 font-bold hover:bg-rose-50 flex items-center gap-1 rounded">
                      <XCircle size={10} />
                      <span>Not Configured</span>
                    </Badge>
                  )}
                </div>
              </CardHeader>
              <CardContent className="space-y-2.5">
                {jiraStatus?.configured ? (
                  <div className="space-y-1.5 text-xs text-slate-600 font-medium">
                    <p><strong className="text-slate-800 font-semibold">Instance URL:</strong> {jiraStatus.base_url}</p>
                    <p><strong className="text-slate-800 font-semibold">Email:</strong> {jiraStatus.user_email}</p>
                  </div>
                ) : (
                  <div className="bg-slate-50 border border-slate-200 p-3.5 rounded-lg flex gap-2.5 max-w-xl">
                    <AlertCircle className="text-amber-500 flex-shrink-0 mt-0.5" size={16} />
                    <p className="text-[10px] text-slate-500 leading-normal">
                      Jira Cloud auth is missing. Set your JIRA credentials (<code className="text-slate-700 bg-slate-100 px-1 rounded font-mono">JIRA_BASE_URL</code>, <code className="text-slate-700 bg-slate-100 px-1 rounded font-mono">JIRA_USER_EMAIL</code>, and <code className="text-slate-700 bg-slate-100 px-1 rounded font-mono">JIRA_API_TOKEN</code>) in the backend repository&apos;s environment file.
                    </p>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Project specific config */}
            {selectedProject ? (
              jiraStatus?.configured ? (
                <div className="grid md:grid-cols-2 gap-4">
                  {/* JQL Filter config */}
                  <Card className="border-slate-200/80 shadow-xs rounded-xl">
                    <CardHeader>
                      <CardTitle className="text-xs font-bold text-slate-400 tracking-wider uppercase">Jira JQL Query Filter</CardTitle>
                      <CardDescription className="text-[10px] text-slate-400 leading-normal">
                        Configure the Jira Search JQL filter query representing tickets destined for this project queue.
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <form onSubmit={handleSaveJiraJql} className="flex gap-2">
                        <Input
                          placeholder="e.g. project = 'MYPROJ' AND status = 'To Do'"
                          value={jiraJql}
                          onChange={(e) => setJiraJql(e.target.value)}
                          className="bg-white border-slate-200 text-xs text-slate-800 focus-visible:ring-blue-500 font-mono"
                        />
                        <Button type="submit" className="bg-blue-600 hover:bg-blue-500 font-semibold text-xs h-9 px-3 flex items-center gap-1">
                          <Save size={13} />
                          <span>Save</span>
                        </Button>
                      </form>
                      <div className="mt-4 border-t border-slate-100 pt-4 flex justify-between items-center">
                        <span className="text-[10px] text-slate-400 font-medium">Trigger instant sync to fetch new Jira tickets:</span>
                        <Button
                          type="button"
                          onClick={handleJiraSync}
                          disabled={isSyncingJira || !jiraJql.trim()}
                          variant="outline"
                          className="border-slate-200 hover:bg-slate-50 text-xs font-semibold flex items-center gap-1.5 h-9"
                        >
                          {isSyncingJira ? <RefreshCw size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                          <span>Sync Now</span>
                        </Button>
                      </div>
                    </CardContent>
                  </Card>

                  {/* Status mappings config */}
                  <Card className="border-slate-200/80 shadow-xs rounded-xl">
                    <CardHeader>
                      <CardTitle className="text-xs font-bold text-slate-400 tracking-wider uppercase">Jira Status Writeback Maps</CardTitle>
                      <CardDescription className="text-[10px] text-slate-400 leading-normal">
                        Map AI agent development status stages back to Jira workflow status transition names.
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <form onSubmit={handleSaveJiraMapping} className="space-y-3">
                        <div className="grid grid-cols-3 items-center gap-2 text-xs font-medium">
                          <span className="text-slate-500 font-mono">developing</span>
                          <span className="text-center text-slate-300">→</span>
                          <Input
                            value={jiraMapping.developing}
                            onChange={(e) => setJiraMapping({...jiraMapping, developing: e.target.value})}
                            className="bg-white border-slate-200 text-xs text-slate-800 h-8"
                          />
                        </div>
                        <div className="grid grid-cols-3 items-center gap-2 text-xs font-medium">
                          <span className="text-slate-500 font-mono">completed</span>
                          <span className="text-center text-slate-300">→</span>
                          <Input
                            value={jiraMapping.completed}
                            onChange={(e) => setJiraMapping({...jiraMapping, completed: e.target.value})}
                            className="bg-white border-slate-200 text-xs text-slate-800 h-8"
                          />
                        </div>
                        <div className="grid grid-cols-3 items-center gap-2 text-xs font-medium">
                          <span className="text-slate-500 font-mono">failed</span>
                          <span className="text-center text-slate-300">→</span>
                          <Input
                            value={jiraMapping.failed}
                            onChange={(e) => setJiraMapping({...jiraMapping, failed: e.target.value})}
                            className="bg-white border-slate-200 text-xs text-slate-800 h-8"
                          />
                        </div>
                        <div className="flex justify-end pt-2">
                          <Button type="submit" className="bg-blue-600 hover:bg-blue-500 font-semibold text-xs h-9 px-3 flex items-center gap-1">
                            <Save size={13} />
                            <span>Save Mapping</span>
                          </Button>
                        </div>
                      </form>
                    </CardContent>
                  </Card>
                </div>
              ) : (
                <div className="p-8 text-center text-xs text-slate-400 bg-slate-100/50 border border-slate-200 rounded-xl">
                  Configure global credentials first to enable project settings.
                </div>
              )
            ) : (
              <div className="p-8 text-center text-xs text-slate-400 bg-slate-100/50 border border-slate-200 rounded-xl">
                Select a project in the sidebar to configure Jira settings.
              </div>
            )}
          </TabsContent>

          {/* AGENTS.md Tab */}
          <TabsContent value="agents-md" className="space-y-4">
            {selectedProject && (
              <Card className="border-slate-200/80 shadow-xs rounded-xl">
                <CardHeader>
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <CardTitle className="text-sm font-bold text-slate-800 flex items-center gap-2">
                        <FileText size={16} className="text-blue-500" />
                        <span>Project Coding Guide (AGENTS.md)</span>
                      </CardTitle>
                      <CardDescription className="text-[11px] text-slate-400 leading-normal">
                        RAG context rules file detailing codebase conventions inside your target project directory.
                      </CardDescription>
                    </div>
                    {agentsMdStatus?.exists ? (
                      <Badge className="bg-emerald-500/10 border-emerald-500/20 text-emerald-500 font-bold hover:bg-emerald-500/10 flex items-center gap-1 rounded">
                        <CheckCircle size={10} />
                        <span>Exists</span>
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="bg-rose-50 border-rose-200 text-rose-500 font-bold hover:bg-rose-50 flex items-center gap-1 rounded">
                        <XCircle size={10} />
                        <span>Missing</span>
                      </Badge>
                    )}
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  {/* File preview */}
                  {agentsMdStatus?.exists && (
                    <div className="space-y-1.5">
                      <span className="text-[10px] font-bold text-slate-400 tracking-wide uppercase">File Content Preview</span>
                      <pre className="p-3.5 bg-slate-950 text-slate-300 font-mono text-[10px] leading-relaxed rounded-lg border border-slate-800 max-h-48 overflow-y-auto whitespace-pre-wrap select-text text-left">
                        {agentsMdStatus.content_preview || 'Empty file.'}
                      </pre>
                    </div>
                  )}

                  {/* Generation Form */}
                  <div className="border-t border-slate-100 pt-4 space-y-3.5">
                    <div className="flex flex-col gap-1.5 max-w-xl">
                      <label className="text-[10px] font-bold text-slate-500 tracking-wider uppercase">Guide Hints & Constraints (Optional)</label>
                      <Textarea
                        placeholder="e.g. Flutter layout follows clean architecture, uses Riverpod viewmodels, custom colors live in theme.dart..."
                        value={agentsMdHints}
                        onChange={(e) => setAgentsMdHints(e.target.value)}
                        className="bg-white border-slate-200 text-xs text-slate-800 focus-visible:ring-blue-500 min-h-[70px]"
                      />
                    </div>
                    <Button
                      type="button"
                      onClick={handleGenerateAgentsMd}
                      disabled={isGeneratingAgentsMd}
                      className="bg-blue-600 hover:bg-blue-500 font-semibold text-xs h-9 px-4 flex items-center gap-1.5"
                    >
                      {isGeneratingAgentsMd ? (
                        <RefreshCw size={13} className="animate-spin" />
                      ) : (
                        <Sparkles size={13} />
                      )}
                      <span>{agentsMdStatus?.exists ? 'Re-generate AGENTS.md' : 'Generate AGENTS.md'}</span>
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          {/* Module Context Tab */}
          <TabsContent value="context" className="space-y-4">
            {selectedProject && (
              <Card className="border-slate-200/80 shadow-xs rounded-xl">
                <CardHeader>
                  <CardTitle className="text-sm font-bold text-slate-800 flex items-center gap-2">
                    <Database size={16} className="text-blue-500" />
                    <span>Project Context RAG Config</span>
                  </CardTitle>
                  <CardDescription className="text-[11px] text-slate-400 leading-normal">
                    General repository description used to retrieve relevant modules during the planning stage.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="space-y-1.5">
                    <span className="text-[10px] font-bold text-slate-400 tracking-wide uppercase">Context Text</span>
                    <Textarea
                      placeholder="Repository outline context rules..."
                      value={contextText}
                      onChange={(e) => setContextText(e.target.value)}
                      className="bg-white border-slate-200 text-xs text-slate-800 focus-visible:ring-blue-500 min-h-[140px]"
                    />
                  </div>

                  <div className="flex items-center justify-between gap-4 border-t border-slate-100 pt-4 flex-wrap">
                    {/* Generate trigger context */}
                    <div className="flex gap-2 max-w-md items-center">
                      <Input
                        placeholder="Context generation hints..."
                        value={contextHints}
                        onChange={(e) => setContextHints(e.target.value)}
                        className="bg-white border-slate-200 text-xs text-slate-800 h-9"
                      />
                      <Button
                        type="button"
                        onClick={handleGenerateContext}
                        disabled={isGeneratingContext}
                        variant="outline"
                        className="border-slate-200 hover:bg-slate-50 text-xs font-semibold flex items-center gap-1.5 h-9 flex-shrink-0"
                      >
                        {isGeneratingContext ? (
                          <RefreshCw size={13} className="animate-spin" />
                        ) : (
                          <Sparkles size={13} className="text-blue-500" />
                        )}
                        <span>Draft Context</span>
                      </Button>
                    </div>

                    <Button
                      type="button"
                      onClick={handleSaveContext}
                      disabled={isSavingContext}
                      className="bg-blue-600 hover:bg-blue-500 font-semibold text-xs h-9 px-4 flex items-center gap-1.5"
                    >
                      {isSavingContext ? (
                        <RefreshCw size={13} className="animate-spin" />
                      ) : (
                        <Save size={13} />
                      )}
                      <span>Save Context Config</span>
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )}
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
