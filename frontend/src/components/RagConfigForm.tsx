import { useEffect } from 'react';
import { ragApi } from '@/lib/api';
import type {
  ChunkingStrategy,
  GraphBackend,
  GraphRagConfig,
  RagConfig,
  RagMode,
  RetrievalStrategy,
  VectorRagConfig,
} from '@/lib/rag-types';
import {
  defaultConfigForProjectMode,
  isGraphRagConfig,
  projectModeToRagMode,
  type ProjectMode,
} from '@/lib/rag-types';
import { Input } from '@/components/ui';

interface RagConfigFormProps {
  value: RagConfig;
  onChange: (config: RagConfig) => void;
  compact?: boolean;
  ragMode?: RagMode;
  graphBackend?: GraphBackend;
}

const defaultVectorConfig = (): VectorRagConfig =>
  defaultConfigForProjectMode('vector') as VectorRagConfig;

function asVectorConfig(value: RagConfig): VectorRagConfig {
  if (isGraphRagConfig(value)) return defaultVectorConfig();
  return value;
}

function asGraphConfig(value: RagConfig, backend: GraphBackend): GraphRagConfig {
  if (isGraphRagConfig(value) && value.graph_backend === backend) {
    return value;
  }
  return defaultConfigForProjectMode(
    backend === 'microsoft' ? 'graph_microsoft' : 'graph_neo4j'
  ) as GraphRagConfig;
}

