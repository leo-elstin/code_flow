'use client';

import React from 'react';

interface POQuestionBubbleProps {
  questions: string[];
  timestamp?: string;
}

export default function POQuestionBubble({ questions, timestamp }: POQuestionBubbleProps) {
  return (
    <div className="flex justify-start px-4 py-1">
      <div className="max-w-[80%] rounded-2xl rounded-tl-sm bg-muted text-foreground px-4 py-3">
        <p className="text-[10px] font-semibold text-muted-foreground mb-2 uppercase tracking-wide">
          PO Agent
        </p>
        <p className="text-sm mb-2">I have a few questions to clarify the requirements:</p>
        <ol className="space-y-1.5">
          {questions.map((q, i) => (
            <li key={i} className="flex gap-2 text-sm">
              <span className="text-muted-foreground font-semibold shrink-0">{i + 1}.</span>
              <span>{q}</span>
            </li>
          ))}
        </ol>
        {timestamp && (
          <p className="text-[10px] text-muted-foreground mt-2">
            {new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </p>
        )}
      </div>
    </div>
  );
}
