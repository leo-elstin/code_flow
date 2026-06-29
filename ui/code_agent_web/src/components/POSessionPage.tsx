'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import {
  POSession,
  POSessionStatus,
  getPOSession,
  replyToPOSession,
  finalizePOSession,
  approvePOSession,
  rejectPOSession,
  subscribeToPOSession,
} from '@/lib/po-api-client';
import POChatThread, { ChatMessage } from './POChatThread';
import POReplyInput from './POReplyInput';
import StoryReviewPanel from './StoryReviewPanel';
import { ArrowLeft } from 'lucide-react';

interface POSessionPageProps {
  sessionId: string;
}

const STATUS_LABELS: Partial<Record<POSessionStatus, string>> = {
  classifying: 'Classifying request...',
  brainstorming: 'Analyzing context...',
  waiting_for_reply: 'Waiting for your reply',
  researching: 'Researching codebase...',
  drafting: 'Drafting brief & stories...',
  awaiting_review: 'Ready for review',
  approved: 'Approved',
  rejected: 'Rejected',
  failed: 'Failed',
};

const TERMINAL_STATUSES: POSessionStatus[] = ['awaiting_review', 'approved', 'rejected', 'failed'];
const PROCESSING_STATUSES: POSessionStatus[] = ['classifying', 'brainstorming', 'researching', 'drafting'];

function buildMessages(session: POSession): ChatMessage[] {
  const messages: ChatMessage[] = [];

  // Add initial context as a context bubble
  if (session.initial_context) {
    messages.push({
      id: 'ctx-0',
      role: 'context',
      content: session.initial_context,
    });
  }

  // Build from conversation if session has messages embedded
  // The API returns sessions without full conversation history
  // — we rely on the messages being reconstructed from DB via polling
  return messages;
}

