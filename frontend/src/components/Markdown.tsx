import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';

interface MarkdownProps {
  content: string;
  className?: string;
}

const markdownComponents: Components = {
  code: ({ className, children, ...props }) => {
    const match = /language-(\w+)/.exec(className || '');
    return match ? (
      <div className="my-3 rounded-lg overflow-hidden border border-border">
        <div className="px-3 py-1 bg-muted text-xs text-muted-foreground font-mono border-b border-border">
          {match[1]}
        </div>
        <pre className="p-3 bg-muted/50 overflow-x-auto">
          <code className="text-xs font-mono" {...props}>{children}</code>
        </pre>
      </div>
    ) : (
      <code className="px-1.5 py-0.5 rounded bg-muted text-sm font-mono" {...props}>{children}</code>
    );
  },
  table: ({ children }) => <div className="my-3 overflow-x-auto"><table className="min-w-full border border-border rounded-lg text-sm">{children}</table></div>,
  thead: ({ children }) => <thead className="bg-muted">{children}</thead>,
  th: ({ children }) => <th className="px-3 py-2 text-left font-semibold border-b border-border">{children}</th>,
  td: ({ children }) => <td className="px-3 py-2 border-b border-border last:border-0">{children}</td>,
  h1: ({ children }) => <h1 className="text-xl font-bold mt-4 mb-2">{children}</h1>,
  h2: ({ children }) => <h2 className="text-lg font-bold mt-3 mb-2">{children}</h2>,
  h3: ({ children }) => <h3 className="text-base font-bold mt-2 mb-1">{children}</h3>,
  ul: ({ children }) => <ul className="list-disc list-inside my-2 space-y-1">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal list-inside my-2 space-y-1">{children}</ol>,
  a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline hover:text-primary/80">{children}</a>,
  hr: () => <hr className="my-4 border-t border-border" />,
  blockquote: ({ children }) => <blockquote className="border-l-4 border-primary pl-4 my-2 italic text-muted-foreground">{children}</blockquote>,
  p: ({ children }) => <p className="my-1">{children}</p>,
};

function safeUrl(url: string): string {
  if (url.startsWith('#') || url.startsWith('/') || /^https?:\/\//i.test(url)) return url;
  return '';
}

export const Markdown = memo(({ content, className = '' }: MarkdownProps) => {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={markdownComponents}
        urlTransform={safeUrl}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});

Markdown.displayName = 'Markdown';
