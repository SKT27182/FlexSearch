export type RagMode = 'vector' | 'graph';
export type ExtractionStrategy = 'ocr' | 'vlm';
export type ChunkingStrategy = 'fixed_window' | 'recursive' | 'semantic' | 'parent_child';
export type VectorRetrievalStrategy = 'dense' | 'bm25' | 'hybrid' | 'parent_child';
export type GraphRetrievalStrategy = 'graph_local' | 'graph_global';
export type RetrievalStrategy = VectorRetrievalStrategy | GraphRetrievalStrategy;
export type RerankingStrategy = 'none' | 'cross_encoder';
export type GraphIndexStatus = 'pending' | 'indexing' | 'ready' | 'failed' | 'disabled';

export interface ExtractionConfig {
  strategy: ExtractionStrategy;
}

export interface ChunkingConfig {
  strategy: ChunkingStrategy;
  params: Record<string, number | null>;
}

export interface RetrievalConfig {
  strategy: RetrievalStrategy;
  params: Record<string, number | null | boolean>;
}

export interface RerankingConfig {
  strategy: RerankingStrategy;
  params: Record<string, unknown>;
}

export interface GraphIndexingConfig {
  enabled: boolean;
  method: 'standard' | 'nlp';
  community_level: number;
}

export interface GraphRetrievalConfig {
  strategy: GraphRetrievalStrategy;
  params: Record<string, number | null | boolean>;
}

export interface RagConfig {
  extraction: ExtractionConfig;
  chunking: ChunkingConfig;
  retrieval: RetrievalConfig;
  reranking: RerankingConfig;
  graph_indexing?: GraphIndexingConfig;
  graph_retrieval?: GraphRetrievalConfig;
}

export interface GraphIndexState {
  status: GraphIndexStatus;
  indexed_at?: string | null;
  fingerprint?: string | null;
  error?: string | null;
  document_count?: number | null;
}

export interface RetrievalOverrides {
  retrieval_strategy?: RetrievalStrategy;
  reranking_strategy?: RerankingStrategy;
  top_k?: number;
  retrieval_params?: Record<string, number | null | boolean>;
  reranking_params?: Record<string, unknown>;
}

export type DocumentStatus =
  | 'uploaded'
  | 'stored'
  | 'extracting'
  | 'extracted'
  | 'chunking'
  | 'indexing'
  | 'completed'
  | 'failed';

export interface DocumentStatusEvent {
  document_id: string;
  project_id: string;
  status: DocumentStatus;
  processing_step: string | null;
  progress_pct: number;
  chunk_count: number;
  error_message: string | null;
  filename?: string;
}

export const TERMINAL_STATUSES: DocumentStatus[] = ['completed', 'failed'];

export const PREVIEW_STATUSES: DocumentStatus[] = [
  'extracted',
  'chunking',
  'indexing',
  'completed',
];

export function canPreview(status: string): boolean {
  return PREVIEW_STATUSES.includes(status as DocumentStatus);
}

export function isGraphMode(mode: RagMode | undefined): boolean {
  return mode === 'graph';
}
