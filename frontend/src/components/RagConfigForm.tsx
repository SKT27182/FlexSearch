import { useEffect } from 'react';
import { ragApi } from '@/lib/api';
import type {
  ChunkingStrategy,
  GraphRagConfig,
  VectorRagConfig,
  VectorRetrievalStrategy,
} from '@/lib/rag-types';
import { defaultGraphRagConfig, defaultVectorRagConfig } from '@/lib/rag-types';
import { Input } from '@/components/ui';

interface VectorRagConfigFormProps {
  mode: 'vector';
  value: VectorRagConfig;
  onChange: (config: VectorRagConfig) => void;
  compact?: boolean;
}

interface GraphRagConfigFormProps {
  mode: 'graph';
  value: GraphRagConfig;
  onChange: (config: GraphRagConfig) => void;
  compact?: boolean;
}

type RagConfigFormProps = VectorRagConfigFormProps | GraphRagConfigFormProps;

export function RagConfigForm(props: RagConfigFormProps) {
  useEffect(() => {
    ragApi.getOptions(props.mode).catch(() => undefined);
  }, [props.mode]);

  if (props.mode === 'graph') {
    return <GraphForm {...props} />;
  }
  return <VectorForm {...props} />;
}

function VectorForm({ value, onChange, compact }: VectorRagConfigFormProps) {
  const update = (partial: Partial<VectorRagConfig>) => {
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
      </div>

      <div className="space-y-2">
        <label className={`${label} font-medium`}>Default retrieval</label>
        <select
          className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
          value={value.retrieval.strategy}
          onChange={(e) => {
            const strategy = e.target.value as VectorRetrievalStrategy;
            let params: Record<string, number | null> = {};
            if (strategy === 'hybrid') {
              params = { rrf_k: 60 };
            } else if (strategy === 'bm25') {
              params = { k1: 1.5, b: 0.75 };
            }
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

function GraphForm({ value, onChange, compact }: GraphRagConfigFormProps) {
  const update = (partial: Partial<GraphRagConfig>) => {
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
              extraction: {
                ...value.extraction,
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
        <label className={`${label} font-medium`}>Passage size (chars)</label>
        <Input
          type="number"
          value={value.extraction.passage_chunk_size}
          onChange={(e) =>
            update({
              extraction: {
                ...value.extraction,
                passage_chunk_size: Number(e.target.value),
              },
            })
          }
        />
      </div>

      <div className="space-y-2">
        <label className={`${label} font-medium`}>Max entities per passage</label>
        <Input
          type="number"
          value={value.indexing.max_entities_per_passage}
          onChange={(e) =>
            update({
              indexing: {
                ...value.indexing,
                max_entities_per_passage: Number(e.target.value),
              },
            })
          }
        />
      </div>

      <div className="space-y-2">
        <label className={`${label} font-medium`}>Default retrieval</label>
        <select
          className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
          value={value.retrieval.strategy}
          onChange={(e) =>
            update({
              retrieval: {
                strategy: e.target.value as 'graph_local' | 'graph_global',
                params:
                  e.target.value === 'graph_global'
                    ? { top_passages: 5 }
                    : { max_hops: 2, top_entities: 10 },
              },
            })
          }
        >
          <option value="graph_local">Graph local (entity-focused)</option>
          <option value="graph_global">Graph global (thematic)</option>
        </select>
      </div>

      <p className={`${label} text-muted-foreground`}>
        Graph RAG requires an LLM API key for entity extraction during indexing.
      </p>
    </div>
  );
}

export const defaultRagConfig = defaultVectorRagConfig;
export { defaultVectorRagConfig, defaultGraphRagConfig };
