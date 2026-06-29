'use client';

import React from 'react';
import { Check, Loader2 } from 'lucide-react';

interface POProgressMessageProps {
  message: string;
  isActive?: boolean;
  isDone?: boolean;
}

export default function POProgressMessage({
  message,
  isActive = false,
  isDone = false,
}: POProgressMessageProps) {
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground px-4 py-1">
      {isActive ? (
        <Loader2 size={14} className="animate-spin shrink-0 text-blue-400" />
      ) : isDone ? (
        <Check size={14} className="shrink-0 text-green-500" />
      ) : (
        <span className="w-3.5 h-3.5 rounded-full border border-muted-foreground/40 shrink-0" />
      )}
      <span className={isActive ? 'text-foreground' : ''}>{message}</span>
    </div>
  );
}