export default function POSessionPage({ sessionId }: POSessionPageProps) {
  const router = useRouter();
  const [session, setSession] = useState<POSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);

  const applySessionUpdate = useCallback((updated: POSession) => {
    setSession(updated);
    // Build messages from session data
    const newMessages: ChatMessage[] = [];

    if (updated.initial_context) {
      newMessages.push({
        id: 'ctx-0',
        role: 'context',
        content: updated.initial_context,
      });
    }

    // Add pending questions as PO bubble if waiting
    if (
      updated.status === 'waiting_for_reply' &&
      updated.pending_questions &&
      updated.pending_questions.length > 0
    ) {
      newMessages.push({
        id: 'questions-latest',
        role: 'po',
        content: updated.pending_questions.join('\n'),
        questions: updated.pending_questions,
      });
    }

    setMessages(newMessages);
  }, []);

  useEffect(() => {
    let unsubscribe: (() => void) | null = null;

    const init = async () => {
      try {
        const initial = await getPOSession(sessionId);
        applySessionUpdate(initial);
        setLoading(false);

        if (!TERMINAL_STATUSES.includes(initial.status)) {
          unsubscribe = subscribeToPOSession(sessionId, (updated) => {
            applySessionUpdate(updated);
          });
        }
      } catch (err: any) {
        setError(err.message || 'Failed to load session');
        setLoading(false);
      }
    };

    init();

    return () => {
      if (unsubscribe) unsubscribe();
    };
  }, [sessionId, applySessionUpdate]);

  const handleReply = async (text: string) => {
    if (!session || isSending) return;
    setIsSending(true);
    // Optimistically add developer message
    setMessages((prev) => [
      ...prev,
      {
        id: `dev-${Date.now()}`,
        role: 'developer',
        content: text,
        timestamp: new Date().toISOString(),
      },
    ]);
    try {
      const updated = await replyToPOSession(sessionId, text);
      applySessionUpdate(updated);
      // Resume polling if not terminal
      if (!TERMINAL_STATUSES.includes(updated.status)) {
        subscribeToPOSession(sessionId, applySessionUpdate);
      }
    } catch (err: any) {
      setError(err.message || 'Failed to send reply');
    } finally {
      setIsSending(false);
    }
  };

  const handleFinalize = async () => {
    if (!session) return;
    try {
      const updated = await finalizePOSession(sessionId);
      applySessionUpdate(updated);
      if (!TERMINAL_STATUSES.includes(updated.status)) {
        subscribeToPOSession(sessionId, applySessionUpdate);
      }
    } catch (err: any) {
      setError(err.message || 'Failed to finalize');
    }
  };

  const handleApprove = async (storyIds: string[]) => {
    if (!session) return;
    try {
      const updated = await approvePOSession(sessionId, storyIds);
      applySessionUpdate(updated);
    } catch (err: any) {
      setError(err.message || 'Failed to approve');
    }
  };

  const handleReject = async () => {
    if (!session) return;
    try {
      const updated = await rejectPOSession(sessionId);
      applySessionUpdate(updated);
    } catch (err: any) {
      setError(err.message || 'Failed to reject');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-background">
        <p className="text-muted-foreground text-sm">Loading session...</p>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="flex items-center justify-center h-screen bg-background">
        <p className="text-muted-foreground text-sm">{error || 'Session not found'}</p>
      </div>
    );
  }

  const statusLabel = STATUS_LABELS[session.status] || session.status;
  const isProcessing = PROCESSING_STATUSES.includes(session.status);
  const isWaitingForReply = session.status === 'waiting_for_reply';
  const isAwaitingReview = session.status === 'awaiting_review';
  const isTerminal = ['approved', 'rejected', 'failed'].includes(session.status);

  return (
    <div className="flex flex-col h-screen bg-background">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b bg-background shrink-0">
        <button
          onClick={() => router.back()}
          className="text-muted-foreground hover:text-foreground transition-colors p-1"
          title="Back"
        >
          <ArrowLeft size={18} />
        </button>
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-sm truncate">{session.initial_context}</p>
          <div className="flex items-center gap-2 mt-0.5">
            <span
              className={`text-xs ${
                session.status === 'failed'
                  ? 'text-destructive'
                  : session.status === 'approved'
                  ? 'text-green-500'
                  : 'text-muted-foreground'
              }`}
            >
              {statusLabel}
            </span>
            {session.readiness_score > 0 && (
              <span className="text-xs text-muted-foreground">
                • {Math.round(session.readiness_score * 100)}% ready
              </span>
            )}
          </div>
        </div>
        {!isTerminal && !isAwaitingReview && (
          <button
            onClick={handleFinalize}
            className="text-xs text-muted-foreground hover:text-foreground underline transition-colors shrink-0"
          >
            Finalize Early
          </button>
        )}
        {!isTerminal && (
          <button
            onClick={handleReject}
            className="text-xs text-destructive hover:text-destructive/80 transition-colors shrink-0"
          >
            Reject
          </button>
        )}
      </div>

      {error && (
        <div className="px-4 py-2 bg-destructive/10 border-b border-destructive/20 text-xs text-destructive">
          {error}
        </div>
      )}

      {/* Main content */}
      {isAwaitingReview || session.status === 'approved' || session.status === 'rejected' ? (
        <div className="flex-1 overflow-hidden">
          {session.status === 'approved' || session.status === 'rejected' ? (
            <div className="flex items-center justify-center h-full">
              <div className="text-center">
                <p className={`text-lg font-semibold ${session.status === 'approved' ? 'text-green-500' : 'text-muted-foreground'}`}>
                  Session {session.status === 'approved' ? 'Approved' : 'Rejected'}
                </p>
                <p className="text-sm text-muted-foreground mt-1">
                  {session.status === 'approved'
                    ? 'Stories have been approved for development.'
                    : 'This session was rejected.'}
                </p>
              </div>
            </div>
          ) : (
            <StoryReviewPanel
              session={session}
              onApprove={handleApprove}
              onReject={handleReject}
            />
          )}
        </div>
      ) : (
        <>
          {/* Chat thread */}
          <POChatThread messages={messages} status={session.status} />

          {/* Bottom input */}
          <POReplyInput
            disabled={!isWaitingForReply || isSending}
            statusLabel={isProcessing ? statusLabel : isSending ? 'Sending...' : statusLabel}
            onSend={handleReply}
            onFinalize={!isTerminal ? handleFinalize : undefined}
          />
        </>
      )}
    </div>
  );
}
