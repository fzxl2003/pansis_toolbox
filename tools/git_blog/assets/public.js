import mermaid from './vendor/mermaid.esm.min.mjs';
mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: document.body.classList.contains('theme-dark') ? 'dark' : 'default' });
for (const node of document.querySelectorAll('pre > code.mermaid')) {
  const source = node.textContent || '';
  try {
    const { svg } = await mermaid.render(`mermaid-${crypto.randomUUID()}`, source);
    node.parentElement.innerHTML = svg;
  } catch { node.parentElement.classList.add('mermaid-error'); }
}
