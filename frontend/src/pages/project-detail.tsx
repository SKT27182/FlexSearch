import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  ArrowLeft,
  Upload,
  FileText,
  Trash2,
  Loader2,
  CheckCircle,
  XCircle,
  RefreshCw,
  Search,
  Settings,
  Download,
  Network,
} from 'lucide-react';
import { useProjectStore } from '@/stores';
import { Button, Card, CardHeader, CardTitle, CardContent, buttonVariants, Input } from '@/components/ui';
import { cn, formatFileSize } from '@/lib/utils';
import {
  documentsApi,
  projectsApi,
  retrievalApi,
  type Document,
  type Project,
  type RetrievedChunk,
} from '@/lib/api';
import { ResizableShell } from '@/components/ResizableShell';
import { RagConfigForm } from '@/components/RagConfigForm';
import { DocumentPreviewDialog } from '@/components/DocumentPreviewDialog';
import { UploadProgressList } from '@/components/UploadProgressList';
import { subscribeProjectDocuments } from '@/hooks/useDocumentStatusStream';
import { canPreview, isGraphMode, type DocumentStatusEvent, type RagConfig, type RagMode, type RetrievalOverrides } from '@/lib/rag-types';
import { defaultRagConfigForMode } from '@/components/RagConfigForm';

const PROCESSING_STATUSES = new Set([
  'uploaded',
  'stored',
  'extracting',
  'extracted',
  'chunking',
  'indexing',
]);

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { selectProject } = useProjectStore();

  const [project, setProject] = useState<Project | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [ragDraft, setRagDraft] = useState<RagConfig>(defaultRagConfigForMode('vector'));
  const [savingRag, setSavingRag] = useState(false);
  const [switchingMode, setSwitchingMode] = useState(false);
  const [graphExporting, setGraphExporting] = useState(false);

  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState(5);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [queryOverrides, setQueryOverrides] = useState<RetrievalOverrides>({});
  const [isQuerying, setIsQuerying] = useState(false);
  const [queryResults, setQueryResults] = useState<RetrievedChunk[]>([]);
  const [effectiveStrategy, setEffectiveStrategy] = useState('');
  const [hasQueried, setHasQueried] = useState(false);

  const [previewDoc, setPreviewDoc] = useState<Document | null>(null);
  const [sseActive, setSseActive] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadData = useCallback(async () => {
    if (!id) return;
    setIsLoading(true);
    try {
      const [proj, docs] = await Promise.all([
        projectsApi.get(id),
        documentsApi.list(id),
      ]);
      setProject(proj);
      setRagDraft(proj.rag_config);
      setDocuments(docs);
      selectProject(proj);
    } catch (error) {
      console.error('Failed to load project:', error);
    } finally {
      setIsLoading(false);
    }
  }, [id, selectProject]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const hasProcessing = useMemo(
    () => documents.some((d) => PROCESSING_STATUSES.has(d.status)),
    [documents]
  );

  const applyStatusEvent = useCallback(
    (ev: DocumentStatusEvent) => {
      if (!id) return;
      setDocuments((prev) => {
        const idx = prev.findIndex((d) => d.id === ev.document_id);
        if (idx < 0) {
          return [
            ...prev,
            {
              id: ev.document_id,
              filename: ev.filename ?? 'Document',
              content_type: 'application/octet-stream',
              size_bytes: 0,
              status: ev.status,
              processing_step: ev.processing_step,
              progress_pct: ev.progress_pct,
              chunk_count: ev.chunk_count,
              project_id: id,
              created_at: new Date().toISOString(),
              error_message: ev.error_message,
            },
          ];
        }
        const next = [...prev];
        next[idx] = {
          ...next[idx],
          status: ev.status,
          processing_step: ev.processing_step,
          progress_pct: ev.progress_pct,
          chunk_count: ev.chunk_count,
          error_message: ev.error_message ?? next[idx].error_message,
        };
        return next;
      });
    },
    [id]
  );

  useEffect(() => {
    if (!id || !hasProcessing) {
      setSseActive(false);
      return;
    }
    setSseActive(true);
    return subscribeProjectDocuments(id, applyStatusEvent);
  }, [id, hasProcessing, applyStatusEvent]);

  const processingDocs = useMemo(
    () => documents.filter((d) => PROCESSING_STATUSES.has(d.status)),
    [documents]
  );

  const handleUpload = async (files: FileList | null) => {
    if (!files || !id) return;
    setIsUploading(true);
    try {
      for (const file of Array.from(files)) {
        const doc = await documentsApi.upload(id, file);
        setDocuments((prev) => [doc, ...prev]);
      }
      setSseActive(true);
    } catch (error) {
      console.error('Upload failed:', error);
    } finally {
      setIsUploading(false);
    }
  };

  const handleDelete = async (docId: string) => {
    if (!confirm('Delete this document?')) return;
    try {
      await documentsApi.delete(docId, id!);
      setDocuments((prev) => prev.filter((d) => d.id !== docId));
    } catch (error) {
      console.error('Delete failed:', error);
    }
  };

  const handleSaveRag = async () => {
    if (!id || !project) return;
    setSavingRag(true);
    try {
      const updated = await projectsApi.update(id, { rag_config: ragDraft });
      setProject(updated);
      setRagDraft(updated.rag_config);
      if (
        documents.some((d) => d.status === 'completed') &&
        confirm('Reprocess all documents with new settings?')
      ) {
        await projectsApi.reindex(id, 'auto');
        await loadData();
        setSseActive(true);
      }
    } finally {
      setSavingRag(false);
    }
  };

  const handleQuery = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || !id) return;
    if (isGraphMode(project?.rag_mode) && project?.graph_index_status?.status !== 'ready') {
      alert('Graph index is not ready yet. Wait for indexing to complete.');
      return;
    }
    setIsQuerying(true);
    setHasQueried(true);
    try {
      const response = await retrievalApi.query({
        project_id: id,
        query: query.trim(),
        top_k: topK,
        overrides: showAdvanced ? queryOverrides : undefined,
      });
      setQueryResults(response.chunks);
      setEffectiveStrategy(
        `${response.retrieval_strategy} / ${response.reranking_strategy}`
      );
    } catch (error) {
      console.error('Query failed:', error);
      alert('Query failed. Check console for details.');
    } finally {
      setIsQuerying(false);
    }
  };

  const getStatusIcon = (status: Document['status']) => {
    if (status === 'completed') return <CheckCircle className="h-4 w-4 text-emerald-500" />;
    if (status === 'failed') return <XCircle className="h-4 w-4 text-destructive" />;
    if (PROCESSING_STATUSES.has(status))
      return <Loader2 className="h-4 w-4 text-primary animate-spin" />;
    return null;
  };

  const handleSwitchMode = async (newMode: RagMode) => {
    if (!id || !project) return;
    if (
      !confirm(
        `Switch to ${newMode === 'graph' ? 'Graph RAG' : 'Traditional RAG'}? This wipes the current index and reprocesses all documents.`
      )
    ) {
      return;
    }
    setSwitchingMode(true);
    try {
      await projectsApi.switchRagMode(id, newMode);
      await loadData();
    } finally {
      setSwitchingMode(false);
    }
  };

  const handleGraphExport = async () => {
    if (!id) return;
    setGraphExporting(true);
    try {
      const blob = await projectsApi.downloadGraphExport(id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `graph-export-${id}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      console.error('Export failed:', error);
      alert('Graph export failed. Is the graph index ready?');
    } finally {
      setGraphExporting(false);
    }
  };

  const isGraphProject = isGraphMode(project?.rag_mode);
  const graphReady = project?.graph_index_status?.status === 'ready';

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  if (!project) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="text-center">
          <h2 className="text-xl font-semibold mb-2">Project not found</h2>
          <Link to="/projects" className={cn(buttonVariants({ variant: 'outline' }))}>
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Projects
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="p-8 animate-fade-in max-w-6xl mx-auto">
      <div className="flex items-center gap-4 mb-8">
        <Link to="/projects" className={cn(buttonVariants({ variant: 'ghost', size: 'icon' }))}>
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex-1">
          <h1 className="text-3xl font-bold">{project.name}</h1>
          <p className="text-muted-foreground">{project.description || 'No description'}</p>
          <p className="text-xs text-muted-foreground mt-1">
            {isGraphProject ? 'Graph RAG' : 'Vector RAG'} ·{' '}
            {project.rag_config.retrieval.strategy}
            {isGraphProject && (
              <>
                {' '}
                · graph index:{' '}
                <span
                  className={cn(
                    project.graph_index_status?.status === 'ready' && 'text-emerald-600',
                    project.graph_index_status?.status === 'failed' && 'text-destructive'
                  )}
                >
                  {project.graph_index_status?.status}
                </span>
              </>
            )}
          </p>
        </div>
        <Button variant="outline" onClick={() => setShowSettings(!showSettings)}>
          <Settings className="h-4 w-4 mr-2" />
          RAG settings
        </Button>
        <Button variant="outline" onClick={loadData}>
          <RefreshCw className="h-4 w-4 mr-2" />
          Refresh
        </Button>
      </div>

      {showSettings && (
        <Card className="mb-8">
          <CardHeader>
            <CardTitle>Project RAG configuration</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <RagConfigForm
              value={ragDraft}
              onChange={setRagDraft}
              ragMode={project.rag_mode}
            />
            <div className="flex flex-wrap gap-2 items-center">
              <Button onClick={handleSaveRag} isLoading={savingRag}>
                Save settings
              </Button>
              <Button
                variant="outline"
                onClick={async () => {
                  if (!id) return;
                  await projectsApi.reindex(id, 'full');
                  setSseActive(true);
                  await loadData();
                }}
              >
                Reprocess all (full)
              </Button>
              {isGraphProject && (
                <Button
                  variant="outline"
                  onClick={async () => {
                    if (!id) return;
                    await projectsApi.rebuildGraphIndex(id);
                    await loadData();
                  }}
                >
                  Rebuild graph index
                </Button>
              )}
              <div className="ml-auto flex gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={switchingMode || project.rag_mode === 'vector'}
                  onClick={() => handleSwitchMode('vector')}
                >
                  Switch to Vector RAG
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={switchingMode || project.rag_mode === 'graph'}
                  onClick={() => handleSwitchMode('graph')}
                >
                  Switch to Graph RAG
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      <ResizableShell
        left={
          <div className="space-y-8">
            {processingDocs.length > 0 && (
              <UploadProgressList
                items={processingDocs}
                onItemClick={(doc) => canPreview(doc.status) && setPreviewDoc(doc)}
              />
            )}

            <Card
              className={cn(
                'border-2 border-dashed transition-colors',
                dragActive ? 'border-primary bg-primary/5' : 'border-border'
              )}
              onDragEnter={(e) => {
                e.preventDefault();
                setDragActive(true);
              }}
              onDragLeave={(e) => {
                e.preventDefault();
                setDragActive(false);
              }}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                setDragActive(false);
                handleUpload(e.dataTransfer.files);
              }}
            >
              <CardContent className="py-8">
                <div className="text-center">
                  <Upload className="h-8 w-8 mx-auto text-muted-foreground mb-3" />
                  <h3 className="text-md font-medium mb-1">Upload Documents</h3>
                  <p className="text-muted-foreground text-xs mb-3">PDF, TXT, MD, Images</p>
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    accept=".pdf,.txt,.md,.png,.jpg,.jpeg"
                    className="hidden"
                    onChange={(e) => handleUpload(e.target.files)}
                  />
                  <Button
                    size="sm"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isUploading}
                  >
                    {isUploading ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      'Select Files'
                    )}
                  </Button>
                  {sseActive && (
                    <p className="text-xs text-primary mt-2">Live updates via SSE</p>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="py-4">
                <CardTitle className="text-lg">Documents ({documents.length})</CardTitle>
              </CardHeader>
              <CardContent className="px-2">
                {documents.length === 0 ? (
                  <p className="text-center py-8 text-sm text-muted-foreground">No documents yet</p>
                ) : (
                  <div className="space-y-1">
                    {documents.map((doc) => (
                      <div
                        key={doc.id}
                        className={cn(
                          'flex items-center gap-3 p-3 rounded-md hover:bg-secondary/50 group',
                          canPreview(doc.status) && 'cursor-pointer'
                        )}
                        onClick={() => canPreview(doc.status) && setPreviewDoc(doc)}
                      >
                        <FileText className="h-4 w-4 text-primary shrink-0" />
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium truncate">{doc.filename}</p>
                          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                            <span>{formatFileSize(doc.size_bytes)}</span>
                            <span>•</span>
                            <span className="flex items-center gap-1">
                              {getStatusIcon(doc.status)}
                              {doc.status}
                              {doc.progress_pct > 0 && doc.status !== 'completed' && (
                                <span>({doc.progress_pct}%)</span>
                              )}
                            </span>
                          </div>
                        </div>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 opacity-0 group-hover:opacity-100"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDelete(doc.id);
                          }}
                        >
                          <Trash2 className="h-3.5 w-3.5 text-destructive" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        }
        main={
          <div className="space-y-8">
            {isGraphProject && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Network className="h-5 w-5" />
                    Visualize graph
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <p className="text-muted-foreground">
                    Download parquet + GraphML, then open in an external tool. Graph index:{' '}
                    <strong>{project.graph_index_status?.status}</strong>
                    {project.graph_index_status?.error && (
                      <span className="text-destructive block mt-1">
                        {project.graph_index_status?.error}
                      </span>
                    )}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!graphReady || graphExporting}
                      onClick={handleGraphExport}
                    >
                      {graphExporting ? (
                        <Loader2 className="h-4 w-4 animate-spin mr-2" />
                      ) : (
                        <Download className="h-4 w-4 mr-2" />
                      )}
                      Download graph export
                    </Button>
                    <a
                      href="https://noworneverev.github.io/graphrag-visualizer/"
                      target="_blank"
                      rel="noreferrer"
                      className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))}
                    >
                      GraphRAG Visualizer
                    </a>
                    <a
                      href="https://microsoft.github.io/graphrag/guides/visualization"
                      target="_blank"
                      rel="noreferrer"
                      className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))}
                    >
                      Gephi guide
                    </a>
                  </div>
                </CardContent>
              </Card>
            )}

            <Card className="shadow-md">
              <CardHeader>
                <CardTitle>Search Knowledge Base</CardTitle>
              </CardHeader>
              <CardContent>
                <form onSubmit={handleQuery} className="flex flex-wrap gap-2 mb-4 items-end">
                  <Input
                    placeholder="Ask something about your documents..."
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    disabled={isQuerying}
                    className="flex-1 min-w-[200px]"
                  />
                  <div className="w-20">
                    <label className="text-xs font-medium text-muted-foreground">
                      Top K
                    </label>
                    <Input
                      type="number"
                      min={1}
                      max={50}
                      value={topK}
                      disabled={isQuerying}
                      onChange={(e) => {
                        const n = parseInt(e.target.value, 10);
                        setTopK(Number.isFinite(n) ? Math.min(50, Math.max(1, n)) : 5);
                      }}
                    />
                  </div>
                  <Button type="submit" disabled={isQuerying || !query.trim() || (isGraphProject && !graphReady)}>
                    {isQuerying ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Search className="h-4 w-4" />
                    )}
                    <span className="ml-2">Query</span>
                  </Button>
                </form>

                <button
                  type="button"
                  className="text-sm text-primary mb-4"
                  onClick={() => setShowAdvanced(!showAdvanced)}
                >
                  {showAdvanced ? 'Hide' : 'Show'} advanced retrieval
                </button>

                {showAdvanced && (
                  <div className="grid grid-cols-2 gap-3 mb-6 p-4 rounded-md bg-secondary/30">
                    <div>
                      <label className="text-xs font-medium">Retrieval override</label>
                      <select
                        className="w-full h-10 rounded-md border border-input bg-background px-2 text-sm"
                        value={queryOverrides.retrieval_strategy ?? ''}
                        onChange={(e) =>
                          setQueryOverrides({
                            ...queryOverrides,
                            retrieval_strategy: e.target.value
                              ? (e.target.value as RetrievalOverrides['retrieval_strategy'])
                              : undefined,
                          })
                        }
                      >
                        <option value="">Project default</option>
                        {isGraphProject ? (
                          <>
                            <option value="graph_local">Graph local</option>
                            <option value="graph_global">Graph global</option>
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
                    {!isGraphProject && (
                    <div>
                      <label className="text-xs font-medium">Reranking override</label>
                      <select
                        className="w-full h-10 rounded-md border border-input bg-background px-2 text-sm"
                        value={queryOverrides.reranking_strategy ?? ''}
                        onChange={(e) =>
                          setQueryOverrides({
                            ...queryOverrides,
                            reranking_strategy: e.target.value
                              ? (e.target.value as 'none' | 'cross_encoder')
                              : undefined,
                          })
                        }
                      >
                        <option value="">Project default</option>
                        <option value="none">None</option>
                        <option value="cross_encoder">Cross-encoder</option>
                      </select>
                    </div>
                    )}
                  </div>
                )}

                {hasQueried && effectiveStrategy && (
                  <p className="text-xs text-muted-foreground mb-4">
                    Strategies used: {effectiveStrategy}
                  </p>
                )}

                <div className="space-y-4">
                  {!hasQueried ? (
                    <p className="text-center py-20 text-muted-foreground">
                      Enter a query to retrieve relevant information
                    </p>
                  ) : isQuerying ? (
                    <div className="animate-pulse h-24 bg-secondary rounded" />
                  ) : queryResults.length === 0 ? (
                    <p className="text-center py-20 text-muted-foreground">No results found.</p>
                  ) : (
                    queryResults.map((chunk, index) => (
                      <Card key={index} className="bg-secondary/20 border-none">
                        <CardContent className="pt-4">
                          <div className="flex justify-between mb-2">
                            <span className="text-[10px] font-bold uppercase text-primary">
                              Match {index + 1} ({(chunk.score * 100).toFixed(1)}%)
                            </span>
                            <span className="text-[10px] text-muted-foreground">
                              {chunk.metadata.filename || 'Unknown'}
                            </span>
                          </div>
                          <p className="text-sm leading-relaxed">{chunk.content}</p>
                        </CardContent>
                      </Card>
                    ))
                  )}
                </div>
              </CardContent>
            </Card>
          </div>
        }
      />

      {previewDoc && id && (
        <DocumentPreviewDialog
          open={!!previewDoc}
          onOpenChange={(open) => !open && setPreviewDoc(null)}
          projectId={id}
          documentId={previewDoc.id}
          filename={previewDoc.filename}
        />
      )}
    </div>
  );
}
