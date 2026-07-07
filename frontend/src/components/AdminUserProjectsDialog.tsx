import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, FolderOpen, FileText, Loader2, Trash2 } from 'lucide-react';
import {
  adminApi,
  type AdminUserProjectsResponse,
  type AdminUserStats,
} from '@/lib/api';
import { Button } from '@/components/ui';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { formatFileSize, formatRelativeTime } from '@/lib/utils';

interface AdminUserProjectsDialogProps {
  user: AdminUserStats | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChanged?: () => void;
}

export function AdminUserProjectsDialog({
  user,
  open,
  onOpenChange,
  onChanged,
}: AdminUserProjectsDialogProps) {
  const [data, setData] = useState<AdminUserProjectsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [expandedProjects, setExpandedProjects] = useState<Set<string>>(new Set());
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user) return;
    setLoading(true);
    setError('');
    try {
      const response = await adminApi.getUserProjects(user.user_id);
      setData(response);
      setExpandedProjects(new Set());
    } catch (err) {
      console.error(err);
      setError('Failed to load user projects.');
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    if (open && user) {
      void load();
    }
  }, [open, user, load]);

  const toggleProject = (projectId: string) => {
    setExpandedProjects((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  };

  const handleDeleteProject = async (projectId: string, name: string) => {
    if (!confirm(`Delete project "${name}" and all its documents?`)) return;
    setBusyId(projectId);
    try {
      await adminApi.deleteProject(projectId);
      await load();
      onChanged?.();
    } catch (err) {
      console.error(err);
      alert('Failed to delete project.');
    } finally {
      setBusyId(null);
    }
  };

  const handleDeleteDocument = async (documentId: string, filename: string) => {
    if (!confirm(`Delete document "${filename}"?`)) return;
    setBusyId(documentId);
    try {
      await adminApi.deleteDocument(documentId);
      await load();
      onChanged?.();
    } catch (err) {
      console.error(err);
      alert('Failed to delete document.');
    } finally {
      setBusyId(null);
    }
  };

  if (!user) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Projects — {user.email}</DialogTitle>
          <p className="text-sm text-muted-foreground mt-1">
            Role: {user.role === 'INFRA_ADMIN' ? 'Infra Admin' : user.role === 'ADMIN' ? 'Admin' : 'User'}
            {' · '}
            {user.project_count} project(s), {user.document_count} document(s)
          </p>
        </DialogHeader>
        <DialogBody>
          {loading ? (
            <div className="flex justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : error ? (
            <p className="text-sm text-destructive">{error}</p>
          ) : !data || data.projects.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">
              No projects for this user.
            </p>
          ) : (
            <div className="space-y-2">
              {data.projects.map((project) => {
                const expanded = expandedProjects.has(project.id);
                const modeLabel =
                  project.rag_mode === 'graph' ? 'Graph RAG' : 'Vector RAG';
                return (
                  <div key={project.id} className="rounded-md border border-border">
                    <div className="flex items-center gap-2 p-3">
                      <button
                        type="button"
                        className="p-1 rounded hover:bg-secondary"
                        onClick={() => toggleProject(project.id)}
                        aria-label={expanded ? 'Collapse project' : 'Expand project'}
                      >
                        {expanded ? (
                          <ChevronDown className="h-4 w-4" />
                        ) : (
                          <ChevronRight className="h-4 w-4" />
                        )}
                      </button>
                      <FolderOpen className="h-4 w-4 text-primary shrink-0" />
                      <div className="flex-1 min-w-0">
                        <p className="font-medium truncate">{project.name}</p>
                        <p className="text-xs text-muted-foreground">
                          {modeLabel} · {project.document_count} doc(s) ·{' '}
                          {formatRelativeTime(project.created_at)}
                        </p>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="text-destructive hover:bg-destructive/10"
                        disabled={busyId === project.id}
                        onClick={() => handleDeleteProject(project.id, project.name)}
                      >
                        {busyId === project.id ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Trash2 className="h-4 w-4" />
                        )}
                      </Button>
                    </div>
                    {expanded && (
                      <div className="border-t border-border bg-secondary/20 px-3 py-2 space-y-1">
                        {project.documents.length === 0 ? (
                          <p className="text-xs text-muted-foreground py-2 pl-8">
                            No documents in this project.
                          </p>
                        ) : (
                          project.documents.map((doc) => (
                            <div
                              key={doc.id}
                              className="flex items-center gap-2 pl-8 py-1.5 rounded hover:bg-secondary/40"
                            >
                              <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                              <div className="flex-1 min-w-0">
                                <p className="text-sm truncate">{doc.filename}</p>
                                <p className="text-[10px] text-muted-foreground">
                                  {formatFileSize(doc.size_bytes)} · {doc.status} ·{' '}
                                  {doc.chunk_count} chunks
                                </p>
                              </div>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7 text-destructive hover:bg-destructive/10"
                                disabled={busyId === doc.id}
                                onClick={() => handleDeleteDocument(doc.id, doc.filename)}
                              >
                                {busyId === doc.id ? (
                                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                  <Trash2 className="h-3.5 w-3.5" />
                                )}
                              </Button>
                            </div>
                          ))
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