export function RagConfigForm({
  value,
  onChange,
  compact,
  ragMode = 'vector',
  graphBackend = 'neo4j',
}: RagConfigFormProps) {
  const isGraph = ragMode === 'graph';
  const backend = isGraphRagConfig(value) ? value.graph_backend : graphBackend;
  const isMicrosoft = isGraph && backend === 'microsoft';
  const graphConfig = isGraph ? asGraphConfig(value, backend) : null;

  useEffect(() => {
    if (isGraph) {
      ragApi.getOptions('graph', backend).catch(() => undefined);
    } else {
      ragApi.getOptions('vector').catch(() => undefined);
    }
  }, [isGraph, backend]);

  const label = compact ? 'text-xs' : 'text-sm';

  const updateGraph = (partial: Partial<GraphRagConfig>) => {
    if (!graphConfig) return;
    onChange({ ...graphConfig, ...partial });
  };

  if (isGraph && graphConfig) {
    return (
      <div className="space-y-4">
        <div className="space-y-2">
          <label className={`${label} font-medium`}>Extraction</label>
          <select
            className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
            value={graphConfig.extraction.strategy}
            onChange={(e) =>
              updateGraph({
                extraction: {
                  ...graphConfig.extraction,
                  strategy: e.target.value as 'ocr' | 'vlm',
                },
              })
            }
          >
            <option value="ocr">OCR</option>
            <option value="vlm">VLM</option>
          </select>
        </div>

        <div className="space-y-2">
          <label className={`${label} font-medium`}>Passage chunk size</label>
          <Input
            type="number"
            min={200}
            max={4096}
            value={graphConfig.extraction.passage_chunk_size}
            onChange={(e) =>
              updateGraph({
                extraction: {
                  ...graphConfig.extraction,
                  passage_chunk_size: Number(e.target.value),
                },
              })
            }
          />
        </div>

        {isMicrosoft ? (
          <div className="space-y-2">
            <label className={`${label} font-medium`}>Microsoft graph indexing</label>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-muted-foreground">Method</label>
                <select
                  className="w-full h-10 rounded-md border border-input bg-background px-2 text-sm"
                  value={graphConfig.microsoft_indexing?.method ?? 'standard'}
                  onChange={(e) =>
                    updateGraph({
                      microsoft_indexing: {
                        enabled: graphConfig.microsoft_indexing?.enabled ?? true,
                        method: e.target.value as 'standard' | 'nlp',
                        community_level: graphConfig.microsoft_indexing?.community_level ?? 2,
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
                  value={graphConfig.microsoft_indexing?.community_level ?? 2}
                  onChange={(e) =>
                    updateGraph({
                      microsoft_indexing: {
                        enabled: graphConfig.microsoft_indexing?.enabled ?? true,
                        method: graphConfig.microsoft_indexing?.method ?? 'standard',
                        community_level: Number(e.target.value),
                      },
                    })
                  }
                />
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            <label className={`${label} font-medium`}>Neo4j indexing</label>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-muted-foreground">Max entities / passage</label>
                <Input
                  type="number"
                  min={1}
                  max={100}
                  value={graphConfig.indexing.max_entities_per_passage}
                  onChange={(e) =>
                    updateGraph({
                      indexing: {
                        ...graphConfig.indexing,
                        max_entities_per_passage: Number(e.target.value),
                      },
                    })
                  }
                />
              </div>
              <div className="flex items-end pb-2">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={graphConfig.indexing.embed_entities}
                    onChange={(e) =>
                      updateGraph({
                        indexing: {
                          ...graphConfig.indexing,
                          embed_entities: e.target.checked,
                        },
                      })
                    }
                  />
                  Embed entities
                </label>
              </div>
            </div>
          </div>
        )}

        <div className="space-y-2">
          <label className={`${label} font-medium`}>Default retrieval</label>
          <select
            className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
            value={graphConfig.retrieval.strategy}
            onChange={(e) => {
              const strategy = e.target.value as 'graph_local' | 'graph_global';
              const params: Record<string, number | null | boolean> = isMicrosoft
                ? { community_level: graphConfig.microsoft_indexing?.community_level ?? 2 }
                : strategy === 'graph_global'
                  ? { top_passages: 5 }
                  : { max_hops: 2, top_entities: 10 };
              updateGraph({ retrieval: { strategy, params } });
            }}
          >
            <option value="graph_local">Graph local (entity-focused)</option>
            <option value="graph_global">Graph global (thematic)</option>
          </select>
        </div>
      </div>
    );
  }

  const vectorConfig = asVectorConfig(value);

  const updateVector = (partial: Partial<VectorRagConfig>) => {
    onChange({ ...vectorConfig, ...partial });
  };

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <label className={`${label} font-medium`}>Extraction</label>
        <select
          className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
          value={vectorConfig.extraction.strategy}
          onChange={(e) =>
            updateVector({
              extraction: { strategy: e.target.value as 'ocr' | 'vlm' },
            })
          }
        >
          <option value="ocr">OCR</option>
          <option value="vlm">VLM</option>
        </select>
      </div>

      <div className="space-y-2">
        <label className={`${label} font-medium`}>Chunking</label>
        <select
          className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
          value={vectorConfig.chunking.strategy}
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
            updateVector({ chunking: { strategy, params } });
          }}
        >
          <option value="fixed_window">Fixed window</option>
          <option value="recursive">Recursive</option>
          <option value="semantic">Semantic</option>
          <option value="parent_child">Parent / child</option>
        </select>
        {(vectorConfig.chunking.strategy === 'fixed_window' ||
          vectorConfig.chunking.strategy === 'recursive') && (
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs text-muted-foreground">Chunk size</label>
              <Input
                type="number"
                value={Number(vectorConfig.chunking.params.chunk_size ?? 512)}
                onChange={(e) =>
                  updateVector({
                    chunking: {
                      ...vectorConfig.chunking,
                      params: {
                        ...vectorConfig.chunking.params,
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
                value={Number(vectorConfig.chunking.params.overlap ?? 50)}
                onChange={(e) =>
                  updateVector({
                    chunking: {
                      ...vectorConfig.chunking,
                      params: {
                        ...vectorConfig.chunking.params,
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

      <div className="space-y-2">
        <label className={`${label} font-medium`}>Default retrieval</label>
        <select
          className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
          value={vectorConfig.retrieval.strategy}
          onChange={(e) => {
            const strategy = e.target.value as RetrievalStrategy;
            let params: Record<string, number | null | boolean> = {};
            if (strategy === 'hybrid') params = { rrf_k: 60 };
            else if (strategy === 'bm25') params = { k1: 1.5, b: 0.75 };
            updateVector({ retrieval: { strategy, params } });
          }}
        >
          <option value="dense">Dense (semantic)</option>
          <option value="bm25">BM25 (lexical)</option>
          <option value="hybrid">Hybrid (semantic + BM25)</option>
          <option value="parent_child">Parent / child</option>
        </select>
      </div>

      <div className="space-y-2">
        <label className={`${label} font-medium`}>Reranking</label>
        <select
          className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
          value={vectorConfig.reranking.strategy}
          onChange={(e) =>
            updateVector({
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
    </div>
  );
}

export function defaultRagConfigForMode(
  mode: RagMode,
  graphBackend: GraphBackend = 'neo4j'
): RagConfig {
  if (mode === 'graph') {
    const projectMode: ProjectMode =
      graphBackend === 'microsoft' ? 'graph_microsoft' : 'graph_neo4j';
    return defaultConfigForProjectMode(projectMode);
  }
  return defaultConfigForProjectMode('vector');
}

export function projectModeFromRag(
  ragMode: RagMode,
  config: RagConfig
): ProjectMode {
  if (ragMode === 'vector') return 'vector';
  if (isGraphRagConfig(config) && config.graph_backend === 'microsoft') {
    return 'graph_microsoft';
  }
  return 'graph_neo4j';
}

export { projectModeToRagMode };
