(function (global) {
  "use strict";

  const MAX_MESSAGE_CHARS = 512 * 1024;
  const MAX_LINES = 12000;
  const MAX_TABLE_COLUMNS = 32;
  const MAX_LINK_CHARS = 4096;
  const SAFE_PROTOCOLS = new Set(["http:", "https:"]);

  function text(value, limit = MAX_MESSAGE_CHARS) {
    return String(value == null ? "" : value).slice(0, limit);
  }

  function element(document, tag, value = null, className = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value != null) node.textContent = text(value);
    return node;
  }

  function safeUrl(value, baseHref = "https://invalid.local/") {
    const raw = text(value, MAX_LINK_CHARS).trim();
    if (!raw || !/^https?:\/\//iu.test(raw) || /[\u0000-\u001f\u007f]/.test(raw)) return null;
    try {
      const parsed = new URL(raw, baseHref);
      if (!SAFE_PROTOCOLS.has(parsed.protocol) || !parsed.hostname || parsed.username || parsed.password) return null;
      return parsed.href;
    } catch {
      return null;
    }
  }

  async function copyVisibleText(value, options = {}) {
    if (typeof options.copyText === "function") return options.copyText(value);
    if (global.navigator?.clipboard?.writeText) return global.navigator.clipboard.writeText(value);
    throw new Error("clipboard_unavailable");
  }

  function bindExternalLink(link, url, label, options = {}) {
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.dataset.external = "true";
    link.setAttribute("aria-label", `${text(label, 512)}（在外部浏览器打开，需确认）`);
    link.addEventListener("click", async event => {
      event?.preventDefault?.();
      const request = Object.freeze({url, label:text(label, 512)});
      if (typeof options.confirmExternal !== "function"
          || typeof options.openExternal !== "function") {
        link.dataset.openState = "unavailable";
        return;
      }
      try {
        const approved = await options.confirmExternal(request);
        if (approved !== true) {
          link.dataset.openState = "denied";
          return;
        }
        await options.openExternal(request);
        link.dataset.openState = "opened";
      } catch {
        link.dataset.openState = "failed";
      }
    });
  }

  function appendInline(document, host, source, baseHref, options = {}) {
    const value = text(source);
    let cursor = 0;
    // This intentionally supports a small, auditable Markdown subset. Raw
    // HTML and images remain ordinary text; no generated node uses innerHTML.
    const token = /(`+)([^`\r\n]+?)\1|\[([^\]\r\n]{1,512})\]\(([^)\r\n]{1,4096})\)|\*\*([^*\r\n]{1,4096})\*\*/g;
    for (let match; (match = token.exec(value)); ) {
      if (match.index > cursor) host.append(document.createTextNode(value.slice(cursor, match.index)));
      if (match[1]) host.append(element(document, "code", match[2], "ac-inline-code"));
      else if (match[3]) {
        const href = safeUrl(match[4], baseHref);
        if (href) {
          const link = element(document, "a", match[3]);
          bindExternalLink(link, href, match[3], options);
          host.append(link);
        } else host.append(document.createTextNode(match[0]));
      } else {
        const strong = element(document, "strong", match[5]);
        host.append(strong);
      }
      cursor = token.lastIndex;
    }
    if (cursor < value.length) host.append(document.createTextNode(value.slice(cursor)));
  }

  function splitTableRow(line) {
    let value = text(line, 32768).trim();
    if (value.startsWith("|")) value = value.slice(1);
    if (value.endsWith("|")) value = value.slice(0, -1);
    const cells = [];
    let current = "";
    let escaped = false;
    for (const char of value) {
      if (escaped) { current += char; escaped = false; continue; }
      if (char === "\\") { escaped = true; continue; }
      if (char === "|" && cells.length < MAX_TABLE_COLUMNS - 1) {
        cells.push(current.trim()); current = "";
      } else current += char;
    }
    cells.push(current.trim());
    return cells.slice(0, MAX_TABLE_COLUMNS);
  }

  function isTableDivider(line, columns) {
    const cells = splitTableRow(line);
    return cells.length === columns && cells.every(cell => /^:?-{3,}:?$/.test(cell));
  }

  function renderTable(document, lines, start, baseHref, options = {}) {
    const headers = splitTableRow(lines[start]);
    if (headers.length < 2 || start + 1 >= lines.length || !isTableDivider(lines[start + 1], headers.length)) return null;
    const table = element(document, "table", null, "ac-markdown-table");
    const head = element(document, "thead"), headRow = element(document, "tr");
    for (const value of headers) { const cell = element(document, "th"); appendInline(document, cell, value, baseHref, options); headRow.append(cell); }
    head.append(headRow); table.append(head);
    const body = element(document, "tbody");
    let index = start + 2;
    while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
      const values = splitTableRow(lines[index]);
      if (values.length !== headers.length) break;
      const row = element(document, "tr");
      for (const value of values) { const cell = element(document, "td"); appendInline(document, cell, value, baseHref, options); row.append(cell); }
      body.append(row); index += 1;
    }
    table.append(body);
    return {node: table, next: index};
  }

  function render(document, source, options = {}) {
    if (!document || typeof document.createElement !== "function" || typeof document.createTextNode !== "function") throw new TypeError("agent_message_document_invalid");
    const value = text(source), baseHref = text(options.baseHref || global.location?.href || "https://invalid.local/", MAX_LINK_CHARS);
    const root = element(document, "div", null, "ac-markdown");
    const lines = value.replace(/\r\n?/g, "\n").split("\n").slice(0, MAX_LINES);
    let paragraph = [];
    const flushParagraph = () => {
      if (!paragraph.length) return;
      const node = element(document, "p");
      paragraph.forEach((line, index) => { if (index) node.append(document.createElement("br")); appendInline(document, node, line, baseHref, options); });
      root.append(node); paragraph = [];
    };
    for (let index = 0; index < lines.length;) {
      const line = lines[index];
      const fence = /^\s*```([^`\s]{0,64})\s*$/.exec(line);
      if (fence) {
        flushParagraph(); const body = []; index += 1;
        while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) { body.push(lines[index]); index += 1; }
        if (index < lines.length) index += 1;
        const wrapper = element(document, "div", null, "ac-code-block-wrap");
        const toolbar = element(document, "div", null, "ac-code-block-toolbar");
        const copy = element(document, "button", "复制代码", "ac-code-copy");
        copy.type = "button";
        copy.setAttribute("aria-label", "复制此代码块");
        const codeText = body.join("\n");
        copy.addEventListener("click", async () => {
          try {
            await copyVisibleText(codeText, {
              ...options,
              copyText:options.copyCode || options.copyText,
            });
            copy.textContent = "已复制";
          } catch {
            copy.textContent = "复制失败";
          }
        });
        toolbar.append(copy);
        const pre = element(document, "pre", null, "ac-code-block"), code = element(document, "code", codeText);
        if (fence[1]) code.dataset.language = fence[1].toLowerCase();
        pre.append(code); wrapper.append(toolbar, pre); root.append(wrapper); continue;
      }
      const table = renderTable(document, lines, index, baseHref, options);
      if (table) { flushParagraph(); root.append(table.node); index = table.next; continue; }
      const heading = /^(#{1,4})\s+(.+)$/.exec(line);
      if (heading) { flushParagraph(); const node = element(document, `h${heading[1].length + 2}`); appendInline(document, node, heading[2], baseHref, options); root.append(node); index += 1; continue; }
      const item = /^\s*[-*]\s+(.+)$/.exec(line);
      if (item) {
        flushParagraph(); const list = element(document, "ul");
        while (index < lines.length) {
          const next = /^\s*[-*]\s+(.+)$/.exec(lines[index]); if (!next) break;
          const row = element(document, "li"); appendInline(document, row, next[1], baseHref, options); list.append(row); index += 1;
        }
        root.append(list); continue;
      }
      if (!line.trim()) flushParagraph(); else paragraph.push(line);
      index += 1;
    }
    flushParagraph();
    if (lines.length >= MAX_LINES || value.length >= MAX_MESSAGE_CHARS) root.append(element(document, "p", "内容过长，当前仅显示受限预览。", "ac-note ac-warning"));
    return root;
  }

  function createMessage(document, options = {}) {
    const reasoning = options.reasoning === true;
    const article = element(document, "article", null, `ac-message ac-${options.role === "user" ? "user" : "assistant"}${reasoning ? " ac-reasoning" : ""}`);
    const header = element(document, "header", null, "ac-message-header");
    if (!reasoning) header.append(element(document, "strong", options.role === "user" ? "你" : "Agent"));
    const copy = element(document, "button", "复制", "ac-message-copy");
    copy.type = "button"; copy.setAttribute("aria-label", reasoning ? "复制此推理过程" : "复制此消息"); header.append(copy);
    const body = element(document, "div", null, "ac-message-text"); body.setAttribute("aria-live", "off");
    let disclosure = null, disclosureState = null;
    if (reasoning) {
      disclosure = element(document, "details", null, "ac-reasoning-disclosure");
      const summary = element(document, "summary", null, "ac-reasoning-summary");
      summary.append(element(document, "strong", "推理过程"));
      disclosureState = element(document, "span", "已记录", "ac-reasoning-state");
      summary.append(disclosureState);
      const content = element(document, "div", null, "ac-reasoning-content");
      content.append(header, body); disclosure.append(summary, content); article.append(disclosure);
    } else article.append(header, body);
    let current = "", copyGeneration = 0, streaming = false, autoOpened = false;
    const setText = (value, update = {}) => {
      const wasStreaming = streaming;
      current = text(value); streaming = update.streaming === true;
      // Growing provider output stays inert plain text. Parsing the complete
      // Markdown source after every delta causes avoidable work and visual
      // churn; the terminal projection promotes the same source once.
      body.replaceChildren(streaming ? element(document, "pre", current, "ac-streaming-text") : render(document, current, options));
      article.dataset.contentLength = String(current.length); article.dataset.streaming = String(streaming);
      article.setAttribute("aria-busy", String(streaming));
      if (reasoning && disclosure && disclosureState) {
        disclosureState.textContent = streaming ? `正在推理 · ${current.length} 字符` : `${current.length} 字符`;
        if (streaming) { disclosure.open = true; autoOpened = true; }
        else if (wasStreaming && autoOpened) { disclosure.open = false; autoOpened = false; }
      }
    };
    copy.addEventListener("click", async () => {
      const generation = ++copyGeneration;
      try {
        await copyVisibleText(current, options);
        if (generation === copyGeneration) copy.textContent = "已复制";
      } catch {
        if (generation === copyGeneration) copy.textContent = "复制失败";
      }
    });
    setText(options.text || "", {streaming:options.streaming === true});
    return Object.freeze({article, body, copy, disclosure, setText, getText:() => current});
  }

  global.AgentMessageRenderer = Object.freeze({
    render,
    createMessage,
    safeUrl,
    copyVisibleText,
    MAX_MESSAGE_CHARS,
    MAX_LINES,
  });
})(window);
