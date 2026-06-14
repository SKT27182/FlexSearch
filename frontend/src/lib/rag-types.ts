export type RagMode = 'vector' | 'graph';

export type ExtractionStrategy = 'ocr' | 'vlm';
export type ChunkingStrategy = 'fixed_window' | 'recursive' | 'semantic' | 'parent_child';
export type VectorRetrievalStrategy = 'dense' | 'bm25' | 'hybrid' | 'parent_child';
export type GraphRetrievalStrategy = 'graph_local' | 'graph_global';
export type RetrievalStrategy = VectorRetrievalStrategy | GraphRetrievalStrategy;
export type RerankingStrategy = 'none' | 'cross_encoder';

export interface ExtractionConfig {
  strategy: ExtractionStrategy;
}

export interface ChunkingConfig {
  strategy: ChunkingStrategy;
  params: Record<string, number | null>;
}

export interface VectorRetrievalConfig {
  strategy: VectorRetrievalStrategy;
  params: Record<string, number | null>;
}

export interface RerankingConfig {
  strategy: RerankingStrategy;
  params: Record<string, unknown>;
}

/** Vector RAG project configuration */
export interface VectorRagConfig {
  extraction: ExtractionConfig;
  chunking: ChunkingConfig;
  retrieval: VectorRetrievalConfig;
  reranking: RerankingConfig;
}

export interface GraphExtractionConfig {
  strategy: ExtractionStrategy;
  passage_chunk_size: number;
}

export interface GraphIndexingConfig {
  max_entities_per_passage: number;
  embed_entities: boolean;
}

export interface GraphRetrievalConfig {
  strategy: GraphRetrievalStrategy;
  params: Record<string, number | null>;
}

/** Graph RAG project configuration */
export interface GraphRagConfig {
  extraction: GraphExtractionConfig;
  indexing: GraphIndexingConfig;
  retrieval: GraphRetrievalConfig;
}

export type RagConfig = VectorRagConfig | GraphRagConfig;

export interface GraphIndexStatus {
  status: 'pending' | 'indexing' | 'ready' | 'failed';
  indexed_at?: string | null;
  entity_count?: number;
  passage_count?: number;
  error?: string | null;
}

export interface RetrievalOverrides {
  retrieval_strategy?: RetrievalStrategy;
  reranking_strategy?: RerankingStrategy;
  top_k?: number;
  retrieval_params?: Record<string, number | null>;
  reranking_params?: Record<string, unknown>;
}

export type DocumentStatus =
  | 'uploaded'
  | 'stored'
  | 'extracting'
  | 'extracted'
  | 'chunking'
  | 'indexing'
  | 'graph_indexing'
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
  'graph_indexing',
  'completed',
];

export function canPreview(status: string): boolean {
  return PREVIEW_STATUSES.includes(status as DocumentStatus);
}

export function isVectorRagConfig(config: RagConfig): config is VectorRagConfig {
  return 'chunking' in config;
}

export function isGraphRagConfig(config: RagConfig): config is GraphRagConfig {
  return 'indexing' in config;
}

export function defaultVectorRagConfig(): VectorRagConfig {
  return {
    extraction: { strategy: 'ocr' },
    chunking: { strategy: 'fixed_window', params: { chunk_size: 512, overlap: 50 } },
    retrieval: { strategy: 'dense', params: {} },
    reranking: { strategy: 'none', params: {} },
  };
}

export function defaultGraphRagConfig(): GraphRagConfig {
  return {
    extraction: { strategy: 'ocr', passage_chunk_size: 800 },
    indexing: { max_entities_per_passage: 20, embed_entities: true },
    retrieval: { strategy: 'graph_local', params: { max_hops: 2, top_entities: 10 } },
  };
}
