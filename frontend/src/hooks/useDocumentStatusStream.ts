import { fetchEventSource } from '@microsoft/fetch-event-source';
import type { DocumentStatusEvent } from '@/lib/rag-types';
import { documentsApi } from '@/lib/api';

const API_BASE = '/api';
const POLL_MS = 2500;

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('access_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function docToEvent(
  projectId: string,
  doc: Awaited<ReturnType<typeof documentsApi.list>>[number]
): DocumentStatusEvent {
  return {
    document_id: doc.id,
    project_id: projectId,
    status: doc.status,
    processing_step: doc.processing_step,
    progress_pct: doc.progress_pct,
    chunk_count: doc.chunk_count,
    error_message: doc.error_message ?? null,
    filename: doc.filename,
  };
}

function isTerminal(status: string): boolean {
  return status === 'completed' || status === 'failed';
}

function dispatchSseMessage(
  msg: { event?: string; data: string },
  onStatus: (event: DocumentStatusEvent) => void
): boolean {
  if (!msg.data) return false;
  try {
    const data = JSON.parse(msg.data) as Record<string, unknown>;
    if (msg.event === 'error') {
      return true;
    }
    if (msg.event === 'status' || msg.event === 'snapshot') {
      onStatus(data as unknown as DocumentStatusEvent);
      return isTerminal(String(data.status));
    }
    if (msg.event === 'snapshots' && Array.isArray(data.documents)) {
      let anyTerminal = false;
      for (const ev of data.documents as DocumentStatusEvent[]) {
        onStatus(ev);
        if (isTerminal(ev.status)) anyTerminal = true;
      }
      return anyTerminal;
    }
    if (msg.event === 'close') {
      return true;
    }
  } catch {
    /* ignore parse errors */
  }
  return false;
}

/** Poll project documents until every doc is terminal (or unsubscribe). */
export function startDocumentPolling(
  projectId: string,
  onStatus: (event: DocumentStatusEvent) => void,
  onDone?: () => void
): () => void {
  let stopped = false;

  let timer: ReturnType<typeof setInterval> | null = null;

  const stop = () => {
    if (stopped) return;
    stopped = true;
    if (timer) clearInterval(timer);
    timer = null;
  };

  const tick = async () => {
    if (stopped) return;
    try {
      const docs = await documentsApi.list(projectId);
      for (const doc of docs) {
        onStatus(docToEvent(projectId, doc));
      }
      const hasProcessing = docs.some((d) => !isTerminal(d.status));
      if (docs.length > 0 && !hasProcessing) {
        onDone?.();
        stop();
      }
    } catch {
      /* ignore transient errors */
    }
  };

  void tick();
  timer = setInterval(() => {
    void tick();
  }, POLL_MS);

  return stop;
}

export function subscribeProjectDocuments(
  projectId: string,
  onStatus: (event: DocumentStatusEvent) => void,
  onDone?: () => void
): () => void {
  const ctrl = new AbortController();
  let pollCleanup: (() => void) | null = null;

  const startPolling = () => {
    if (pollCleanup) return;
    pollCleanup = startDocumentPolling(projectId, onStatus, () => {
      onDone?.();
    });
  };

  // Poll in parallel so UI updates even if SSE is buffered or Redis is down
  startPolling();

  void fetchEventSource(`${API_BASE}/projects/${projectId}/documents/events`, {
    signal: ctrl.signal,
    headers: authHeaders(),
    openWhenHidden: true,
    onopen: async (res) => {
      if (!res.ok) {
        startPolling();
        throw new Error(`SSE failed: ${res.status}`);
      }
      const ct = res.headers.get('content-type') ?? '';
      if (!ct.includes('text/event-stream')) {
        startPolling();
        throw new Error(`Unexpected content-type: ${ct}`);
      }
    },
    onmessage: (msg) => {
      const terminal = dispatchSseMessage(msg, onStatus);
      if (terminal) {
        onDone?.();
      }
    },
    onclose: () => {
      startPolling();
    },
    onerror: () => {
      startPolling();
      return 60_000;
    },
  }).catch(() => {
    startPolling();
  });

  return () => {
    ctrl.abort();
    pollCleanup?.();
    pollCleanup = null;
  };
}
