import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeRaw from 'rehype-raw';
import rehypeKatex from 'rehype-katex';
import rehypeHighlight from 'rehype-highlight';
import rehypeSlug from 'rehype-slug';
import path from 'node:path';

function resolveHref(href, sourcePath, sourceMap, blogSlug, image = false) {
  if (!href || /^(https?:|mailto:|tel:|#|data:)/i.test(href)) return href;
  const [file, anchor = ''] = href.split('#', 2);
  const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(sourcePath), file));
  if (sourceMap[resolved]) return `/blog/${encodeURIComponent(blogSlug)}/posts/${resolved.split('/').map(encodeURIComponent).join('/')}${anchor ? `#${anchor}` : ''}`.replace(`/posts/${resolved.split('/').map(encodeURIComponent).join('/')}`, `/posts/${sourceMap[resolved].split('/').map(encodeURIComponent).join('/')}`);
  if (image || !/\.(md|markdown)$/i.test(file)) return `/blog/${encodeURIComponent(blogSlug)}/assets/${resolved.split('/').map(encodeURIComponent).join('/')}${anchor ? `#${anchor}` : ''}`;
  return href;
}

function app(input) {
  const components = {
    a: ({node, href, children, ...props}) => React.createElement('a', {...props, href: resolveHref(href, input.sourcePath, input.sourceMap, input.blogSlug)}, children),
    img: ({node, src, ...props}) => React.createElement('img', {...props, src: resolveHref(src, input.sourcePath, input.sourceMap, input.blogSlug, true)}),
    code: ({node, className, children, ...props}) => {
      if (String(className || '').includes('language-mermaid')) return React.createElement('code', {className: 'mermaid'}, String(children).replace(/\n$/, ''));
      return React.createElement('code', {className, ...props}, children);
    },
  };
  return React.createElement(Markdown, {remarkPlugins:[remarkGfm,remarkMath], rehypePlugins:[rehypeRaw,rehypeKatex,rehypeHighlight,rehypeSlug], components}, input.markdown);
}
try {
  const input = JSON.parse(await new Promise((resolve, reject) => { let text=''; process.stdin.setEncoding('utf8'); process.stdin.on('data', x => text += x); process.stdin.on('end', () => resolve(text)); process.stdin.on('error', reject); }));
  process.stdout.write(JSON.stringify({html: renderToStaticMarkup(app(input))}));
} catch (error) { console.error(error?.stack || String(error)); process.exit(1); }
