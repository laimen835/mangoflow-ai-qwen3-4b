/* Shared by the portal and floating chat. Never interpret model text as HTML. */
(function (root) {
  'use strict';
  function safeURL(value) {
    try {
      // Model-generated links must be explicit web addresses, not relative URLs.
      if (!/^https?:\/\//i.test(value)) return null;
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  }
  function trimURL(value) {
    let tail = '';
    while (/[.,!?;:»]$/.test(value) || (value.endsWith(')') && (value.match(/\)/g)||[]).length > (value.match(/\(/g)||[]).length)) {
      tail = value.slice(-1) + tail; value = value.slice(0,-1);
    }
    return [value, tail];
  }
  function tokens(text, sources = []) {
    const result = [];
    const pattern = /\[([^\]\n]+)\]\((https?:\/\/[^\s<>]+)\)|https?:\/\/[^\s<>"\[\]]+|\[(\d+)\]/gi;
    let cursor = 0;
    for (const match of String(text).matchAll(pattern)) {
      if (match.index > cursor) result.push({text:text.slice(cursor,match.index)});
      if (match[1]) {
        const href = safeURL(match[2]);
        result.push(href ? {text:match[1],href} : {text:match[0]});
      } else if (match[3]) {
        const source = sources[Number(match[3])-1];
        const href = safeURL(source?.url || '');
        result.push(href ? {text:match[0],href,title:source.title} : {text:match[0]});
      } else {
        const [url, tail] = trimURL(match[0]);
        const href = safeURL(url);
        result.push(href ? {text:url,href} : {text:url});
        if (tail) result.push({text:tail});
      }
      cursor = match.index + match[0].length;
    }
    if (cursor < text.length) result.push({text:text.slice(cursor)});
    return result;
  }
  function render(container, text, sources = []) {
    container.replaceChildren();
    for (const token of tokens(String(text || ''),sources)) {
      if (!token.href) { container.append(document.createTextNode(token.text)); continue; }
      const a = document.createElement('a');
      a.textContent=token.text; a.href=token.href; a.target='_blank'; a.rel='noopener noreferrer';
      if (token.title) a.title=token.title;
      container.append(a);
    }
  }
  root.AnswerRenderer = {safeURL,tokens,render};
  if (typeof module !== 'undefined') module.exports=root.AnswerRenderer;
})(typeof window !== 'undefined' ? window : globalThis);
