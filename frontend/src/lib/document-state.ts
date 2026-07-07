import type { Document } from '@/lib/api';
import type { DocumentStatus, DocumentStatusEvent } from '@/lib/rag-types';

export function documentId(value: string | undefined | null): string {
  return value ? String(value) : '';
}

export function eventDocumentId(ev: DocumentStatusEvent): string {
  return documentId(ev.document_id);
}

/** Stable display order: oldest upload first, then by id. */
export function compareDocumentOrder(a: Document, b: Document): number {
  const byTime = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
  if (byTime !== 0) return byTime;
  return documentId(a.id).localeCompare(documentId(b.id));
}

export function sortDocuments(docs: Document[]): Document[] {
  return [...docs].sort(compareDocumentOrder);
}

function upsertInPlace(prev: Document[], docId: string, merged: Document): Document[] {
  const idx = prev.findIndex((d) => documentId(d.id) === docId);
  if (idx >= 0) {
    const next = [...prev];
    next[idx] = merged;
    return next;
  }
  return [...prev, merged];
}

/** Merge a status event into a document list, keeping at most one row per document id. */
export function upsertDocumentFromEvent(
  prev: Document[],
  ev: DocumentStatusEvent,
  projectId: string
): Document[] {
  const docId = eventDocumentId(ev);
  if (!docId) return prev;

  const existing = prev.find((d) => documentId(d.id) === docId);
  const merged: Document = {
    ...(existing ?? {
      id: docId,
      filename: ev.filename ?? 'Document',
      content_type: 'application/octet-stream',
      size_bytes: 0,
      project_id: projectId,
      created_at: new Date().toISOString(),
      chunk_count: 0,
      error_message: null,
    }),
    id: docId,
    status: ev.status as DocumentStatus,
    processing_step: ev.processing_step,
    progress_pct: ev.progress_pct,
    chunk_count: ev.chunk_count,
    error_message: ev.error_message ?? existing?.error_message ?? null,
    ...(ev.filename ? { filename: ev.filename } : {}),
  };

  return upsertInPlace(prev, docId, merged);
}

/** Insert or replace a document from the API (e.g. after upload). */
export function upsertDocumentFromApi(prev: Document[], doc: Document): Document[] {
  const docId = documentId(doc.id);
  return upsertInPlace(prev, docId, doc);
}

export function dedupeDocumentsById(docs: Document[]): Document[] {
  const byId = new Map<string, Document>();
  for (const doc of docs) {
    const id = documentId(doc.id);
    if (id) byId.set(id, doc);
  }
  return sortDocuments(Array.from(byId.values()));
}

/** Apply server list onto local state, preserving row order and refreshing every known doc. */
export function mergeDocumentsFromServer(prev: Document[], server: Document[]): Document[] {
  const serverById = new Map(server.map((d) => [documentId(d.id), d]));
  const seen = new Set<string>();
  const next: Document[] = [];

  for (const local of prev) {
    const id = documentId(local.id);
    const remote = serverById.get(id);
    if (remote) {
      next.push(remote);
      seen.add(id);
    } else {
      next.push(local);
    }
  }

  for (const remote of server) {
    const id = documentId(remote.id);
    if (!seen.has(id)) {
      next.push(remote);
    }
  }

  return next;
}

export function processingDocuments(
  docs: Document[],
  processingStatuses: ReadonlySet<string>,
  uploadRank?: ReadonlyMap<string, number>
): Document[] {
  const byId = new Map<string, Document>();
  for (const d of docs) {
    if (processingStatuses.has(d.status)) {
      byId.set(documentId(d.id), d);
    }
  }

  return Array.from(byId.values()).sort((a, b) => {
    const idA = documentId(a.id);
    const idB = documentId(b.id);
    const rankA = uploadRank?.get(idA);
    const rankB = uploadRank?.get(idB);
    if (rankA !== undefined && rankB !== undefined) return rankA - rankB;
    if (rankA !== undefined) return -1;
    if (rankB !== undefined) return 1;
    return compareDocumentOrder(a, b);
  });
}
