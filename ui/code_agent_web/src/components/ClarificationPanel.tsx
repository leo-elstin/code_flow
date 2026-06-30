'use client';

import React, { useState } from 'react';
import { ClarificationQuestion, ClarifyAnswer } from '@/lib/models';
import { Button } from '@/components/ui/button';
import { HelpCircle, ChevronRight } from 'lucide-react';

interface ClarificationPanelProps {
  questions: ClarificationQuestion[];
  isBusy: boolean;
  onSubmit: (answers: ClarifyAnswer[]) => Promise<void>;
  onSkip: () => void;
}

export default function ClarificationPanel({
  questions,
  isBusy,
  onSubmit,
  onSkip,
}: ClarificationPanelProps) {
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const allAnswered = questions.every(q => selected[q.id]);

  const handleSubmit = async () => {
    if (!allAnswered || submitting) return;
    setSubmitting(true);
    setError('');
    try {
      const answers: ClarifyAnswer[] = questions.map(q => {
        const optId = selected[q.id];
        const opt = q.options.find(o => o.id === optId)!;
        return {
          question_id: q.id,
          question: q.question,
          option_id: optId,
          option_label: opt.label,
          option_description: opt.description,
        };
      });
      await onSubmit(answers);
    } catch (err: any) {
      setError(err.message || 'Failed to submit answers');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Header */}
      <div className="flex items-start gap-3">
        <div className="w-8 h-8 rounded-full bg-amber-100 flex items-center justify-center flex-shrink-0 mt-0.5">
          <HelpCircle size={16} className="text-amber-600" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-slate-800">Clarification needed</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            The planner found {questions.length === 1 ? 'a design decision' : `${questions.length} design decisions`} that
            need your input before it can produce a final plan.
          </p>
        </div>
      </div>

      {/* Questions */}
      {questions.map((q, qi) => (
        <div key={q.id} className="rounded-lg border border-slate-200 bg-white overflow-hidden">
          {/* Question header */}
          <div className="px-4 py-3 border-b border-slate-100 bg-slate-50">
            <div className="flex items-start gap-2">
              <span className="text-[10px] font-mono font-bold text-slate-400 mt-0.5 flex-shrink-0">
                Q{qi + 1}
              </span>
              <p className="text-xs font-semibold text-slate-800 leading-relaxed">{q.question}</p>
            </div>
            {q.context && (
              <p className="text-[11px] text-slate-500 mt-1.5 leading-relaxed pl-5">{q.context}</p>
            )}
          </div>

          {/* Options */}
          <div className="divide-y divide-slate-100">
            {q.options.map(opt => {
              const isSelected = selected[q.id] === opt.id;
              return (
                <button
                  key={opt.id}
                  onClick={() => setSelected(prev => ({ ...prev, [q.id]: opt.id }))}
                  className={`w-full text-left px-4 py-3 flex items-start gap-3 transition-colors ${
                    isSelected
                      ? 'bg-blue-50 hover:bg-blue-50'
                      : 'hover:bg-slate-50'
                  }`}
                >
                  {/* Option badge */}
                  <span
                    className={`text-[10px] font-bold w-5 h-5 rounded flex-shrink-0 flex items-center justify-center mt-0.5 border ${
                      isSelected
                        ? 'bg-blue-600 text-white border-blue-600'
                        : 'bg-white text-slate-500 border-slate-300'
                    }`}
                  >
                    {opt.id}
                  </span>
                  <div className="min-w-0">
                    <span
                      className={`text-xs font-semibold block ${
                        isSelected ? 'text-blue-700' : 'text-slate-700'
                      }`}
                    >
                      {opt.label}
                    </span>
                    {opt.description && (
                      <span className="text-[11px] text-slate-500 leading-relaxed block mt-0.5">
                        {opt.description}
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      ))}

      {/* Error */}
      {error && (
        <p className="text-xs text-red-600 bg-red-50 rounded p-2 border border-red-200">{error}</p>
      )}

      {/* Actions */}
      <div className="flex items-center justify-between pt-1">
        <button
          onClick={onSkip}
          disabled={isBusy || submitting}
          className="text-xs text-slate-400 hover:text-slate-600 transition-colors disabled:opacity-50"
        >
          Skip — let planner decide
        </button>
        <Button
          onClick={handleSubmit}
          disabled={!allAnswered || isBusy || submitting}
          size="sm"
          className="gap-1.5 text-xs"
        >
          {submitting ? 'Planning…' : 'Continue'}
          <ChevronRight size={13} />
        </Button>
      </div>

      {/* Progress hint */}
      {questions.length > 1 && (
        <p className="text-[10px] text-slate-400 text-center -mt-3">
          {Object.keys(selected).length} of {questions.length} answered
        </p>
      )}
    </div>
  );
}
