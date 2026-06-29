'use client';

import React from 'react';

interface POMessageBubbleProps {
  role: 'po' | 'developer' | 'context';
  content: string;
  timestamp?: string;
}

export default function POMessageBubble({ role, content, timestamp }: POMessageBubbleProps) {
  const isPO = role === 'po';
  const isContext = role === 'context';

  if (isContext) {
    return (
      <div className="mx-4 my-3 p-3 rounded-lg bg-muted/40 border border-border">
        <p className="text-xs font-semibold text-muted-foreground mb-1 uppercase tracking-wide">Initial Context</p>
        <p className="text-sm text-foreground whitespace-pre-wrap">{content}</p>
      </div>
    );
  }

  return (
    <div className={`flex ${isPO ? 'justify-start' : 'justify-end'} px-4 py-1`}>
      <div
        className={`max-w-[75%] rounded-2xl px-4 py-2.5 ${
          isPO
            ? 'bg-muted text-foreground rounded-tl-sm'
            : 'bg-primary text-primary-foreground rounded-tr-sm'
        }`}
      >
        <p className={`text-[10px] font-semibold mb-1 ${isPO ? 'text-muted-foreground' : 'text-primary-foreground/70'}`}>
          {isPO ? 'PO Agent' : 'You'}
        </p>
        <p className="text-sm whitespace-pre-wrap">{content}</p>
        {timestamp && (
          <p className={`text-[10px] mt-1 ${isPO ? 'text-muted-foreground' : 'text-primary-foreground/60'}`}>
            {new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </p>
        )}
      </div>
    </div>
  );
}
