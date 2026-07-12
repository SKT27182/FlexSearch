import { useRef, useState } from 'react';
import { Globe, Loader2 } from 'lucide-react';
import {
  Button,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  Input,
  Progress,
} from '@/components/ui';
import { websiteApi, type JobProgressEvent } from '@/lib/api';

interface WebsiteCrawlDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  onQueued?: () => void;
}

export function WebsiteCrawlDialog({
  open,
  onOpenChange,
  projectId,
  onQueued,
}: WebsiteCrawlDialogProps) {
  const [url, setUrl] = useState('https://');
  const [maxDepth, setMaxDepth] = useState(2);
  const [maxPages, setMaxPages] = useState(25);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  const applyEvent = (ev: JobProgressEvent) => {
    if (typeof ev.progress === 'number') setProgress(ev.progress);
    if (ev.message) setMessage(ev.message);
  };

  const handleStart = async () => {
    if (!url.trim() || running) return;
    setRunning(true);
    setProgress(0);
    setMessage('Submitting crawl…');
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const { job_id } = await websiteApi.crawl(projectId, {
        url: url.trim(),
        max_depth: maxDepth,
        max_pages: maxPages,
      });
      onQueued?.();
      await websiteApi.streamJob(job_id, {
        signal: controller.signal,
        onSnapshot: applyEvent,
        onProgress: (ev) => {
          applyEvent(ev);
          if (ev.event === 'page_complete') onQueued?.();
        },
        onClose: () => {
          setMessage((m) => m || 'Crawl finished');
          setProgress(100);
        },
        onError: (detail) => setMessage(detail),
      });
    } catch (error) {
      if ((error as Error)?.name !== 'AbortError') {
        console.error(error);
        setMessage('Crawl failed');
      }
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !running && onOpenChange(v)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Globe className="h-4 w-4" />
            Crawl website
          </DialogTitle>
        </DialogHeader>
        <div className="p-4 space-y-4">
          <div>
            <label className="text-xs text-muted-foreground">Start URL</label>
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={running}
              placeholder="https://example.com/docs"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-muted-foreground">Max depth</label>
              <Input
                type="number"
                min={0}
                max={10}
                value={maxDepth}
                disabled={running}
                onChange={(e) => setMaxDepth(Number(e.target.value))}
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground">Max pages</label>
              <Input
                type="number"
                min={1}
                max={500}
                value={maxPages}
                disabled={running}
                onChange={(e) => setMaxPages(Number(e.target.value))}
              />
            </div>
          </div>
          {(running || progress > 0) && (
            <div className="space-y-2">
              <Progress value={progress} />
              <p className="text-xs text-muted-foreground">{message}</p>
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="outline" disabled={running} onClick={() => onOpenChange(false)}>
              Close
            </Button>
            <Button onClick={() => void handleStart()} disabled={running || !url.trim()}>
              {running ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Start crawl'}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
