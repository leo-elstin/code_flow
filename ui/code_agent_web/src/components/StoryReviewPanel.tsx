'use client';

import React, { useState } from 'react';
import { POSession, DraftStory } from '@/lib/po-api-client';
import { Button } from '@/components/ui/button';
import StoryDraftCard from './StoryDraftCard';
import { CheckCheck, X } from 'lucide-react';

interface StoryReviewPanelProps {
  session: POSession;
  onApprove: (storyIds: string[]) => void;
  onReject: () => void;
}

export default function StoryReviewPanel({
  session,
  onApprove,
  onReject,
}: StoryReviewPanelProps) {
  const stories = session.draft_stories ?? [];
  const [selected, setSelected] = useState<Set<string>>(
    new Set(stories.map((s) => s.id))
  );

  const toggleStory = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  return (
    <div className="flex flex-col h-full">
      {/* Brief summary header */}
      {session.brief && (
        <div className="px-4 py-3 border-b bg-muted/20 shrink-0">
          <h3 className="font-semibold text-sm">Product Brief</h3>
          {session.brief.summary && (
            <p className="text-sm text-muted-foreground mt-0.5">{session.brief.summary}</p>
          )}
          {session.brief.approach && (
            <p className="text-xs text-muted-foreground mt-1 italic">{session.brief.approach}</p>
          )}
        </div>
      )}

      {/* Story cards — scrollable */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">
          Draft Stories ({stories.length})
        </p>
        {stories.length === 0 ? (
          <p className="text-sm text-muted-foreground italic">No stories generated.</p>
        ) : (
          stories.map((story) => (
            <StoryDraftCard
              key={story.id}
              story={story}
              included={selected.has(story.id)}
              onToggle={() => toggleStory(story.id)}
            />
          ))
        )}
      </div>

      {/* Action bar */}
      <div className="border-t p-4 flex items-center justify-between shrink-0 bg-background">
        <Button
          variant="outline"
          size="sm"
          onClick={onReject}
          className="text-destructive border-destructive/40 hover:bg-destructive/10 gap-1.5"
        >
          <X size={14} />
          Reject
        </Button>
        <div className="flex items-center gap-2">
          <p className="text-xs text-muted-foreground">
            {selected.size} of {stories.length} selected
          </p>
          <Button
            size="sm"
            onClick={() => onApprove([...selected])}
            disabled={selected.size === 0}
            className="gap-1.5"
          >
            <CheckCheck size={14} />
            Approve {selected.size > 0 ? `(${selected.size})` : ''}
          </Button>
        </div>
      </div>
    </div>
  );
}
