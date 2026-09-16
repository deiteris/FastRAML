/**
 * Descriptions, as the Markdown they are.
 *
 * **Every `description:` in RAML is Markdown** -- the spec says so, of the root,
 * of a type, of a property, of a method, of a response, of a security scheme --
 * and so is a `documentation:` item's `content:`. Showing the source was not a
 * cautious reading of that, it was the wrong output: a list rendered as a column
 * of hyphens, a link as brackets, and an author's hard line wraps as the
 * paragraph shape.
 *
 * ## Why this is safe
 *
 * `html: false` is the whole of it. The parser emits no raw-HTML token at all,
 * so `<script>` in a description arrives as text and is escaped like any other
 * text -- there is nothing to sanitise afterwards, because nothing unsafe is
 * ever produced. That is a stronger position than rendering HTML and cleaning
 * it: a sanitiser has to be right about every construct, and this has to be
 * right about one setting.
 *
 * `validateLink` is markdown-it's own, and rejects `javascript:`, `vbscript:`,
 * `file:` and every `data:` that is not one of four image types -- so
 * `[click](javascript:...)` renders with no `href`. `linkify` is on, which is
 * why the rule below hardens the `rel` of everything it produces as well.
 *
 * That leaves `dangerouslySetInnerHTML`, which is what the two above make
 * ordinary rather than dangerous. `smoke.tsx` asserts it on a hostile string
 * rather than leaving the argument as prose.
 *
 * markdown-it and not `marked` + DOMPurify: the second needs a DOM, and the
 * checks here render to static markup in Node, so the sanitiser would be absent
 * in exactly the run that is supposed to catch its absence.
 */

import MarkdownIt from 'markdown-it';
import { highlightCode } from './highlighting';

const md = new MarkdownIt({
  html: false,
  linkify: true,
  // An author's line breaks are wrapping, not structure. `breaks: true` turns
  // a paragraph wrapped at 76 columns into a stack of short lines, which is
  // what the `<pre>` this replaced was doing.
  breaks: false,
  typographer: false,
  highlight(source, language) {
    return highlightCode(source, language)?.html ?? '';
  },
});

/*
 * A link out of the document opens away from it and carries no window handle:
 * `rel="noopener"` denies `window.opener`, and a description is written by
 * whoever wrote the RAML, who is not necessarily whoever is reading it.
 */
const openLink = md.renderer.rules.link_open ?? ((tokens, at, options, _env, self) => self.renderToken(tokens, at, options));
md.renderer.rules.link_open = (tokens, at, options, env, self) => {
  const token = tokens[at];
  const href = String(token?.attrGet('href') ?? '');
  if (/^[a-z][a-z0-9+.-]*:/i.test(href)) {
    token?.attrSet('target', '_blank');
    token?.attrSet('rel', 'noopener noreferrer nofollow');
  }
  return openLink(tokens, at, options, env, self);
};

/** A description, with its paragraphs, lists and code blocks. */
export function Prose({ children }: { children?: string }) {
  if (!children || children.trim() === '') return null;
  return <div className="prose" dangerouslySetInnerHTML={{ __html: md.render(children) }} />;
}

/**
 * The first paragraph, with no block structure.
 *
 * For the places a description shares a line or a table cell with something
 * else: an attribute's gloss, a reference's, a row in a listing. A `<p>` there
 * breaks the row it is part of.
 *
 * The first paragraph and not the whole of it, for the reason docs/16 § 9.6
 * gives for the same decision in `render.py`: a description may run to pages
 * and the row is one line. Rendering the whole with `renderInline` is worse
 * than long -- it emits no block elements at all, so a list came out as its
 * source, asterisks and all, run together with the paragraph above it.
 */
export function ProseInline({ className, children }: { className?: string; children?: string }) {
  const gloss = children === undefined ? '' : firstParagraph(children);
  if (gloss === '') return null;
  return <span className={className} dangerouslySetInnerHTML={{ __html: md.renderInline(gloss) }} />;
}

/**
 * Up to the first blank line, or the first line that opens a block.
 *
 * A list may follow a paragraph with no blank line between them, so the blank
 * line alone is not the boundary.
 */
export function firstParagraph(source: string): string {
  const lines: string[] = [];
  for (const line of source.split(/\r?\n/)) {
    if (line.trim() === '' || /^\s*([*+-]\s|\d+[.)]\s|#{1,6}\s|>|```|\|)/.test(line)) break;
    lines.push(line.trim());
  }
  return lines.join(' ').trim();
}

/** The renderer itself, for a check that wants the string. */
export function renderMarkdown(source: string): string {
  return md.render(source);
}
