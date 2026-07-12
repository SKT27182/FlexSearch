import { useRef, useState } from 'react';
import { Archive, Download, Loader2, Upload } from 'lucide-react';
import {
  Button,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  Progress,
} from '@/components/ui';
import { bulkApi, type JobProgressEvent } from '@/lib/api';

interface BulkImportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  onQueued?: () => void;
}

export function BulkImportDialog({
  open,
  onOpenChange,
  projectId,
  onQueued,
}: BulkImportDialogProps) {
  const [running, setRunning] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const applyEvent = (ev: JobProgressEvent) => {
    if (typeof ev.progress === 'number') setProgress(ev.progress);
    if (ev.message) setMessage(ev.message);
  };

  const handleImport = async (file: File | null) => {
    if (!file || running) return;
    setRunning(true);
    setProgress(0);
    setMessage('Uploading .ragpack…');
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const { job_id } = await bulkApi.importPack(projectId, file);
      onQueued?.();
      await bulkApi.streamJob(job_id, {
        signal: controller.signal,
        onSnapshot: applyEvent,
        onProgress: (ev) => {
          applyEvent(ev);
          if (ev.event === 'document_complete') onQueued?.();
        },
        onClose: () => {
          setMessage((m) => m || 'Import finished');
          setProgress(100);
        },
        onError: (detail) => setMessage(detail),
      });
    } catch (error) {
      if ((error as Error)?.name !== 'AbortError') {
        console.error(error);
        setMessage('Import failed');
      }
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const blob = await bulkApi.exportPack(projectId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `project-${projectId.slice(0, 8)}.ragpack.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      console.error(error);
      setMessage('Export failed — need completed documents');
    } finally {
      setExporting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !running && onOpenChange(v)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Archive className="h-4 w-4" />
            Bulk import / export
          </DialogTitle>
        </DialogHeader>
        <div className="p-4 space-y-4">
          <p className="text-sm text-muted-foreground">
            Import a <code>.ragpack</code> / <code>.zip</code> with{' '}
            <code>manifest.json</code>, or export this project&apos;s completed documents.
          </p>
          <input
            ref={fileRef}
            type="file"
            accept=".ragpack,.zip,.ragpack.zip"
            className="hidden"
            onChange={(e) => void handleImport(e.target.files?.[0] ?? null)}
          />
          {(running || progress > 0) && (
            <div className="space-y-2">
              <Progress value={progress} />
              <p className="text-xs text-muted-foreground">{message}</p>
            </div>
          )}
          {!running && message && progress === 0 && (
            <p className="text-xs text-destructive">{message}</p>
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" disabled={running} onClick={() => onOpenChange(false)}>
              Close
            </Button>
            <Button
              variant="secondary"
              disabled={running || exporting}
              onClick={() => void handleExport()}
            >
              {exporting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <>
                  <Download className="h-4 w-4 mr-1" />
                  Export
                </>
              )}
            </Button>
            <Button
              disabled={running}
              onClick={() => fileRef.current?.click()}
            >
              {running ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <>
                  <Upload className="h-4 w-4 mr-1" />
                  Import .ragpack
                </>
              )}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
