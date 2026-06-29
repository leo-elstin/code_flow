'use client';

import React, { useState, useRef, KeyboardEvent } from 'react';
import { Send } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface POReplyInputProps {
  disabled?: boolean;
  statusLabel?: string;
  onSend: (text: string) => void;
  onFinalize?: () => void;
}

export default function POReplyInput({
  disabled = false,
  statusLabel,
  onSend,
  onFinalize,
}: POReplyInputProps) {
  const [text, setText] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    // Auto-resize
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 160)}px`;
    }
  };

  return (
    <div className="border-t p-4 bg-background">
      <div className="flex gap-2 items-end">
        <textarea
          ref={textareaRef}
          rows={1}
          disabled={disabled}
          placeholder={
            disabled
              ? statusLabel || 'Waiting...'
              : 'Type your answer... (Enter to send, Shift+Enter for new line)'
          }
          value={text}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          className="flex-1 resize-none rounded-xl border border-input bg-background px-3 py-2.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50 disabled:cursor-not-allowed min-h-[42px] max-h-40 overflow-y-auto"
        />
        <Button
          onClick={handleSend}
          disabled={disabled || !text.trim()}
          size="sm"
          className="shrink-0 h-[42px] px-3"
        >
          <Send size={16} />
        </Button>
      </div>
      {onFinalize && (
        <div className="flex justify-start mt-2">
          <button
            onClick={onFinalize}
            className="text-xs text-muted-foreground underline hover:text-foreground transition-colors"
          >
            Finalize Early (generate brief now)
          </button>
        </div>
      )}
    </div>
  );
}
