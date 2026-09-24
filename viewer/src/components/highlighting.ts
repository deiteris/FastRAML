import hljs from 'highlight.js/lib/core';
import bash from 'highlight.js/lib/languages/bash';
import csharp from 'highlight.js/lib/languages/csharp';
import css from 'highlight.js/lib/languages/css';
import go from 'highlight.js/lib/languages/go';
import graphql from 'highlight.js/lib/languages/graphql';
import http from 'highlight.js/lib/languages/http';
import ini from 'highlight.js/lib/languages/ini';
import java from 'highlight.js/lib/languages/java';
import javascript from 'highlight.js/lib/languages/javascript';
import json from 'highlight.js/lib/languages/json';
import markdown from 'highlight.js/lib/languages/markdown';
import plaintext from 'highlight.js/lib/languages/plaintext';
import python from 'highlight.js/lib/languages/python';
import shell from 'highlight.js/lib/languages/shell';
import sql from 'highlight.js/lib/languages/sql';
import typescript from 'highlight.js/lib/languages/typescript';
import xml from 'highlight.js/lib/languages/xml';
import yaml from 'highlight.js/lib/languages/yaml';

/*
 * The languages an API document shows: its payloads (JSON, XML, YAML, form
 * data), requests (HTTP, shell), and the client snippets a documentation page
 * carries. `lib/common` registers some forty and was most of the bundle. A
 * fence naming a language outside this list is detected among these, and left
 * plain where none fits. Fewer candidates also make detection better.
 */
const LANGUAGES = { bash, csharp, css, go, graphql, http, ini, java, javascript, json, markdown, plaintext, python, shell, sql, typescript, xml, yaml };
for (const [name, language] of Object.entries(LANGUAGES)) hljs.registerLanguage(name, language);

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
