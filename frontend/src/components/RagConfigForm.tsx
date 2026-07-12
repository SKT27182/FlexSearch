import { useEffect } from 'react';
import { ragApi } from '@/lib/api';
import type {
  ChatConfig,
  ChunkingStrategy,
  GraphBackend,
  GraphRagConfig,
  HierarchyRetrievalMode,
  HierarchicalSummaryConfig,
  RagConfig,
  RagMode,
  RetrievalStrategy,
  VectorRagConfig,
} from '@/lib/rag-types';
import {
  defaultChatConfig,
  defaultConfigForProjectMode,
  defaultSummariesConfig,
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

function resolveChat(config: { chat?: ChatConfig }): ChatConfig {
  return { ...defaultChatConfig(), ...(config.chat || {}) };
}

function ChatStagesSection({
  chat,
  label,
  onChange,
}: {
  chat: ChatConfig;
  label: string;
  onChange: (chat: ChatConfig) => void;
}) {
  return (
    <div className="space-y-3 border-t border-border/60 pt-4">
      <label className={`${label} font-medium`}>Chat quality</label>
      <p className="text-xs text-muted-foreground">
        Optional steps that refine chat search and answers (nearby chunks,
        rewrite, multi-query, multi-hop, timings). Memory is on by default for
        conversation context; other stages start off.
      </p>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-xs text-muted-foreground">Top K</label>
          <Input
            type="number"
            min={1}
            max={50}
            value={chat.top_k}
            onChange={(e) => onChange({ ...chat, top_k: Number(e.target.value) })}
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">
            Neighbor expand (±N)
          </label>
          <Input
            type="number"
            min={0}
            max={5}
            value={chat.context_window}
            onChange={(e) =>
              onChange({ ...chat, context_window: Number(e.target.value) })
            }
          />
        </div>
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={chat.memory.enabled}
          onChange={(e) =>
            onChange({
              ...chat,
              memory: { ...chat.memory, enabled: e.target.checked },
            })
          }
        />
        Session memory (conversation context)
      </label>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={chat.optimization.enabled}
          onChange={(e) =>
            onChange({
              ...chat,
              optimization: { ...chat.optimization, enabled: e.target.checked },
            })
          }
        />
        Query rewrite & clarify
      </label>
      {chat.optimization.enabled && (
        <div className="ml-5 space-y-1">
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={chat.optimization.rewrite}
              onChange={(e) =>
                onChange({
                  ...chat,
                  optimization: { ...chat.optimization, rewrite: e.target.checked },
                })
              }
            />
            Rewrite using chat history
          </label>
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={chat.optimization.clarify}
              onChange={(e) =>
                onChange({
                  ...chat,
                  optimization: { ...chat.optimization, clarify: e.target.checked },
                })
              }
            />
            Ask when the question is unclear
          </label>
        </div>
      )}

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={chat.multi_query.enabled}
          onChange={(e) =>
            onChange({
              ...chat,
              multi_query: { ...chat.multi_query, enabled: e.target.checked },
            })
          }
        />
        Multi-query search
      </label>
      {chat.multi_query.enabled && (
        <div className="ml-5">
          <label className="text-xs text-muted-foreground">Query variants</label>
          <Input
            type="number"
            min={2}
            max={8}
            value={chat.multi_query.count}
            onChange={(e) =>
              onChange({
                ...chat,
                multi_query: { ...chat.multi_query, count: Number(e.target.value) },
              })
            }
          />
        </div>
      )}

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={chat.multihop.enabled}
          onChange={(e) =>
            onChange({
              ...chat,
              multihop: { ...chat.multihop, enabled: e.target.checked },
            })
          }
        />
        Multi-hop (break into sub-questions)
      </label>
      {chat.multihop.enabled && (
        <div className="ml-5">
          <label className="text-xs text-muted-foreground">Max hops</label>
          <Input
            type="number"
            min={1}
            max={5}
            value={chat.multihop.max_hops}
            onChange={(e) =>
              onChange({
                ...chat,
                multihop: { ...chat.multihop, max_hops: Number(e.target.value) },
              })
            }
          />
        </div>
      )}

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={chat.debug}
          onChange={(e) => onChange({ ...chat, debug: e.target.checked })}
        />
        Show stage timings in chat
      </label>
    </div>
  );
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
                strategy: e.target.value as GraphRagConfig['extraction']['strategy'],
              },
            })
          }
        >
          <option value="ocr">OCR</option>
          <option value="vlm">VLM</option>
          <option value="docling">Docling</option>
          <option value="hybrid_pdf">Hybrid PDF</option>
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

        <ChatStagesSection
          chat={resolveChat(graphConfig)}
          label={label}
          onChange={(chat) => updateGraph({ chat })}
        />
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
              extraction: {
                ...vectorConfig.extraction,
                strategy: e.target.value as VectorRagConfig['extraction']['strategy'],
              },
            })
          }
        >
          <option value="ocr">OCR</option>
          <option value="vlm">VLM</option>
          <option value="docling">Docling</option>
          <option value="hybrid_pdf">Hybrid PDF</option>
        </select>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={vectorConfig.extraction.extract_hierarchy !== false}
            onChange={(e) =>
              updateVector({
                extraction: {
                  ...vectorConfig.extraction,
                  extract_hierarchy: e.target.checked,
                },
              })
            }
          />
          Extract heading hierarchy into chunk metadata
        </label>
      </div>

      <div className="space-y-2">
        <label className={`${label} font-medium`}>Chunking</label>
        <select
          className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
          value={vectorConfig.chunking.strategy}
          onChange={(e) => {
            const strategy = e.target.value as ChunkingStrategy;
            let params: Record<string, number | boolean | string | null>;
            if (strategy === 'fixed_window') {
              params = { chunk_size: 512, overlap: 50 };
            } else if (strategy === 'recursive') {
              params = { chunk_size: 512, overlap: 50, preserve_structure: true };
            } else if (strategy === 'semantic') {
              params = {
                similarity_threshold: 0.5,
                min_chunk_size: 100,
                max_chunk_size: 1000,
                breakpoint_threshold_type: 'percentile',
                buffer_size: 1,
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
          <option value="recursive">Recursive (structure-preserving)</option>
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
        {vectorConfig.chunking.strategy === 'recursive' && (
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={vectorConfig.chunking.params.preserve_structure !== false}
              onChange={(e) =>
                updateVector({
                  chunking: {
                    ...vectorConfig.chunking,
                    params: {
                      ...vectorConfig.chunking.params,
                      preserve_structure: e.target.checked,
                    },
                  },
                })
              }
            />
            Preserve tables / code fences as atomic units
          </label>
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
        <label className={`${label} font-medium`}>Hierarchical summaries</label>
        <p className="text-xs text-muted-foreground">
          Celery summary queue builds cluster + document manifesto after ingest.
        </p>
        {(() => {
          const summaries: HierarchicalSummaryConfig = {
            ...defaultSummariesConfig(),
            ...(vectorConfig.summaries || {}),
          };
          return (
            <>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={summaries.enabled}
                  onChange={(e) =>
                    updateVector({
                      summaries: { ...summaries, enabled: e.target.checked },
                    })
                  }
                />
                Build summaries after indexing
              </label>
              <div>
                <label className="text-xs text-muted-foreground">
                  Hierarchy retrieval mode
                </label>
                <select
                  className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
                  value={summaries.retrieval_mode}
                  onChange={(e) =>
                    updateVector({
                      summaries: {
                        ...summaries,
                        retrieval_mode: e.target.value as HierarchyRetrievalMode,
                      },
                    })
                  }
                >
                  <option value="chunks_only">Chunks only</option>
                  <option value="summaries_first">Summaries first (expand members)</option>
                  <option value="mixed">Mixed (summaries + chunks)</option>
                </select>
              </div>
            </>
          );
        })()}
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

      <ChatStagesSection
        chat={resolveChat(vectorConfig)}
        label={label}
        onChange={(chat) => updateVector({ chat })}
      />
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
