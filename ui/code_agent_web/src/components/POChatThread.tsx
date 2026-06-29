'use client';

import React, { useEffect, useRef } from 'react';
import { POSessionStatus } from '@/lib/po-api-client';
import POMessageBubble from './POMessageBubble';
import POQuestionBubble from './POQuestionBubble';
import POProgressMessage from './POProgressMessage';

export interface ChatMessage {
  id?: string;
  role: 'po' | 'developer' | 'context' | 'progress';
  content: string;
  questions?: string[];
  timestamp?: string;
  isActive?: boolean;
  isDone?: boolean;
}

interface POChatThreadProps {
  messages: ChatMessage[];
  status?: POSessionStatus;
}

const STATUS_PROGRESS_LABELS: Partial<Record<POSessionStatus, string>> = {
  classifying: 'Classifying request...',
  brainstorming: 'Analyzing context...',
  researching: 'Researching codebase...',
  drafting: 'Drafting product brief...',
};

export default function POChatThread({ messages, status }: POChatThreadProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, status]);

  const showProgressBar =
    status && STATUS_PROGRESS_LABELS[status] != null;

  return (
    <div className="flex-1 overflow-y-auto py-4 space-y-1">
      {messages.map((msg, idx) => {
        if (msg.role === 'progress') {
          return (
            <POProgressMessage
              key={msg.id || idx}
              message={msg.content}
              isActive={msg.isActive}
              isDone={msg.isDone}
            />
          );
        }
        if (msg.role === 'context') {
          return (
            <POMessageBubble
              key={msg.id || idx}
              role="context"
              content={msg.content}
              timestamp={msg.timestamp}
            />
          );
        }
        if (msg.role === 'po' && msg.questions && msg.questions.length > 0) {
          return (
            <POQuestionBubble
              key={msg.id || idx}
              questions={msg.questions}
              timestamp={msg.timestamp}
            />
          );
        }
        return (
          <POMessageBubble
            key={msg.id || idx}
            role={msg.role as 'po' | 'developer'}
            content={msg.content}
            timestamp={msg.timestamp}
          />
        );
      })}

      {showProgressBar && (
        <POProgressMessage
          message={STATUS_PROGRESS_LABELS[status!]!}
          isActive
        />
      )}

      <div ref={bottomRef} />
    </div>
  );
}
