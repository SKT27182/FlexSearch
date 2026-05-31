import { useEffect } from 'react';
import { ragApi } from '@/lib/api';
import type { ChunkingStrategy, RagConfig, RetrievalStrategy } from '@/lib/rag-types';
import { Input } from '@/components/ui';

interface RagConfigFormProps {
  value: RagConfig;
  onChange: (config: RagConfig) => void;
  compact?: boolean;
}

const defaultConfig = (): RagConfig => ({
  extraction: { strategy: 'ocr' },
  chunking: { strategy: 'fixed_window', params: { chunk_size: 512, overlap: 50 } },
  retrieval: { strategy: 'dense', params: {} },
  reranking: { strategy: 'none', params: {} },
});

export function RagConfigForm({ value, onChange, compact }: RagConfigFormProps) {
  useEffect(() => {
    ragApi.getOptions().catch(() => undefined);
  }, []);

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

      <div className="space-y-2">
        <label className={`${label} font-medium`}>Default retrieval</label>
        <select
          className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
          value={value.retrieval.strategy}
          onChange={(e) => {
            const strategy = e.target.value as RetrievalStrategy;
            const params =
              strategy === 'hybrid'
                ? { rrf_k: 60 }
                : strategy === 'bm25'
                  ? { k1: 1.5, b: 0.75 }
                  : {};
            update({ retrieval: { strategy, params } });
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
    </div>
  );
}

export { defaultConfig as defaultRagConfig };
