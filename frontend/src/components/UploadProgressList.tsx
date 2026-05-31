import type { Document } from '@/lib/api';
import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/utils';

const STATUS_LABELS: Record<string, string> = {
  uploaded: 'Uploaded',
  stored: 'Stored',
  extracting: 'Extracting',
  extracted: 'Text ready',
  chunking: 'Chunking',
  indexing: 'Indexing',
  completed: 'Completed',
  failed: 'Failed',
};

interface UploadProgressListProps {
  items: Document[];
  onItemClick?: (doc: Document) => void;
}

export function UploadProgressList({ items, onItemClick }: UploadProgressListProps) {
  if (items.length === 0) return null;

  return (
    <div className="space-y-2 mb-4">
      {items.map((doc) => {
        const clickable = onItemClick && doc.status !== 'failed';
        return (
          <div
            key={doc.id}
            className={cn(
              'rounded-md border border-border p-3 text-sm',
              clickable && 'cursor-pointer hover:bg-secondary/40'
            )}
            onClick={() => clickable && onItemClick(doc)}
            onKeyDown={(e) => {
              if (clickable && (e.key === 'Enter' || e.key === ' ')) {
                onItemClick(doc);
              }
            }}
            role={clickable ? 'button' : undefined}
            tabIndex={clickable ? 0 : undefined}
          >
            <div className="flex justify-between gap-2 mb-1">
              <span className="font-medium truncate">{doc.filename}</span>
              <span className="text-xs text-muted-foreground shrink-0">
                {STATUS_LABELS[doc.status] ?? doc.status}
              </span>
            </div>
            {doc.processing_step && (
              <p className="text-xs text-muted-foreground mb-2">{doc.processing_step}</p>
            )}
            {doc.status !== 'completed' && doc.status !== 'failed' && (
              <Progress value={doc.progress_pct} />
            )}
            {doc.status === 'failed' && doc.error_message && (
              <p className="text-xs text-destructive mt-1">{doc.error_message}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}
