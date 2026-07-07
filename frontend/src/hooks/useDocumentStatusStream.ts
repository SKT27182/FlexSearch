import { fetchEventSource } from '@microsoft/fetch-event-source';
import type { DocumentStatusEvent } from '@/lib/rag-types';
import { documentId } from '@/lib/document-state';
import { documentsApi } from '@/lib/api';

const API_BASE = '/api';
const POLL_MS = 2000;

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
    const normalize = (raw: Record<string, unknown>): DocumentStatusEvent | null => {
      const id = documentId(String(raw.document_id ?? raw.id ?? ''));
      if (!id) return null;
      return {
        document_id: id,
        project_id: String(raw.project_id ?? ''),
        status: raw.status as DocumentStatusEvent['status'],
        processing_step: (raw.processing_step as string | null) ?? null,
        progress_pct: Number(raw.progress_pct ?? 0),
        chunk_count: Number(raw.chunk_count ?? 0),
        error_message: (raw.error_message as string | null) ?? null,
        filename: raw.filename as string | undefined,
      };
    };

    if (msg.event === 'status' || msg.event === 'snapshot') {
      const ev = normalize(data);
      if (!ev) return false;
      onStatus(ev);
      return false;
    }
    if (msg.event === 'snapshots' && Array.isArray(data.documents)) {
      for (const raw of data.documents as Record<string, unknown>[]) {
        const ev = normalize(raw);
        if (!ev) continue;
        onStatus(ev);
      }
      return false;
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
  let sseConnected = false;

  const startPolling = () => {
    if (pollCleanup) return;
    pollCleanup = startDocumentPolling(projectId, onStatus, () => {
      onDone?.();
    });
  };

  // Poll all documents in parallel with SSE so batch uploads never miss updates
  // for files that finish while others are still uploading.
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
      sseConnected = true;
    },
    onmessage: (msg) => {
      dispatchSseMessage(msg, onStatus);
    },
    onclose: () => {
      if (!sseConnected) startPolling();
    },
    onerror: () => {
      startPolling();
      return 60_000;
    },
  }).catch(() => {
    startPolling();
  });

  // Fallback polling if SSE never connects within a few seconds
  const pollFallbackTimer = setTimeout(() => {
    if (!sseConnected) startPolling();
  }, 4000);

  return () => {
    clearTimeout(pollFallbackTimer);
    ctrl.abort();
    pollCleanup?.();
    pollCleanup = null;
  };
}
