import hljs from 'highlight.js/lib/common';

export interface Highlighted {
  html: string;
  language: string;
}

/**
 * Highlight code with an author's language when available, otherwise detect it.
 *
 * Auto-detection is limited to strings that look like source. Short prose and
 * identifiers score surprisingly well as some programming languages, and an
 * example containing `Dune` should remain an example rather than become code.
 */
export function highlightCode(source: string, language?: string): Highlighted | null {
  if (language && hljs.getLanguage(language)) {
    const result = hljs.highlight(source, { language, ignoreIllegals: true });
    return { html: result.value, language: result.language ?? language };
  }

  if (!looksLikeSource(source)) return null;
  const result = hljs.highlightAuto(source);
  if (!result.language || result.relevance < 1) return null;
  return { html: result.value, language: result.language };
}

function looksLikeSource(source: string): boolean {
  return /\r?\n/.test(source) || /^\s*(?:[<{\[]|#!|---(?:\s|$))/.test(source);
}
