import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { documentsApi } from '@/lib/api';
import { Markdown } from '@/components/Markdown';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

interface DocumentPreviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  documentId: string;
  filename: string;
}

export function DocumentPreviewDialog({
  open,
  onOpenChange,
  projectId,
  documentId,
  filename,
}: DocumentPreviewDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{filename}</DialogTitle>
          <p className="text-sm text-muted-foreground">Extracted content</p>
        </DialogHeader>
        <DialogBody>{open && <PreviewContent key={`${projectId}:${documentId}`} projectId={projectId} documentId={documentId} />}</DialogBody>
      </DialogContent>
    </Dialog>
  );
}

function PreviewContent({ projectId, documentId }: { projectId: string; documentId: string }) {
  const [content, setContent] = useState('');
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    documentsApi
      .getContent(projectId, documentId)
      .then(({ content: text, truncated: t }) => {
        if (!active) return;
        setContent(text);
        setTruncated(t);
      })
      .catch(() => { if (active) setError('Could not load extracted text.'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [projectId, documentId]);

  return (
    <>
          {loading && (
            <div className="flex justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
            </div>
          )}
          {error && <p className="text-destructive text-sm">{error}</p>}
          {!loading && !error && (
            <>
              {truncated && (
                <p className="text-xs text-amber-600 mb-3">
                  Content was truncated for display.
                </p>
              )}
              <div className="prose prose-sm dark:prose-invert max-w-none">
                <Markdown content={content} />
              </div>
            </>
          )}
    </>
  );
}
