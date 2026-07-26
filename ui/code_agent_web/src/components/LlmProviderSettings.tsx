'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { CodeAgentApiClient } from '@/lib/api-client';
import { LlmConfig, LlmProvider, ProjectSummary, UpdateLlmConfig } from '@/lib/models';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Bot, CheckCircle, XCircle, RefreshCw, Save, Plug, AlertCircle } from 'lucide-react';
import { toast } from 'sonner';

const PROVIDERS: { value: LlmProvider; label: string; hint: string; modelHint: string }[] = [
  {
    value: 'anthropic',
    label: 'Claude (Anthropic)',
    hint: 'Calls the Anthropic Messages API directly.',
    modelHint: 'claude-sonnet-5',
  },
  {
    value: 'openai_gateway',
    label: 'OpenAI via gateway',
    hint: 'Routes OpenAI-compatible calls through your own base URL.',
    modelHint: 'gpt-5.5',
  },
  {
    value: 'openai',
    label: 'OpenAI (direct)',
    hint: 'Calls api.openai.com directly with the official SDK.',
    modelHint: 'gpt-5.5',
  },
];

/** Local edit buffer — mirrors UpdateLlmConfig but with non-null strings for inputs. */
interface FormState {
  provider: LlmProvider;
  apiKey: string;
  baseUrl: string;
  chatModel: string;
  devModel: string;
  reasoningEffort: string;
  reasoningMode: string;
}

const EMPTY_FORM: FormState = {
  provider: 'openai_gateway',
  apiKey: '',
  baseUrl: '',
  chatModel: '',
  devModel: '',
  reasoningEffort: '',
  reasoningMode: '',
};

const REASONING_EFFORTS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];
const REASONING_MODES = ['standard', 'pro'];

function toForm(config: LlmConfig): FormState {
  return {
    provider: config.provider,
    // Never populate the key field — the API only ever sends a masked preview.
    apiKey: '',
    baseUrl: config.base_url || '',
    chatModel: config.chat_model || '',
    devModel: config.dev_model || '',
    reasoningEffort: config.reasoning_effort || '',
    reasoningMode: config.reasoning_mode || '',
  };
}

function toPayload(form: FormState): UpdateLlmConfig {
  return {
    provider: form.provider,
    // Undefined means "keep the stored key", so an untouched field is safe.
    api_key: form.apiKey.trim() ? form.apiKey.trim() : undefined,
    base_url: form.provider === 'openai_gateway' ? form.baseUrl.trim() || null : null,
    chat_model: form.chatModel.trim() || null,
    dev_model: form.devModel.trim() || null,
    // Anthropic has its own request shape already; reasoning effort/mode only
    // applies to the OpenAI Responses API path.
    reasoning_effort: form.provider === 'anthropic' ? null : form.reasoningEffort || null,
    reasoning_mode: form.provider === 'anthropic' ? null : form.reasoningMode || null,
  };
}

function providerMeta(provider: LlmProvider) {
  return PROVIDERS.find((p) => p.value === provider) || PROVIDERS[0];
}

interface ProviderFieldsProps {
  form: FormState;
  onChange: (next: FormState) => void;
  keyPlaceholder: string;
  idPrefix: string;
}

