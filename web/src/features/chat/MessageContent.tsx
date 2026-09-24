import { useMemo, useState, type MouseEvent } from "react";
import DOMPurify from "dompurify";
import { Marked } from "marked";
import { useI18n } from "@/shared/i18n";

function escapeCode(text: string): string {
  return text.replace(/[&<>"']/g, (char) => {
    const entities: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[char];
  });
}

export function MessageContent({ content }: { content: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const html = useMemo(() => {
    const parser = new Marked({
      gfm: true,
      async: false,
      renderer: {
        html({ text }) {
          return escapeCode(text);
        },
        code({ text }) {
          return `<div class="my-2 min-w-0 overflow-hidden rounded-md border border-border bg-background"><button type="button" data-copy-code="true" class="block ml-auto border-b border-l border-border px-2 py-1 text-xs">${escapeCode(t("chat_code_copy"))}</button><pre class="overflow-x-auto p-3 text-xs"><code>${escapeCode(text)}</code></pre></div>`;
        },
      },
    });
    const parsed = parser.parse(content) as string;
    return DOMPurify.sanitize(parsed, {
      ALLOWED_TAGS: [
        "a",
        "blockquote",
        "br",
        "button",
        "code",
        "del",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
      ],
      ALLOWED_ATTR: ["href", "title", "class", "type", "data-copy-code"],
    });
  }, [content, t]);

  const copyCode = async (event: MouseEvent<HTMLDivElement>) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const button = target.closest<HTMLButtonElement>("button[data-copy-code]");
    if (!button || !event.currentTarget.contains(button)) return;
    const code = button.parentElement?.querySelector("pre code")?.textContent;
    if (code == null) return;
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div
      className="min-w-0 break-words [&_a]:underline [&_ol]:list-decimal [&_ol]:pl-5 [&_p+p]:mt-2 [&_ul]:list-disc [&_ul]:pl-5"
      onClick={(event) => void copyCode(event)}
    >
      <div dangerouslySetInnerHTML={{ __html: html }} />
      <span className="sr-only" role="status">
        {copied ? t("chat_code_copied") : ""}
      </span>
    </div>
  );
}
