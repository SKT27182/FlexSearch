import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, MessageSquare, Plus, Trash2, FileText, Bug, Sparkles } from 'lucide-react';
import { Button, Card, CardContent, CardHeader, CardTitle, Input } from '@/components/ui';
import { Markdown } from '@/components/Markdown';
import {
  chatApi,
  suggestionsApi,
  type ChatCitation,
  type ChatSession,
  type ChatTurn,
  type Document,
} from '@/lib/api';
import { cn } from '@/lib/utils';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  citations?: ChatCitation[];
  streaming?: boolean;
}

interface DebugStageEvent {
  stage: string;
  duration_ms?: number;
  detail?: Record<string, unknown>;
  stages?: DebugStageEvent[];
  total_stage_ms?: number;
}

interface ProjectChatPanelProps {
  projectId: string;
  graphReady: boolean;
  graphStatusLabel?: string;
  documents: Document[];
  onPreviewDocument?: (doc: Document) => void;
}

export function ProjectChatPanel({
  projectId,
  graphReady,
  graphStatusLabel,
  documents,
  onPreviewDocument,
}: ProjectChatPanelProps) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [statusLabel, setStatusLabel] = useState('');
  const [citations, setCitations] = useState<ChatCitation[]>([]);
  const [strategyLabel, setStrategyLabel] = useState('');
  const [debugEvents, setDebugEvents] = useState<DebugStageEvent[]>([]);
  const [suggested, setSuggested] = useState<string[]>([]);
  const [followups, setFollowups] = useState<string[]>([]);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const loadSessions = useCallback(async () => {
    try {
      const list = await chatApi.listSessions(projectId);
      setSessions(list);
    } catch (error) {
      console.error('Failed to load chat sessions:', error);
    }
  }, [projectId]);

  const loadProjectSuggestions = useCallback(async () => {
    if (!graphReady) return;
    setLoadingSuggestions(true);
    try {
      const qs = await suggestionsApi.project(projectId, 5);
      setSuggested(qs);
    } catch (error) {
      console.error('Failed to load suggestions:', error);
    } finally {
      setLoadingSuggestions(false);
    }
  }, [projectId, graphReady]);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  useEffect(() => {
    if (messages.length === 0 && graphReady) {
      void loadProjectSuggestions();
    }
  }, [messages.length, graphReady, loadProjectSuggestions]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, citations, followups]);

  const loadSessionTurns = async (sessionId: string) => {
    setActiveSessionId(sessionId);
    setCitations([]);
    setStrategyLabel('');
    setDebugEvents([]);
    setFollowups([]);
    try {
      const turns = await chatApi.listTurns(sessionId);
      setMessages(turnsToMessages(turns));
      const lastAssistant = [...turns].reverse().find((t) => t.role === 'assistant');
      if (lastAssistant?.citations && Array.isArray(lastAssistant.citations)) {
        setCitations(lastAssistant.citations as ChatCitation[]);
      }
      if (lastAssistant?.retrieval_strategy) {
        setStrategyLabel(
          `${lastAssistant.retrieval_strategy} / ${lastAssistant.reranking_strategy || 'none'}`
        );
      }
    } catch (error) {
      console.error('Failed to load turns:', error);
    }
  };

  const startNewChat = () => {
    abortRef.current?.abort();
    setActiveSessionId(null);
    setMessages([]);
    setCitations([]);
    setStrategyLabel('');
    setStatusLabel('');
    setDebugEvents([]);
    setFollowups([]);
    setInput('');
    void loadProjectSuggestions();
  };

  const deleteSession = async (sessionId: string) => {
    if (!confirm('Delete this chat session?')) return;
    try {
      await chatApi.deleteSession(sessionId);
      if (activeSessionId === sessionId) startNewChat();
      await loadSessions();
    } catch (error) {
      console.error('Failed to delete session:', error);
    }
  };

  const findDocument = (documentId: string) =>
    documents.find((d) => d.id === documentId) ?? null;

  const sendQuestion = async (question: string) => {
    if (!question || isStreaming || !graphReady) return;

    setInput('');
    setIsStreaming(true);
    setStatusLabel('retrieve');
    setCitations([]);
    setDebugEvents([]);
    setFollowups([]);
    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: question,
    };
    const assistantId = `a-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      userMsg,
      { id: assistantId, role: 'assistant', content: '', streaming: true },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;
    let finalAnswer = '';

    try {
      await chatApi.stream(
        {
          project_id: projectId,
          query: question,
          session_id: activeSessionId,
          persist: true,
        },
        {
          signal: controller.signal,
          onSession: (sessionId) => setActiveSessionId(sessionId),
          onStatus: (stage) => setStatusLabel(stage),
          onCitations: (cites, meta) => {
            setCitations(cites);
            setStrategyLabel(`${meta.retrieval_strategy} / ${meta.reranking_strategy}`);
          },
          onToken: (token) => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, content: m.content + token } : m
              )
            );
          },
          onDebug: (payload) => {
            setDebugEvents((prev) => {
              if (payload.stage === 'summary' && Array.isArray(payload.stages)) {
                return payload.stages as DebugStageEvent[];
              }
              if (typeof payload.stage !== 'string') return prev;
              return [...prev, payload as unknown as DebugStageEvent];
            });
          },
          onDone: (payload) => {
            const answer = String(payload.answer || '');
            finalAnswer = answer;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      content: answer || m.content,
                      streaming: false,
                      citations: (payload.citations as ChatCitation[]) || citations,
                    }
                  : m
              )
            );
            if (Array.isArray(payload.citations)) {
              setCitations(payload.citations as ChatCitation[]);
            }
            const debug = payload.debug as { stages?: DebugStageEvent[] } | undefined;
            if (debug?.stages) {
              setDebugEvents(debug.stages);
            }
          },
          onPersisted: () => {
            void loadSessions();
          },
          onError: (detail) => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      content: m.content || `Error: ${detail}`,
                      streaming: false,
                    }
                  : m
              )
            );
          },
        }
      );

      // Follow-up chips after stream completes
      const answerText =
        finalAnswer ||
        (await new Promise<string>((resolve) => {
          setMessages((prev) => {
            const m = prev.find((x) => x.id === assistantId);
            resolve(m?.content || '');
            return prev;
          });
        }));
      if (answerText) {
        try {
          const chips = await suggestionsApi.followup(projectId, question, answerText, 3);
          setFollowups(chips);
        } catch (error) {
          console.error('Follow-up suggestions failed:', error);
        }
      }
    } catch (error) {
      if ((error as Error)?.name !== 'AbortError') {
        console.error('Chat stream failed:', error);
      }
    } finally {
      setIsStreaming(false);
      setStatusLabel('');
      abortRef.current = null;
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, streaming: false } : m))
      );
    }
  };

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    await sendQuestion(input.trim());
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[220px_1fr] gap-4 min-h-[520px]">
      <Card className="overflow-hidden">
        <CardHeader className="py-3 flex flex-row items-center justify-between space-y-0">
          <CardTitle className="text-sm">History</CardTitle>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={startNewChat}>
            <Plus className="h-4 w-4" />
          </Button>
        </CardHeader>
        <CardContent className="px-2 pb-3 space-y-1 max-h-[480px] overflow-y-auto">
          {sessions.length === 0 ? (
            <p className="text-xs text-muted-foreground px-2 py-4 text-center">
              No chats yet
            </p>
          ) : (
            sessions.map((s) => (
              <div
                key={s.id}
                className={cn(
                  'group flex items-center gap-1 rounded-md px-2 py-2 text-sm cursor-pointer hover:bg-secondary/60',
                  activeSessionId === s.id && 'bg-secondary'
                )}
                onClick={() => void loadSessionTurns(s.id)}
              >
                <MessageSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="flex-1 truncate text-xs">{s.title || 'Untitled'}</span>
                <button
                  type="button"
                  className="opacity-0 group-hover:opacity-100 p-1"
                  onClick={(e) => {
                    e.stopPropagation();
                    void deleteSession(s.id);
                  }}
                >
                  <Trash2 className="h-3 w-3 text-destructive" />
                </button>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <div className="flex flex-col gap-4 min-w-0">
        <Card className="flex-1 flex flex-col min-h-[360px]">
          <CardHeader className="py-3">
            <CardTitle className="text-lg flex items-center gap-2">
              <MessageSquare className="h-5 w-5" />
              Chat
            </CardTitle>
            {!graphReady && (
              <p className="text-sm text-muted-foreground">
                Chat is available once the index is ready.
                {graphStatusLabel ? ` Status: ${graphStatusLabel}.` : null}
              </p>
            )}
            {strategyLabel && (
              <p className="text-xs text-muted-foreground">Strategies: {strategyLabel}</p>
            )}
          </CardHeader>
          <CardContent className="flex-1 flex flex-col gap-3">
            <div className="flex-1 space-y-3 overflow-y-auto max-h-[360px] pr-1">
              {messages.length === 0 ? (
                <div className="py-8 space-y-4">
                  <p className="text-center text-muted-foreground text-sm">
                    Ask a question about your project documents
                  </p>
                  {(suggested.length > 0 || loadingSuggestions) && (
                    <div className="space-y-2">
                      <p className="text-[10px] uppercase font-semibold text-muted-foreground flex items-center gap-1 justify-center">
                        <Sparkles className="h-3 w-3" />
                        Suggested
                        {loadingSuggestions && (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        )}
                      </p>
                      <div className="flex flex-wrap gap-2 justify-center">
                        {suggested.map((q) => (
                          <button
                            key={q}
                            type="button"
                            disabled={!graphReady || isStreaming}
                            className="text-left text-xs rounded-md border border-border/60 px-3 py-2 hover:bg-secondary/50 max-w-full"
                            onClick={() => void sendQuestion(q)}
                          >
                            {q}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                messages.map((m) => (
                  <div
                    key={m.id}
                    className={cn(
                      'rounded-md px-3 py-2 text-sm',
                      m.role === 'user'
                        ? 'bg-primary/10 ml-8 whitespace-pre-wrap'
                        : 'bg-secondary/40 mr-4'
                    )}
                  >
                    <p className="text-[10px] uppercase font-semibold text-muted-foreground mb-1">
                      {m.role}
                      {m.streaming ? ' · typing…' : ''}
                    </p>
                    {m.role === 'assistant' ? (
                      m.content || m.streaming ? (
                        <Markdown content={m.content || (m.streaming ? '…' : '')} />
                      ) : null
                    ) : (
                      m.content
                    )}
                  </div>
                ))
              )}
              {followups.length > 0 && !isStreaming && (
                <div className="flex flex-wrap gap-2 pt-1">
                  {followups.map((q) => (
                    <button
                      key={q}
                      type="button"
                      className="text-xs rounded-md border border-primary/30 text-primary px-2.5 py-1.5 hover:bg-primary/10"
                      onClick={() => void sendQuestion(q)}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              )}
              {statusLabel && isStreaming && (
                <p className="text-xs text-primary flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  {statusLabel}
                </p>
              )}
              <div ref={bottomRef} />
            </div>

            <form onSubmit={handleSend} className="flex gap-2">
              <Input
                placeholder="Ask about your documents…"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={isStreaming || !graphReady}
              />
              <Button type="submit" disabled={isStreaming || !input.trim() || !graphReady}>
                {isStreaming ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Send'}
              </Button>
            </form>
          </CardContent>
        </Card>

        {citations.length > 0 && (
          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-sm">Sources</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {citations.map((c) => {
                const doc = findDocument(c.document_id);
                const tip = c.content?.slice(0, 400) || '';
                return (
                  <button
                    key={`${c.index}-${c.chunk_id}`}
                    type="button"
                    title={tip}
                    className="w-full text-left rounded-md border border-border/60 p-3 hover:bg-secondary/40 transition-colors group relative"
                    onClick={() => doc && onPreviewDocument?.(doc)}
                    disabled={!doc}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[10px] font-bold uppercase text-primary">
                        [{c.index}] {(c.score * 100).toFixed(0)}%
                      </span>
                      <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                        <FileText className="h-3 w-3" />
                        {c.filename || doc?.filename || c.document_id.slice(0, 8)}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground line-clamp-3">{c.content}</p>
                    <span className="pointer-events-none absolute left-3 right-3 bottom-full mb-1 z-20 hidden group-hover:block rounded border border-border bg-popover p-2 text-[11px] text-popover-foreground shadow-md max-h-40 overflow-y-auto whitespace-pre-wrap">
                      {tip}
                      {c.content && c.content.length > 400 ? '…' : ''}
                    </span>
                  </button>
                );
              })}
            </CardContent>
          </Card>
        )}

        {debugEvents.length > 0 && (
          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-sm flex items-center gap-2">
                <Bug className="h-4 w-4" />
                Pipeline debug
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-1">
              {debugEvents.map((ev, i) => (
                <div
                  key={`${ev.stage}-${i}`}
                  className="flex items-center justify-between text-xs font-mono rounded border border-border/40 px-2 py-1.5"
                >
                  <span className="text-muted-foreground">{ev.stage}</span>
                  <span className="font-semibold">
                    {typeof ev.duration_ms === 'number' ? `${ev.duration_ms} ms` : '—'}
                  </span>
                </div>
              ))}
              <p className="text-[10px] text-muted-foreground pt-1">
                Enable via project Settings → Chat quality → Show stage timings
              </p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

function turnsToMessages(turns: ChatTurn[]): ChatMessage[] {
  return turns.map((t) => ({
    id: t.id,
    role: t.role === 'assistant' ? 'assistant' : 'user',
    content: t.content,
    citations: Array.isArray(t.citations) ? (t.citations as ChatCitation[]) : undefined,
  }));
}
