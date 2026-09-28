const darkTheme = document.body.classList.contains('theme-dark')
  || (document.body.classList.contains('theme-auto') && window.matchMedia('(prefers-color-scheme: dark)').matches);

function relativeTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return '刚刚';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)} 天前`;
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function refreshTimes() {
  document.querySelectorAll('.blog-time[data-time]').forEach((node) => {
    node.textContent = relativeTime(node.dataset.time);
  });
}
refreshTimes();
window.setInterval(refreshTimes, 60_000);

for (const card of document.querySelectorAll('.blog-card[data-href]')) {
  const visit = () => { window.location.assign(card.dataset.href); };
  card.addEventListener('click', (event) => {
    if (!event.target.closest('a, button, input, select, textarea')) visit();
  });
  card.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      visit();
    }
  });
}

async function writeToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Clipboard access can be denied outside a secure context. Fall back to
      // the selection-based API so self-hosted HTTP blogs can still copy code.
    }
  }

  const textarea = document.createElement('textarea');
  const previouslyFocused = document.activeElement;
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  textarea.style.pointerEvents = 'none';
  document.body.append(textarea);
  let copied = false;
  try {
    textarea.select();
    copied = document.execCommand('copy');
  } finally {
    textarea.remove();
    previouslyFocused?.focus?.({ preventScroll: true });
  }
  if (!copied) throw new Error('Copy command was rejected');
}

for (const pre of document.querySelectorAll('#write pre')) {
  const code = pre.querySelector('code');
  if (code?.classList.contains('mermaid')) continue;

  const source = code?.textContent ?? pre.textContent ?? '';
  const container = document.createElement('div');
  container.className = 'blog-code-block';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'blog-copy-button';
  button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="11" height="11" rx="2"></rect><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"></path></svg>';
  button.setAttribute('aria-label', '复制代码');
  button.setAttribute('title', '复制代码');
  button.setAttribute('aria-live', 'polite');
  pre.before(container);
  container.append(pre, button);

  let resetTimer;
  button.addEventListener('click', async () => {
    window.clearTimeout(resetTimer);
    button.disabled = true;
    try {
      await writeToClipboard(source);
      button.dataset.state = 'success';
      button.setAttribute('aria-label', '代码已复制');
      button.setAttribute('title', '已复制');
    } catch {
      button.dataset.state = 'error';
      button.setAttribute('aria-label', '代码复制失败');
      button.setAttribute('title', '复制失败');
    } finally {
      button.disabled = false;
      resetTimer = window.setTimeout(() => {
        delete button.dataset.state;
        button.setAttribute('aria-label', '复制代码');
        button.setAttribute('title', '复制代码');
      }, 2000);
    }
  });
}

const articleLayout = document.querySelector('.blog-article-layout');
const outline = articleLayout?.querySelector('.blog-outline');
const outlineList = articleLayout?.querySelector('.blog-outline-list');
const outlineToggle = articleLayout?.querySelector('.blog-outline-toggle');
const outlineClose = articleLayout?.querySelector('.blog-outline-close');
const article = articleLayout?.querySelector('.markdown-body');

if (articleLayout && outline && outlineList && outlineToggle && article) {
  const headings = [...article.querySelectorAll('h1, h2, h3, h4')];
  const usedIds = new Set();
  headings.forEach((heading, index) => {
    if (!heading.id) {
      const stem = (heading.textContent || `section-${index + 1}`).trim().toLowerCase()
        .replace(/[^\p{L}\p{N}\s-]/gu, '')
        .replace(/\s+/g, '-') || `section-${index + 1}`;
      let id = stem;
      let duplicate = 2;
      while (usedIds.has(id) || document.getElementById(id)) id = `${stem}-${duplicate++}`;
      heading.id = id;
    }
    usedIds.add(heading.id);
    const link = document.createElement('a');
    link.href = `#${encodeURIComponent(heading.id)}`;
    link.textContent = heading.textContent || heading.id;
    link.style.setProperty('--outline-level', String(Math.max(0, Number(heading.tagName.slice(1)) - 1)));
    link.addEventListener('click', (event) => {
      event.preventDefault();
      heading.scrollIntoView({ behavior: 'smooth', block: 'start' });
      history.replaceState(null, '', `#${encodeURIComponent(heading.id)}`);
    });
    outlineList.append(link);
  });
  if (!headings.length) {
    articleLayout.classList.add('outline-unavailable');
  } else {
    const setOutline = (visible) => {
      articleLayout.classList.toggle('outline-hidden', !visible);
      outlineToggle.setAttribute('aria-expanded', String(visible));
    };
    outlineToggle.addEventListener('click', () => setOutline(articleLayout.classList.contains('outline-hidden')));
    outlineClose?.addEventListener('click', () => setOutline(false));
    if (window.matchMedia('(max-width: 920px)').matches) setOutline(false);
    const links = [...outlineList.querySelectorAll('a')];
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
      if (!visible) return;
      links.forEach((link) => link.classList.toggle('is-active', decodeURIComponent(link.hash.slice(1)) === visible.target.id));
    }, { rootMargin: '-18% 0px -70% 0px' });
    headings.forEach((heading) => observer.observe(heading));
  }
}

const mermaidNodes = [...document.querySelectorAll('pre > code.mermaid')];
if (mermaidNodes.length) {
  import('./vendor/mermaid.esm.min.mjs').then(async ({ default: mermaid }) => {
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: darkTheme ? 'dark' : 'default' });
    for (const node of mermaidNodes) {
      const source = node.textContent || '';
      try {
        const id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
        const { svg } = await mermaid.render(`mermaid-${id}`, source);
        node.parentElement.innerHTML = svg;
      } catch {
        node.parentElement.classList.add('mermaid-error');
      }
    }
  });
}
