import { useEffect } from 'react';
import { ragApi } from '@/lib/api';
import type {
  ChunkingStrategy,
  GraphRetrievalStrategy,
  RagConfig,
  RagMode,
  RetrievalStrategy,
  VectorRetrievalStrategy,
} from '@/lib/rag-types';
import { Input } from '@/components/ui';

interface RagConfigFormProps {
  value: RagConfig;
  onChange: (config: RagConfig) => void;
  compact?: boolean;
  ragMode?: RagMode;
}

const defaultConfig = (): RagConfig => ({
  extraction: { strategy: 'ocr' },
  chunking: { strategy: 'fixed_window', params: { chunk_size: 512, overlap: 50 } },
  retrieval: { strategy: 'dense', params: {} },
  reranking: { strategy: 'none', params: {} },
  graph_indexing: { enabled: true, method: 'standard', community_level: 2 },
  graph_retrieval: { strategy: 'graph_local', params: { community_level: 2 } },
});

export function RagConfigForm({ value, onChange, compact, ragMode = 'vector' }: RagConfigFormProps) {
  const isGraph = ragMode === 'graph';

  useEffect(() => {
    ragApi.getOptions(ragMode).catch(() => undefined);
  }, [ragMode]);

  const update = (partial: Partial<RagConfig>) => {
    onChange({ ...value, ...partial });
  };

  const label = compact ? 'text-xs' : 'text-sm';

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <label className={`${label} font-medium`}>Extraction</label>
        <select
          className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
          value={value.extraction.strategy}
          onChange={(e) =>
            update({
              extraction: { strategy: e.target.value as 'ocr' | 'vlm' },
            })
          }
        >
          <option value="ocr">OCR</option>
          <option value="vlm">VLM</option>
        </select>
      </div>

      {!isGraph && (
        <div className="space-y-2">
          <label className={`${label} font-medium`}>Chunking</label>
          <select
            className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
            value={value.chunking.strategy}
            onChange={(e) => {
              const strategy = e.target.value as ChunkingStrategy;
              let params: Record<string, number | null>;
              if (strategy === 'fixed_window' || strategy === 'recursive') {
                params = { chunk_size: 512, overlap: 50 };
              } else if (strategy === 'semantic') {
                params = {
                  similarity_threshold: 0.5,
                  min_chunk_size: 100,
                  max_chunk_size: 1000,
                };
              } else {
                params = {
                  parent_chunk_size: 1500,
                  child_chunk_size: 300,
                  overlap: 50,
                };
              }
              update({ chunking: { strategy, params } });
            }}
          >
            <option value="fixed_window">Fixed window</option>
            <option value="recursive">Recursive</option>
            <option value="semantic">Semantic</option>
            <option value="parent_child">Parent / child</option>
          </select>
          {(value.chunking.strategy === 'fixed_window' ||
            value.chunking.strategy === 'recursive') && (
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-muted-foreground">Chunk size</label>
                <Input
                  type="number"
                  value={Number(value.chunking.params.chunk_size ?? 512)}
                  onChange={(e) =>
                    update({
                      chunking: {
                        ...value.chunking,
                        params: {
                          ...value.chunking.params,
                          chunk_size: Number(e.target.value),
                        },
                      },
                    })
                  }
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Overlap</label>
                <Input
                  type="number"
                  value={Number(value.chunking.params.overlap ?? 50)}
                  onChange={(e) =>
                    update({
                      chunking: {
                        ...value.chunking,
                        params: {
                          ...value.chunking.params,
                          overlap: Number(e.target.value),
                        },
                      },
                    })
                  }
                />
              </div>
            </div>
          )}
        </div>
      )}

      {isGraph && (
        <div className="space-y-2">
          <label className={`${label} font-medium`}>Graph indexing</label>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs text-muted-foreground">Method</label>
              <select
                className="w-full h-10 rounded-md border border-input bg-background px-2 text-sm"
                value={value.graph_indexing?.method ?? 'standard'}
                onChange={(e) =>
                  update({
                    graph_indexing: {
                      enabled: value.graph_indexing?.enabled ?? true,
                      method: e.target.value as 'standard' | 'nlp',
                      community_level: value.graph_indexing?.community_level ?? 2,
                    },
                  })
                }
              >
                <option value="standard">Standard (LLM)</option>
                <option value="nlp">NLP + LLM</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-muted-foreground">Community level</label>
              <Input
                type="number"
                min={0}
                max={4}
                value={value.graph_indexing?.community_level ?? 2}
                onChange={(e) =>
                  update({
                    graph_indexing: {
                      enabled: value.graph_indexing?.enabled ?? true,
                      method: value.graph_indexing?.method ?? 'standard',
                      community_level: Number(e.target.value),
                    },
                  })
                }
              />
            </div>
          </div>
        </div>
      )}

      <div className="space-y-2">
        <label className={`${label} font-medium`}>Default retrieval</label>
        <select
          className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
          value={value.retrieval.strategy}
          onChange={(e) => {
            const strategy = e.target.value as RetrievalStrategy;
            let params: Record<string, number | null | boolean> = {};
            if (strategy === 'hybrid') params = { rrf_k: 60 };
            else if (strategy === 'bm25') params = { k1: 1.5, b: 0.75 };
            else if (strategy === 'graph_local' || strategy === 'graph_global')
              params = { community_level: 2 };
            update({ retrieval: { strategy, params } });
          }}
        >
          {isGraph ? (
            <>
              <option value="graph_local">Graph local (entity-focused)</option>
              <option value="graph_global">Graph global (thematic)</option>
            </>
          ) : (
            <>
              <option value="dense">Dense (semantic)</option>
              <option value="bm25">BM25 (lexical)</option>
              <option value="hybrid">Hybrid (semantic + BM25)</option>
              <option value="parent_child">Parent / child</option>
            </>
          )}
        </select>
      </div>

      {!isGraph && (
        <div className="space-y-2">
          <label className={`${label} font-medium`}>Reranking</label>
          <select
            className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
            value={value.reranking.strategy}
            onChange={(e) =>
              update({
                reranking: {
                  strategy: e.target.value as 'none' | 'cross_encoder',
                  params: {},
                },
              })
            }
          >
            <option value="none">None</option>
            <option value="cross_encoder">Cross-encoder</option>
          </select>
        </div>
      )}
    </div>
  );
}

export { defaultConfig as defaultRagConfig };

export function defaultRagConfigForMode(mode: RagMode): RagConfig {
  if (mode === 'graph') {
    return {
      extraction: { strategy: 'ocr' },
      chunking: { strategy: 'fixed_window', params: {} },
      retrieval: { strategy: 'graph_local', params: { community_level: 2 } },
      reranking: { strategy: 'none', params: {} },
      graph_indexing: { enabled: true, method: 'standard', community_level: 2 },
      graph_retrieval: { strategy: 'graph_local', params: { community_level: 2 } },
    };
  }
  return defaultConfig();
}
