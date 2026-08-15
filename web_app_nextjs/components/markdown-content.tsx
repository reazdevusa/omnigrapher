"use client";

import * as React from "react";
import { useRef, useState, memo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import { Copy, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function Pre({
  children,
  ...props
}: React.HTMLAttributes<HTMLPreElement> & { children?: React.ReactNode }) {
  const [copied, setCopied] = useState(false);
  const preRef = useRef<HTMLPreElement>(null);

  const codeEl = React.isValidElement(children) ? children : null;
  const className = ((codeEl?.props as any)?.className as string) || "";
  const language = className
    .replace("language-", "")
    .split(" ")
    .find((c) => c) || "text";

  const handleCopy = async () => {
    const text = preRef.current?.innerText ?? "";
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {}
  };

  return (
    <div className="my-3 rounded-md border border-border bg-card overflow-hidden">
      <div className="flex items-center justify-between px-3 py-1.5 bg-muted border-b border-border">
        <span className="text-xs font-mono text-muted-foreground uppercase">
          {language}
        </span>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-6 w-6"
          onClick={handleCopy}
          aria-label="Copy code"
        >
          {copied ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
        </Button>
      </div>
      <pre
        ref={preRef}
        {...props}
        className={cn("m-0 p-3 overflow-x-auto text-sm", props.className)}
      >
        {children}
      </pre>
    </div>
  );
}

function MarkdownContentImpl({
  content,
  className,
}: {
  content: string;
  className?: string;
}) {
  return (
    <div className={cn("prose prose-sm max-w-none", className)}>
      <ReactMarkdown
        rehypePlugins={[rehypeHighlight]}
        components={{ pre: Pre }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export const MarkdownContent = memo(MarkdownContentImpl);