function ProviderFields({ form, onChange, keyPlaceholder, idPrefix }: ProviderFieldsProps) {
  const meta = providerMeta(form.provider);

  return (
    <div className="space-y-3.5">
      <div className="grid md:grid-cols-2 gap-3.5">
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor={`${idPrefix}-provider`}
            className="text-[10px] font-bold text-slate-500 tracking-wider uppercase"
          >
            Provider
          </label>
          <Select
            value={form.provider}
            onValueChange={(value) => onChange({ ...form, provider: value as LlmProvider })}
          >
            <SelectTrigger id={`${idPrefix}-provider`} className="bg-white border-slate-200 text-xs h-9">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PROVIDERS.map((p) => (
                <SelectItem key={p.value} value={p.value} className="text-xs">
                  {p.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="text-[10px] text-slate-400">{meta.hint}</span>
        </div>

        <div className="flex flex-col gap-1.5">
          <label
            htmlFor={`${idPrefix}-key`}
            className="text-[10px] font-bold text-slate-500 tracking-wider uppercase"
          >
            API Key
          </label>
          <Input
            id={`${idPrefix}-key`}
            type="password"
            autoComplete="off"
            placeholder={keyPlaceholder}
            value={form.apiKey}
            onChange={(e) => onChange({ ...form, apiKey: e.target.value })}
            className="bg-white border-slate-200 text-xs text-slate-800 h-9 font-mono"
          />
          <span className="text-[10px] text-slate-400">Leave blank to keep the saved key.</span>
        </div>
      </div>

      {form.provider === 'openai_gateway' && (
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor={`${idPrefix}-base-url`}
            className="text-[10px] font-bold text-slate-500 tracking-wider uppercase"
          >
            Gateway Base URL
          </label>
          <Input
            id={`${idPrefix}-base-url`}
            placeholder="https://your-gateway.example.com/v1"
            value={form.baseUrl}
            onChange={(e) => onChange({ ...form, baseUrl: e.target.value })}
            className="bg-white border-slate-200 text-xs text-slate-800 h-9 font-mono"
          />
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-3.5">
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor={`${idPrefix}-chat-model`}
            className="text-[10px] font-bold text-slate-500 tracking-wider uppercase"
          >
            Chat Model
          </label>
          <Input
            id={`${idPrefix}-chat-model`}
            placeholder={meta.modelHint}
            value={form.chatModel}
            onChange={(e) => onChange({ ...form, chatModel: e.target.value })}
            className="bg-white border-slate-200 text-xs text-slate-800 h-9 font-mono"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor={`${idPrefix}-dev-model`}
            className="text-[10px] font-bold text-slate-500 tracking-wider uppercase"
          >
            Dev Model (Optional)
          </label>
          <Input
            id={`${idPrefix}-dev-model`}
            placeholder="Defaults to the chat model"
            value={form.devModel}
            onChange={(e) => onChange({ ...form, devModel: e.target.value })}
            className="bg-white border-slate-200 text-xs text-slate-800 h-9 font-mono"
          />
        </div>
      </div>

      {form.provider !== 'anthropic' && (
        <div className="grid md:grid-cols-2 gap-3.5 border-t border-slate-100 pt-3.5">
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor={`${idPrefix}-reasoning-effort`}
              className="text-[10px] font-bold text-slate-500 tracking-wider uppercase"
            >
              Reasoning Effort (Optional)
            </label>
            <Select
              value={form.reasoningEffort || '__none__'}
              onValueChange={(value) =>
                onChange({ ...form, reasoningEffort: value === '__none__' ? '' : value })
              }
            >
              <SelectTrigger id={`${idPrefix}-reasoning-effort`} className="bg-white border-slate-200 text-xs h-9">
                <SelectValue placeholder="Chat Completions (default)" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__" className="text-xs">
                  Chat Completions (default)
                </SelectItem>
                {REASONING_EFFORTS.map((effort) => (
                  <SelectItem key={effort} value={effort} className="text-xs">
                    {effort}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-[10px] text-slate-400">
              Setting this routes calls through the Responses API — required for reasoning models
              that reject tool calls on Chat Completions.
            </span>
          </div>
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor={`${idPrefix}-reasoning-mode`}
              className="text-[10px] font-bold text-slate-500 tracking-wider uppercase"
            >
              Reasoning Mode (Optional)
            </label>
            <Select
              value={form.reasoningMode || '__none__'}
              onValueChange={(value) =>
                onChange({ ...form, reasoningMode: value === '__none__' ? '' : value })
              }
            >
              <SelectTrigger id={`${idPrefix}-reasoning-mode`} className="bg-white border-slate-200 text-xs h-9">
                <SelectValue placeholder="Standard" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__" className="text-xs">
                  Standard
                </SelectItem>
                {REASONING_MODES.map((mode) => (
                  <SelectItem key={mode} value={mode} className="text-xs">
                    {mode}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-[10px] text-slate-400">
              Pro trades latency and tokens for more thorough reasoning on hard tasks.
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

interface LlmProviderSettingsProps {
  selectedProject: ProjectSummary | null;
}

export default function LlmProviderSettings({ selectedProject }: LlmProviderSettingsProps) {
  const [globalConfig, setGlobalConfig] = useState<LlmConfig | null>(null);
  const [globalForm, setGlobalForm] = useState<FormState>(EMPTY_FORM);
  const [isSavingGlobal, setIsSavingGlobal] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  const [projectConfig, setProjectConfig] = useState<LlmConfig | null>(null);
  const [projectForm, setProjectForm] = useState<FormState>(EMPTY_FORM);
  const [hasOverride, setHasOverride] = useState(false);
  const [isSavingProject, setIsSavingProject] = useState(false);

  const loadGlobal = useCallback(async () => {
    try {
      const config = await CodeAgentApiClient.getLlmConfig();
      setGlobalConfig(config);
      setGlobalForm(toForm(config));
    } catch (err: any) {
      toast.error(err.message || 'Failed to load LLM configuration');
    }
  }, []);

  const loadProject = useCallback(async (projectId: number) => {
    try {
      const config = await CodeAgentApiClient.getProjectLlmConfig(projectId);
      setProjectConfig(config);
      setProjectForm(toForm(config));
      setHasOverride(Boolean(config.has_override));
    } catch (err: any) {
      toast.error(err.message || 'Failed to load project LLM override');
    }
  }, []);

  useEffect(() => {
    loadGlobal();
  }, [loadGlobal]);

  useEffect(() => {
    if (selectedProject) {
      loadProject(selectedProject.id);
    } else {
      setProjectConfig(null);
      setHasOverride(false);
    }
  }, [selectedProject, loadProject]);

  const handleSaveGlobal = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSavingGlobal(true);
    try {
      const saved = await CodeAgentApiClient.updateLlmConfig(toPayload(globalForm));
      setGlobalConfig(saved);
      setGlobalForm(toForm(saved));
      setTestResult(null);
      toast.success('LLM provider updated — new runs will use it immediately');
      // The project card shows inherited values when no override is set.
      if (selectedProject && !hasOverride) loadProject(selectedProject.id);
    } catch (err: any) {
      toast.error(err.message || 'Failed to save LLM configuration');
    } finally {
      setIsSavingGlobal(false);
    }
  };

  const handleTest = async () => {
    setIsTesting(true);
    setTestResult(null);
    try {
      const result = await CodeAgentApiClient.testLlmConfig(toPayload(globalForm));
      setTestResult({
        ok: result.ok,
        message: result.ok
          ? `Connected to ${result.model || 'the model'}`
          : result.error || 'Connection failed',
      });
    } catch (err: any) {
      setTestResult({ ok: false, message: err.message || 'Connection failed' });
    } finally {
      setIsTesting(false);
    }
  };

  const handleSaveProject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedProject) return;
    setIsSavingProject(true);
    try {
      const saved = await CodeAgentApiClient.updateProjectLlmConfig(
        selectedProject.id,
        hasOverride ? toPayload(projectForm) : { provider: null }
      );
      setProjectConfig(saved);
      setProjectForm(toForm(saved));
      setHasOverride(Boolean(saved.has_override));
      toast.success(
        saved.has_override
          ? `Override saved for ${selectedProject.name}`
          : `${selectedProject.name} now uses the global provider`
      );
    } catch (err: any) {
      toast.error(err.message || 'Failed to save project override');
    } finally {
      setIsSavingProject(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Global config */}
      <Card className="border-slate-200/80 shadow-xs rounded-xl">
        <CardHeader>
          <div className="flex items-start justify-between gap-4">
            <div>
              <CardTitle className="text-sm font-bold text-slate-800 flex items-center gap-2">
                <Bot size={16} className="text-blue-500" />
                <span>Global LLM Provider</span>
              </CardTitle>
              <CardDescription className="text-[11px] text-slate-400 leading-normal">
                The model and credential every agent run uses. Saved changes apply to the next run
                — no backend restart needed.
              </CardDescription>
            </div>
            {globalConfig?.key_configured ? (
              <Badge className="bg-emerald-500/10 border-emerald-500/20 text-emerald-500 font-bold hover:bg-emerald-500/10 flex items-center gap-1 rounded">
                <CheckCircle size={10} />
                <span>Key Set</span>
              </Badge>
            ) : (
              <Badge
                variant="outline"
                className="bg-rose-50 border-rose-200 text-rose-500 font-bold hover:bg-rose-50 flex items-center gap-1 rounded"
              >
                <XCircle size={10} />
                <span>No Key</span>
              </Badge>
            )}
          </div>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSaveGlobal} className="space-y-4">
            {globalConfig?.from_env && (
              <div className="bg-slate-50 border border-slate-200 p-3 rounded-lg flex gap-2.5">
                <AlertCircle className="text-amber-500 shrink-0 mt-0.5" size={15} />
                <p className="text-[10px] text-slate-500 leading-normal">
                  Currently reading from the backend&apos;s{' '}
                  <code className="text-slate-700 bg-slate-100 px-1 rounded font-mono">.env</code>{' '}
                  file. Saving here stores the configuration in the database and takes over from{' '}
                  <code className="text-slate-700 bg-slate-100 px-1 rounded font-mono">.env</code>.
                </p>
              </div>
            )}

            <ProviderFields
              form={globalForm}
              onChange={setGlobalForm}
              keyPlaceholder={globalConfig?.api_key_preview || 'Paste your API key'}
              idPrefix="global"
            />

            <div className="border-t border-slate-100 pt-4 flex items-center justify-between gap-4 flex-wrap">
              <div className="flex items-center gap-2.5">
                <Button
                  type="button"
                  onClick={handleTest}
                  disabled={isTesting}
                  variant="outline"
                  className="border-slate-200 hover:bg-slate-50 text-xs font-semibold flex items-center gap-1.5 h-9"
                >
                  {isTesting ? (
                    <RefreshCw size={13} className="animate-spin" />
                  ) : (
                    <Plug size={13} className="text-blue-500" />
                  )}
                  <span>Test Connection</span>
                </Button>

                {testResult && (
                  <Badge
                    variant="outline"
                    className={
                      testResult.ok
                        ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 font-bold hover:bg-emerald-500/10 flex items-center gap-1 rounded max-w-md'
                        : 'bg-rose-50 border-rose-200 text-rose-500 font-bold hover:bg-rose-50 flex items-center gap-1 rounded max-w-md'
                    }
                  >
                    {testResult.ok ? <CheckCircle size={10} /> : <XCircle size={10} />}
                    <span className="truncate">{testResult.message}</span>
                  </Badge>
                )}
              </div>

              <Button
                type="submit"
                disabled={isSavingGlobal}
                className="bg-blue-600 hover:bg-blue-500 font-semibold text-xs h-9 px-4 flex items-center gap-1.5"
              >
                {isSavingGlobal ? (
                  <RefreshCw size={13} className="animate-spin" />
                ) : (
                  <Save size={13} />
                )}
                <span>Save Provider</span>
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {/* Per-project override */}
      {selectedProject ? (
        <Card className="border-slate-200/80 shadow-xs rounded-xl">
          <CardHeader>
            <div className="flex items-start justify-between gap-4">
              <div>
                <CardTitle className="text-xs font-bold text-slate-400 tracking-wider uppercase">
                  Project Override — {selectedProject.name}
                </CardTitle>
                <CardDescription className="text-[10px] text-slate-400 leading-normal">
                  Run this project on a different provider or model than the global default.
                </CardDescription>
              </div>
              <Badge
                variant="outline"
                className={
                  hasOverride
                    ? 'bg-blue-500/10 border-blue-500/20 text-blue-600 font-bold hover:bg-blue-500/10 rounded'
                    : 'bg-slate-100 border-slate-200 text-slate-500 font-bold hover:bg-slate-100 rounded'
                }
              >
                {hasOverride ? 'Overridden' : 'Inheriting Global'}
              </Badge>
            </div>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSaveProject} className="space-y-4">
              <label className="flex items-center gap-2.5 cursor-pointer w-fit">
                <input
                  type="checkbox"
                  checked={hasOverride}
                  onChange={(e) => setHasOverride(e.target.checked)}
                  className="accent-blue-600 w-3.5 h-3.5"
                />
                <span className="text-xs font-medium text-slate-600">
                  Use a different provider for this project
                </span>
              </label>

              {hasOverride ? (
                <ProviderFields
                  form={projectForm}
                  onChange={setProjectForm}
                  keyPlaceholder={
                    projectConfig?.has_override && projectConfig.api_key_preview
                      ? projectConfig.api_key_preview
                      : 'Blank inherits the global key when the provider matches'
                  }
                  idPrefix="project"
                />
              ) : (
                <p className="text-[11px] text-slate-500 bg-slate-50 border border-slate-200 rounded-lg p-3">
                  Using the global provider:{' '}
                  <strong className="text-slate-700">
                    {providerMeta(globalForm.provider).label}
                  </strong>
                  {globalConfig?.chat_model ? ` · ${globalConfig.chat_model}` : ''}
                </p>
              )}

              <div className="border-t border-slate-100 pt-4 flex justify-end">
                <Button
                  type="submit"
                  disabled={isSavingProject}
                  className="bg-blue-600 hover:bg-blue-500 font-semibold text-xs h-9 px-4 flex items-center gap-1.5"
                >
                  {isSavingProject ? (
                    <RefreshCw size={13} className="animate-spin" />
                  ) : (
                    <Save size={13} />
                  )}
                  <span>Save Override</span>
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      ) : (
        <div className="p-8 text-center text-xs text-slate-400 bg-slate-100/50 border border-slate-200 rounded-xl">
          Select a project in the sidebar to set a per-project provider override.
        </div>
      )}
    </div>
  );
}
