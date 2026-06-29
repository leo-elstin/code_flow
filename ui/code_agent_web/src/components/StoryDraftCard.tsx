'use client';

import React from 'react';
import { DraftStory } from '@/lib/po-api-client';

interface StoryDraftCardProps {
  story: DraftStory;
  included: boolean;
  onToggle: () => void;
}

const effortColors: Record<string, string> = {
  XS: 'text-green-600 border-green-600/40',
  S: 'text-green-500 border-green-500/40',
  M: 'text-yellow-500 border-yellow-500/40',
  L: 'text-orange-500 border-orange-500/40',
  XL: 'text-red-500 border-red-500/40',
};

const priorityColors: Record<string, string> = {
  critical: 'text-red-500 border-red-500/40',
  high: 'text-orange-500 border-orange-500/40',
  medium: 'text-yellow-500 border-yellow-500/40',
  low: 'text-muted-foreground border-border',
};

export default function StoryDraftCard({ story, included, onToggle }: StoryDraftCardProps) {
  const effortClass = effortColors[story.effort] || 'text-muted-foreground border-border';
  const priorityClass = priorityColors[story.priority?.toLowerCase()] || 'text-muted-foreground border-border';

  return (
    <div
      className={`border rounded-lg p-4 transition-opacity ${
        included ? 'border-border bg-card' : 'border-border/50 bg-card opacity-50'
      }`}
    >
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={included}
          onChange={onToggle}
          className="mt-1 h-4 w-4 rounded border-input accent-primary cursor-pointer shrink-0"
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            {story.epic && (
              <span className="text-xs text-muted-foreground font-medium">{story.epic}</span>
            )}
            {story.effort && (
              <span className={`text-xs border rounded px-1.5 py-0.5 font-medium ${effortClass}`}>
                {story.effort}
              </span>
            )}
            {story.priority && (
              <span className={`text-xs border rounded px-1.5 py-0.5 font-medium ${priorityClass}`}>
                {story.priority}
              </span>
            )}
          </div>
          <h4 className="font-semibold text-sm leading-snug">{story.title}</h4>
          {story.story && (
            <p className="text-sm text-muted-foreground mt-1 italic">{story.story}</p>
          )}
          {story.description && story.description !== story.story && (
            <p className="text-sm text-muted-foreground mt-1">{story.description}</p>
          )}
          {story.acceptance_criteria && story.acceptance_criteria.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-semibold text-muted-foreground mb-1.5 uppercase tracking-wide">
                Acceptance Criteria
              </p>
              <ul className="space-y-1">
                {story.acceptance_criteria.map((ac, i) => (
                  <li key={i} className="text-sm text-muted-foreground flex gap-1.5">
                    <span className="shrink-0">•</span>
                    <span>{ac}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {story.depends_on && story.depends_on.length > 0 && (
            <p className="text-xs text-muted-foreground mt-2">
              Depends on: {story.depends_on.join(', ')}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
