'use client';

import React, { useEffect, useState } from 'react';
import { CodeAgentApiClient } from '@/lib/api-client';
import { DevEngine, ProjectSummary } from '@/lib/models';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Cpu, RefreshCw, Save } from 'lucide-react';
import { toast } from 'sonner';

const ENGINES: { value: DevEngine; label: string; hint: string }[] = [
  {
    value: 'api',
    label: 'API loop (default)',
    hint: 'The hand-rolled tool-calling loop — battle-tested, uses the LLM provider configured above.',
  },
  {
    value: 'claude_code_cli',
    label: 'Claude Code CLI (pilot)',
    hint: 'Shells out to the Claude Code CLI already installed on this machine for its own agent loop and context management. Only the dev node is affected — planner, verifier, and QA are unchanged.',
  },
];

interface DevEngineSettingsProps {
  selectedProject: ProjectSummary | null;
}

export default function DevEngineSettings({ selectedProject }: DevEngineSettingsProps) {
  const [engine, setEngine] = useState<DevEngine>('api');
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    setEngine(selectedProject?.dev_engine || 'api');
  }, [selectedProject]);

  if (!selectedProject) {
    return (
      <div className="p-8 text-center text-xs text-slate-400 bg-slate-100/50 border border-slate-200 rounded-xl">
        Select a project in the sidebar to choose its dev engine.
      </div>
    );
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSaving(true);
    try {
      const saved = await CodeAgentApiClient.updateProjectDevEngine(selectedProject.id, engine);
      setEngine(saved.dev_engine);
      toast.success(`Dev engine for ${selectedProject.name} set to ${saved.dev_engine}`);
    } catch (err: any) {
      toast.error(err.message || 'Failed to save dev engine');
    } finally {
      setIsSaving(false);
    }
  };

  const meta = ENGINES.find((e) => e.value === engine) || ENGINES[0];

  return (
    <Card className="border-slate-200/80 shadow-xs rounded-xl">
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle className="text-xs font-bold text-slate-400 tracking-wider uppercase flex items-center gap-2">
              <Cpu size={14} className="text-blue-500" />
              <span>Dev Engine — {selectedProject.name}</span>
            </CardTitle>
            <CardDescription className="text-[10px] text-slate-400 leading-normal">
              Which engine runs the dev node for this project. Per-project, so you can pilot the
              CLI engine on one project while everything else keeps running the current loop.
            </CardDescription>
          </div>
          <Badge
            variant="outline"
            className={
              engine === 'claude_code_cli'
                ? 'bg-blue-500/10 border-blue-500/20 text-blue-600 font-bold hover:bg-blue-500/10 rounded'
                : 'bg-slate-100 border-slate-200 text-slate-500 font-bold hover:bg-slate-100 rounded'
            }
          >
            {meta.label}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSave} className="space-y-3.5">
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="dev-engine-select"
              className="text-[10px] font-bold text-slate-500 tracking-wider uppercase"
            >
              Engine
            </label>
            <Select value={engine} onValueChange={(value) => setEngine(value as DevEngine)}>
              <SelectTrigger id="dev-engine-select" className="bg-white border-slate-200 text-xs h-9">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ENGINES.map((e) => (
                  <SelectItem key={e.value} value={e.value} className="text-xs">
                    {e.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-[10px] text-slate-400">{meta.hint}</span>
          </div>

          <div className="border-t border-slate-100 pt-4 flex justify-end">
            <Button
              type="submit"
              disabled={isSaving}
              className="bg-blue-600 hover:bg-blue-500 font-semibold text-xs h-9 px-4 flex items-center gap-1.5"
            >
              {isSaving ? <RefreshCw size={13} className="animate-spin" /> : <Save size={13} />}
              <span>Save Engine</span>
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
