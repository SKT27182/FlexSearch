import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, Upload, FileText, Trash2, Loader2, CheckCircle, XCircle, Clock, RefreshCw, Search } from 'lucide-react';
import { useProjectStore } from '@/stores';
import { Button, Card, CardHeader, CardTitle, CardContent, buttonVariants, Input } from '@/components/ui';
import { cn, formatFileSize } from '@/lib/utils';
import { documentsApi, projectsApi, retrievalApi, type Document, type Project, type RetrievedChunk } from '@/lib/api';

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { selectProject } = useProjectStore();

  const [project, setProject] = useState<Project | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);

  // RAG Query states
  const [query, setQuery] = useState('');
  const [isQuerying, setIsQuerying] = useState(false);
  const [queryResults, setQueryResults] = useState<RetrievedChunk[]>([]);
  const [hasQueried, setHasQueried] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  // Load project and documents
  useEffect(() => {
    if (!id) return;

    const loadData = async () => {
      setIsLoading(true);
      try {
        const [proj, docs] = await Promise.all([
          projectsApi.get(id),
          documentsApi.list(id),
        ]);
        setProject(proj);
        setDocuments(docs);
        selectProject(proj);
      } catch (error) {
        console.error('Failed to load project:', error);
      } finally {
        setIsLoading(false);
      }
    };

    loadData();
  }, [id, selectProject]);

  const refreshDocuments = useCallback(async () => {
    if (!id) return;
    const docs = await documentsApi.list(id);
    setDocuments(docs);
  }, [id]);

  // Auto-refresh for processing documents
  useEffect(() => {
    const hasProcessing = documents.some(
      (doc) => doc.status === 'pending' || doc.status === 'processing'
    );

    if (hasProcessing && id) {
      const interval = setInterval(async () => {
        const docs = await documentsApi.list(id);
        setDocuments(docs);
        
        // Stop interval if no more documents are processing
        const stillProcessing = docs.some(
          (doc) => doc.status === 'pending' || doc.status === 'processing'
        );
        if (!stillProcessing) {
          clearInterval(interval);
        }
      }, 3000);
      return () => clearInterval(interval);
    }
  }, [documents, id]);

  const handleUpload = async (files: FileList | null) => {
    if (!files || !id) return;

    setIsUploading(true);
    try {
      for (const file of Array.from(files)) {
        await documentsApi.upload(id, file);
      }
      await refreshDocuments();
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

  const handleQuery = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || !id) return;

    setIsQuerying(true);
    setHasQueried(true);
    try {
      const response = await retrievalApi.query({
        project_id: id,
        query: query.trim(),
        top_k: 5,
      });
      setQueryResults(response.chunks);
    } catch (error) {
      console.error('Query failed:', error);
    } finally {
      setIsQuerying(false);
    }
  };

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    handleUpload(e.dataTransfer.files);
  };

  const getStatusIcon = (status: Document['status']) => {
    switch (status) {
      case 'completed':
        return <CheckCircle className="h-4 w-4 text-emerald-500" />;
      case 'failed':
        return <XCircle className="h-4 w-4 text-destructive" />;
      case 'processing':
        return <Loader2 className="h-4 w-4 text-primary animate-spin" />;
      default:
        return <Clock className="h-4 w-4 text-muted-foreground" />;
    }
  };

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
          <Link 
            to="/projects"
            className={cn(buttonVariants({ variant: 'outline' }))}
          >
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Projects
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="p-8 animate-fade-in max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-4 mb-8">
        <Link 
          to="/projects"
          className={cn(buttonVariants({ variant: 'ghost', size: 'icon' }))}
        >
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex-1">
          <h1 className="text-3xl font-bold">{project.name}</h1>
          <p className="text-muted-foreground">{project.description || 'No description'}</p>
        </div>
        <Button variant="outline" onClick={refreshDocuments}>
          <RefreshCw className="h-4 w-4 mr-2" />
          Refresh
        </Button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-1 space-y-8">
          {/* Upload Area */}
          <Card
            className={cn(
              'border-2 border-dashed transition-colors',
              dragActive ? 'border-primary bg-primary/5' : 'border-border'
            )}
            onDragEnter={handleDrag}
            onDragLeave={handleDrag}
            onDragOver={handleDrag}
            onDrop={handleDrop}
          >
            <CardContent className="py-8">
              <div className="text-center">
                <Upload className="h-8 w-8 mx-auto text-muted-foreground mb-3" />
                <h3 className="text-md font-medium mb-1">Upload Documents</h3>
                <p className="text-muted-foreground text-xs mb-3">
                  PDF, TXT, MD, Images
                </p>
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
              </div>
            </CardContent>
          </Card>

          {/* Documents List */}
          <Card>
            <CardHeader className="py-4">
              <CardTitle className="text-lg">Documents ({documents.length})</CardTitle>
            </CardHeader>
            <CardContent className="px-2">
              {documents.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground">
                  <p className="text-sm">No documents yet</p>
                </div>
              ) : (
                <div className="space-y-1">
                  {documents.map((doc) => (
                    <div
                      key={doc.id}
                      className="flex items-center gap-3 p-3 rounded-md hover:bg-secondary/50 transition-colors group"
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
                          </span>
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 opacity-0 group-hover:opacity-100 transition-opacity"
                        onClick={() => handleDelete(doc.id)}
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

        <div className="lg:col-span-2 space-y-8">
          {/* Query Section */}
          <Card className="shadow-md">
            <CardHeader>
              <CardTitle>Search Knowledge Base</CardTitle>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleQuery} className="flex gap-2 mb-6">
                <Input
                  placeholder="Ask something about your documents..."
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  disabled={isQuerying}
                  className="flex-1"
                />
                <Button type="submit" disabled={isQuerying || !query.trim()}>
                  {isQuerying ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Search className="h-4 w-4" />
                  )}
                  <span className="ml-2">Query</span>
                </Button>
              </form>

              {/* Results */}
              <div className="space-y-6">
                {!hasQueried ? (
                  <div className="text-center py-20 text-muted-foreground">
                    <Search className="h-12 w-12 mx-auto mb-4 opacity-20" />
                    <p>Enter a query to retrieve relevant information from your documents</p>
                  </div>
                ) : isQuerying ? (
                  <div className="space-y-4">
                    {[1, 2, 3].map((i) => (
                      <div key={i} className="animate-pulse flex space-x-4">
                        <div className="flex-1 space-y-4 py-1">
                          <div className="h-4 bg-secondary rounded w-3/4"></div>
                          <div className="space-y-2">
                            <div className="h-4 bg-secondary rounded"></div>
                            <div className="h-4 bg-secondary rounded w-5/6"></div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : queryResults.length === 0 ? (
                  <div className="text-center py-20 text-muted-foreground">
                    <p>No relevant information found for your query.</p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {queryResults.map((chunk, index) => (
                      <Card key={index} className="bg-secondary/20 border-none">
                        <CardContent className="pt-4">
                          <div className="flex justify-between items-start mb-2">
                            <span className="text-[10px] font-bold uppercase tracking-wider text-primary">
                              Match {index + 1} (Score: {(chunk.score * 100).toFixed(1)}%)
                            </span>
                            <span className="text-[10px] text-muted-foreground">
                              Doc: {chunk.metadata.filename || 'Unknown'}
                            </span>
                          </div>
                          <p className="text-sm leading-relaxed text-foreground/90">
                            {chunk.content}
                          </p>
                        </CardContent>
                      </Card>
                    ))}
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
