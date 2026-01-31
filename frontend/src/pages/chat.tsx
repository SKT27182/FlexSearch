import { useState, useEffect, useRef, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Send, Loader2, FolderOpen, Plus, MessageSquare, Trash2, FileText, ChevronDown } from 'lucide-react';
import { useProjectStore, useAuthStore } from '@/stores';
import { Button, Input } from '@/components/ui';
import { cn, formatRelativeTime } from '@/lib/utils';
import { chatApi, type ChatMessage } from '@/lib/api';
import { Markdown } from '@/components/Markdown';

interface Message extends ChatMessage {
  isStreaming?: boolean;
  sources?: Array<{ filename: string; chunk_index: number; content: string; score: number }>;
}

// Source item component with expand/collapse
function SourceItem({ source, index }: { source: { filename: string; chunk_index: number; content: string; score: number }; index: number }) {
  const [isExpanded, setIsExpanded] = useState(false);

  return (
    <div className="text-xs border border-border/50 rounded-md overflow-hidden">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between px-3 py-2 bg-background/50 hover:bg-background transition-colors"
      >
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <FileText className="h-3 w-3 shrink-0 text-primary" />
          <span className="truncate font-medium">{source.filename}</span>
          <span className="text-muted-foreground shrink-0">chunk {source.chunk_index}</span>
          <span className="text-muted-foreground shrink-0 ml-auto">
            {(source.score * 100).toFixed(0)}%
          </span>
        </div>
        <ChevronDown
          className={cn(
            'h-3 w-3 ml-2 shrink-0 transition-transform',
            isExpanded && 'rotate-180'
          )}
        />
      </button>
      {isExpanded && (
        <div className="px-3 py-2 bg-muted/30 border-t border-border/50">
          <p className="text-xs text-foreground/80 whitespace-pre-wrap leading-relaxed">
            {source.content}
          </p>
        </div>
      )}
    </div>
  );
}

