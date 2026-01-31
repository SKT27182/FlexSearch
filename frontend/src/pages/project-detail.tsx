import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, Upload, FileText, Trash2, Loader2, CheckCircle, XCircle, Clock, RefreshCw } from 'lucide-react';
import { useProjectStore } from '@/stores';
import { Button, Card, CardHeader, CardTitle, CardContent, buttonVariants } from '@/components/ui';
import { cn, formatFileSize, formatRelativeTime } from '@/lib/utils';
import { documentsApi, projectsApi, type Document, type Project } from '@/lib/api';

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { selectProject } = useProjectStore();

  const [project, setProject] = useState<Project | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);

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
      case 'COMPLETED':
        return <CheckCircle className="h-4 w-4 text-success" />;
      case 'FAILED':
        return <XCircle className="h-4 w-4 text-destructive" />;
      case 'PROCESSING':
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
    <div className="p-8 animate-fade-in">
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
        <Link 
          to={`/chat?project=${project.id}`}
          className={cn(buttonVariants())}
        >
          Start Chat
        </Link>
      </div>

      {/* Upload Area */}
      <Card
        className={cn(
          'mb-8 border-2 border-dashed transition-colors',
          dragActive ? 'border-primary bg-primary/5' : 'border-border'
        )}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
      >
        <CardContent className="py-12">
          <div className="text-center">
            <Upload className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
            <h3 className="text-lg font-medium mb-1">Upload Documents</h3>
            <p className="text-muted-foreground text-sm mb-4">
              Drag and drop files or click to browse
            </p>
            <p className="text-xs text-muted-foreground mb-4">
              Supports: PDF, TXT, MD, PNG, JPG, JPEG
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
              onClick={() => fileInputRef.current?.click()}
              disabled={isUploading}
            >
              {isUploading ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Uploading...
                </>
              ) : (
                <>
                  <Upload className="h-4 w-4 mr-2" />
                  Select Files
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Documents List */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Documents ({documents.length})</CardTitle>
        </CardHeader>
        <CardContent>
          {documents.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <FileText className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p>No documents yet</p>
              <p className="text-sm">Upload files to build your knowledge base</p>
            </div>
          ) : (
            <div className="divide-y divide-border">
              {documents.map((doc) => (
                <div
                  key={doc.id}
                  className="flex items-center gap-4 py-4 group"
                >
                  <div className="p-2.5 rounded-lg bg-secondary">
                    <FileText className="h-5 w-5 text-primary" />
                  </div>

                  <div className="flex-1 min-w-0">
                    <p className="font-medium truncate">{doc.filename}</p>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground">
                      <span>{formatFileSize(doc.size_bytes)}</span>
                      <span>•</span>
                      <span>{doc.chunk_count} chunks</span>
                      <span>•</span>
                      <span>{formatRelativeTime(doc.created_at)}</span>
                    </div>
                  </div>

                  <div className="flex items-center gap-3">
                    <div className="flex items-center gap-1.5">
                      {getStatusIcon(doc.status)}
                      <span className="text-sm capitalize">{doc.status.toLowerCase()}</span>
                    </div>

                    <Button
                      variant="ghost"
                      size="icon"
                      className="opacity-0 group-hover:opacity-100 transition-opacity"
                      onClick={() => handleDelete(doc.id)}
                    >
                      <Trash2 className="h-4 w-4 text-destructive" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
