export type RagMode = 'vector' | 'graph';
export type GraphBackend = 'neo4j' | 'microsoft';
/** UI mode when creating or displaying a project */
export type ProjectMode = 'vector' | 'graph_neo4j' | 'graph_microsoft';
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

export interface GraphRetrievalConfig {
  strategy: GraphRetrievalStrategy;
  params: Record<string, number | null | boolean>;
}

export interface GraphIndexingConfig {
  enabled: boolean;
  method: 'standard' | 'nlp';
  community_level: number;
}

export interface Neo4jIndexingConfig {
  max_entities_per_passage: number;
  embed_entities: boolean;
}

export interface GraphExtractionConfig {
  strategy: ExtractionStrategy;
  passage_chunk_size: number;
}

export interface VectorRagConfig {
  extraction: ExtractionConfig;
  chunking: ChunkingConfig;
  retrieval: RetrievalConfig;
  reranking: RerankingConfig;
}

export interface GraphRagConfig {
  graph_backend: GraphBackend;
  extraction: GraphExtractionConfig;
  indexing: Neo4jIndexingConfig;
  microsoft_indexing?: GraphIndexingConfig;
  retrieval: GraphRetrievalConfig;
}

/** @deprecated Use VectorRagConfig | GraphRagConfig — kept for gradual migration */
export interface LegacyRagConfig extends VectorRagConfig {
  graph_indexing?: GraphIndexingConfig;
  graph_retrieval?: GraphRetrievalConfig;
}

export type RagConfig = VectorRagConfig | GraphRagConfig | LegacyRagConfig;

export interface GraphIndexState {
  backend?: GraphBackend | null;
  status: GraphIndexStatus;
  indexed_at?: string | null;
  fingerprint?: string | null;
  error?: string | null;
  document_count?: number | null;
  entity_count?: number | null;
  passage_count?: number | null;
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

export function isGraphRagConfig(config: RagConfig | null | undefined): config is GraphRagConfig {
  return config != null && typeof config === 'object' && 'graph_backend' in config;
}

export function getGraphBackend(config: RagConfig): GraphBackend | null {
  if (isGraphRagConfig(config)) return config.graph_backend;
  return null;
}

export function projectModeToRagMode(mode: ProjectMode): RagMode {
  return mode === 'vector' ? 'vector' : 'graph';
}

export function getProjectMode(ragMode: RagMode, config: RagConfig | null | undefined): ProjectMode {
  if (ragMode === 'vector') return 'vector';
  if (isGraphRagConfig(config) && config.graph_backend === 'microsoft') {
    return 'graph_microsoft';
  }
  return 'graph_neo4j';
}

export function projectModeLabel(mode: ProjectMode): string {
  switch (mode) {
    case 'vector':
      return 'Vector RAG';
    case 'graph_neo4j':
      return 'Graph RAG (Neo4j)';
    case 'graph_microsoft':
      return 'Graph RAG (Microsoft)';
  }
}

export function defaultConfigForProjectMode(mode: ProjectMode): RagConfig {
  if (mode === 'graph_neo4j') {
    return {
      graph_backend: 'neo4j',
      extraction: { strategy: 'ocr', passage_chunk_size: 800 },
      indexing: { max_entities_per_passage: 20, embed_entities: true },
      retrieval: { strategy: 'graph_local', params: { max_hops: 2, top_entities: 10 } },
    };
  }
  if (mode === 'graph_microsoft') {
    return {
      graph_backend: 'microsoft',
      extraction: { strategy: 'ocr', passage_chunk_size: 800 },
      indexing: { max_entities_per_passage: 20, embed_entities: true },
      microsoft_indexing: { enabled: true, method: 'standard', community_level: 2 },
      retrieval: { strategy: 'graph_local', params: { community_level: 2 } },
    };
  }
  return {
    extraction: { strategy: 'ocr' },
    chunking: { strategy: 'fixed_window', params: { chunk_size: 512, overlap: 50 } },
    retrieval: { strategy: 'dense', params: {} },
    reranking: { strategy: 'none', params: {} },
  };
}