export function ChatPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { projects, currentProject, selectProject, sessions, currentSession, selectSession, createSession, deleteSession } = useProjectStore();
  const { user } = useAuthStore();

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isConnected, setIsConnected] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [newSessionName, setNewSessionName] = useState('');
  const [showNewSession, setShowNewSession] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Auto-select project from URL
  useEffect(() => {
    const projectId = searchParams.get('project');
    if (projectId && !currentProject) {
      const project = projects.find((p) => p.id === projectId);
      if (project) {
        selectProject(project);
      }
    }
  }, [searchParams, projects, currentProject, selectProject]);

  // Load chat history when session changes
  useEffect(() => {
    if (currentSession) {
      chatApi.getHistory(currentSession.id).then(setMessages);
    } else {
      setMessages([]);
    }
  }, [currentSession?.id]);

  // WebSocket connection
  useEffect(() => {
    if (!currentSession) {
      setIsConnected(false);
      return;
    }

    const ws = chatApi.createWebSocket(currentSession.id);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
    };

    ws.onclose = () => {
      setIsConnected(false);
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      switch (data.type) {
        case 'start':
          setIsLoading(true);
          setMessages((prev) => [
            ...prev,
            { role: 'assistant', content: '', isStreaming: true },
          ]);
          break;

        case 'chunk':
          setMessages((prev) => {
            const updated = [...prev];
            const lastIdx = updated.length - 1;
            if (updated[lastIdx]?.isStreaming) {
              updated[lastIdx] = {
                ...updated[lastIdx],
                content: updated[lastIdx].content + data.content,
              };
            }
            return updated;
          });
          break;

        case 'end':
          setIsLoading(false);
          setMessages((prev) => {
            const updated = [...prev];
            const lastIdx = updated.length - 1;
            if (updated[lastIdx]?.isStreaming) {
              updated[lastIdx] = {
                ...updated[lastIdx],
                isStreaming: false,
                sources: data.sources,
              };
            }
            return updated;
          });
          break;

        case 'error':
          setIsLoading(false);
          setMessages((prev) => [
            ...prev.filter((m) => !m.isStreaming),
            { role: 'assistant', content: `Error: ${data.content}` },
          ]);
          break;
      }
    };

    return () => {
      ws.close();
    };
  }, [currentSession?.id]);

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = useCallback(() => {
    if (!input.trim() || !wsRef.current || !isConnected || isLoading) return;

    const message = input.trim();
    setMessages((prev) => [...prev, { role: 'user', content: message }]);
    wsRef.current.send(JSON.stringify({ message }));
    setInput('');
  }, [input, isConnected, isLoading]);

  const handleCreateSession = async () => {
    if (!newSessionName.trim() || !currentProject) return;

    const session = await createSession(newSessionName.trim(), currentProject.id);
    selectSession(session);
    setNewSessionName('');
    setShowNewSession(false);
  };

  const handleDeleteSession = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (confirm('Delete this session?')) {
      await deleteSession(id);
    }
  };

  return (
    <div className="flex h-screen">
      {/* Sidebar - Sessions */}
      <aside className="w-72 border-r border-border bg-card flex flex-col">
        {/* Project Selector */}
        <div className="p-4 border-b border-border">
          <label className="text-xs text-muted-foreground mb-2 block">Project</label>
          <select
            value={currentProject?.id || ''}
            onChange={(e) => {
              const project = projects.find((p) => p.id === e.target.value);
              selectProject(project || null);
              if (project) {
                setSearchParams({ project: project.id });
              }
            }}
            className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm"
          >
            <option value="">Select a project</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>

        {/* Sessions List */}
        <div className="flex-1 overflow-auto p-3 space-y-1">
          {currentProject ? (
            <>
              {sessions.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground text-sm">
                  <MessageSquare className="h-8 w-8 mx-auto mb-2 opacity-50" />
                  <p>No sessions yet</p>
                </div>
              ) : (
                sessions.map((session) => (
                  <button
                    key={session.id}
                    onClick={() => selectSession(session)}
                    className={cn(
                      'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left text-sm transition-colors group',
                      currentSession?.id === session.id
                        ? 'bg-primary/10 text-primary'
                        : 'hover:bg-secondary text-foreground'
                    )}
                  >
                    <MessageSquare className="h-4 w-4 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="truncate font-medium">{session.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {formatRelativeTime(session.updated_at)}
                      </p>
                    </div>
                    <button
                      onClick={(e) => handleDeleteSession(session.id, e)}
                      className="opacity-0 group-hover:opacity-100 p-1 hover:bg-destructive/10 rounded"
                    >
                      <Trash2 className="h-3.5 w-3.5 text-destructive" />
                    </button>
                  </button>
                ))
              )}
            </>
          ) : (
            <div className="text-center py-8 text-muted-foreground text-sm">
              <FolderOpen className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p>Select a project first</p>
            </div>
          )}
        </div>

        {/* New Session */}
        <div className="p-3 border-t border-border">
          {showNewSession ? (
            <div className="space-y-2">
              <Input
                placeholder="Session name..."
                value={newSessionName}
                onChange={(e) => setNewSessionName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleCreateSession()}
                autoFocus
              />
              <div className="flex gap-2">
                <Button size="sm" className="flex-1" onClick={handleCreateSession}>
                  Create
                </Button>
                <Button size="sm" variant="outline" onClick={() => setShowNewSession(false)}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <Button
              variant="outline"
              className="w-full"
              onClick={() => setShowNewSession(true)}
              disabled={!currentProject}
            >
              <Plus className="h-4 w-4 mr-2" />
              New Session
            </Button>
          )}
        </div>
      </aside>

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col">
        {currentSession ? (
          <>
            {/* Chat Header */}
            <header className="h-14 border-b border-border flex items-center px-6 gap-4">
              <div className="flex-1">
                <h2 className="font-semibold">{currentSession.name}</h2>
                <p className="text-xs text-muted-foreground">
                  {currentProject?.name} • {isConnected ? 'Connected' : 'Disconnected'}
                </p>
              </div>
              <div
                className={cn(
                  'w-2 h-2 rounded-full',
                  isConnected ? 'bg-success' : 'bg-destructive'
                )}
              />
            </header>

            {/* Messages */}
            <div className="flex-1 overflow-auto p-6 space-y-6">
              {messages.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full text-center">
                  <MessageSquare className="h-16 w-16 text-muted-foreground/50 mb-4" />
                  <h3 className="text-lg font-medium">Start a conversation</h3>
                  <p className="text-muted-foreground text-sm max-w-md mt-1">
                    Ask questions about your documents. The AI will search your knowledge base
                    and provide relevant answers.
                  </p>
                </div>
              ) : (
                messages.map((msg, idx) => (
                  <div
                    key={idx}
                    className={cn(
                      'flex gap-4 animate-fade-in',
                      msg.role === 'user' ? 'justify-end' : 'justify-start'
                    )}
                  >
                    {msg.role === 'assistant' && (
                      <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary to-accent flex items-center justify-center shrink-0">
                        <MessageSquare className="h-4 w-4 text-primary-foreground" />
                      </div>
                    )}

                    <div
                      className={cn(
                        'max-w-[70%] rounded-2xl px-4 py-3',
                        msg.role === 'user'
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-secondary text-foreground'
                      )}
                    >
                      {msg.role === 'assistant' ? (
                        <>
                          <Markdown content={msg.content} />
                          {msg.isStreaming && (
                            <span className="inline-block w-2 h-4 bg-current animate-pulse ml-1" />
                          )}
                        </>
                      ) : (
                        <p className="whitespace-pre-wrap">{msg.content}</p>
                      )}

                      {/* Sources */}
                      {msg.sources && msg.sources.length > 0 && (
                        <div className="mt-3 pt-3 border-t border-border/50">
                          <p className="text-xs text-muted-foreground mb-2 flex items-center gap-1">
                            <FileText className="h-3 w-3" />
                            Sources ({msg.sources.length})
                          </p>
                          <div className="space-y-2">
                            {msg.sources.map((src, i) => (
                              <SourceItem key={i} source={src} index={i} />
                            ))}
                          </div>
                        </div>
                      )}
                    </div>

                    {msg.role === 'user' && (
                      <div className="w-8 h-8 rounded-lg bg-muted flex items-center justify-center shrink-0">
                        <span className="text-sm font-medium">
                          {user?.email?.[0].toUpperCase()}
                        </span>
                      </div>
                    )}
                  </div>
                ))
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* Input */}
            <div className="p-4 border-t border-border">
              <div className="flex gap-3 max-w-4xl mx-auto">
                <Input
                  placeholder="Ask a question..."
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
                  disabled={!isConnected || isLoading}
                  className="flex-1"
                />
                <Button onClick={handleSend} disabled={!isConnected || isLoading || !input.trim()}>
                  {isLoading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Send className="h-4 w-4" />
                  )}
                </Button>
              </div>
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <MessageSquare className="h-20 w-20 mx-auto text-muted-foreground/50 mb-6" />
              <h2 className="text-2xl font-semibold mb-2">Select or create a session</h2>
              <p className="text-muted-foreground max-w-md">
                {currentProject
                  ? 'Create a new session to start chatting with your documents'
                  : 'First select a project, then create a session to begin'}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
