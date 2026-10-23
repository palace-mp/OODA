// The item document: editing in place, margin comments and suggested changes.
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const csrf = () =>
    (document.cookie.match(/(?:^|; )ooda_csrftoken=([^;]+)/) || [])[1] || ($("[name=csrfmiddlewaretoken]") || {}).value;

  const item = $("#item");
  const doc = $("#doc");
  const threads = $("#threads");
  if (!item || !doc || !threads) return;

  const BLOCKS = "p, h2, h3, li, blockquote";
  const marks = {}; // thread id -> the <mark> elements that highlight its passage

  // --- Finding passages in the document ------------------------------------

  // Paragraph-like elements that hold text directly.
  const blocks = () => $$(BLOCKS, doc).filter((b) => !b.querySelector(BLOCKS));

  function blockOf(node) {
    const el = node && (node.nodeType === 1 ? node : node.parentElement);
    const block = el && el.closest(BLOCKS);
    return block && doc.contains(block) ? block : null;
  }

  // Text nodes of an element, leaving out what the page itself shows (suggested text).
  function textNodes(el) {
    const out = [];
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.parentElement.closest("[data-ui]") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    while (walker.nextNode()) out.push(walker.currentNode);
    return out;
  }

  const textOf = (el) => textNodes(el).map((n) => n.nodeValue).join("");

  // Where is `quote`? Prefer the occurrence that follows `prefix`, as when it was written.
  function locate(quote, prefix) {
    let fallback = null;
    for (const block of blocks()) {
      const nodes = textNodes(block);
      const text = nodes.map((n) => n.nodeValue).join("");
      for (let at = text.indexOf(quote); at >= 0; at = text.indexOf(quote, at + 1)) {
        const loc = { nodes, start: at, end: at + quote.length };
        if (!prefix || text.slice(0, at).endsWith(prefix)) return loc;
        fallback = fallback || loc;
      }
    }
    return fallback;
  }

  // Wrap the located passage in elements made by `make`, one per text node it spans.
  function wrap(loc, make) {
    const out = [];
    let pos = 0;
    for (const node of loc.nodes) {
      const len = node.nodeValue.length;
      const start = pos;
      pos += len;
      if (pos <= loc.start || start >= loc.end) continue;
      const range = document.createRange();
      range.setStart(node, Math.max(loc.start - start, 0));
      range.setEnd(node, Math.min(loc.end - start, len));
      if (range.collapsed) continue;
      const el = make();
      range.surroundContents(el);
      out.push(el);
    }
    return out;
  }

  function unwrap(el) {
    const parent = el.parentNode;
    if (!parent) return;
    el.replaceWith(...el.childNodes);
    parent.normalize();
  }

  // The document as it should be stored: without highlights and suggested text.
  function cleanHTML() {
    const clone = doc.cloneNode(true);
    clone.querySelectorAll("[data-ui]").forEach((e) => e.remove());
    clone.querySelectorAll("mark").forEach((m) => m.replaceWith(...m.childNodes));
    return clone.innerHTML;
  }

  // --- Highlighting what threads refer to ----------------------------------

  function makeMark(id, cls) {
    const m = document.createElement("mark");
    m.className = cls;
    m.dataset.comment = id;
    return m;
  }

  $$(".thread", threads).forEach((card) => {
    const id = card.dataset.comment;
    const quote = card.dataset.quote;
    if (!quote) return;
    const loc = locate(quote, card.dataset.prefix);
    if (!("open" in card.dataset)) {
      // Resolved threads keep their place next to the text, without highlighting it.
      if (loc) marks[id] = wrap(loc, () => makeMark(id, "c closed"));
      else if ($(".anchor-quote", card)) $(".anchor-quote", card).hidden = false;
      return;
    }
    if (!loc) {
      // The text has changed since. Show what it was about instead.
      card.classList.add("orphan");
      const q = $(".anchor-quote", card);
      if (q) q.hidden = false;
      const accept = $(".accept-form button", card);
      if (accept) accept.disabled = true;
      return;
    }
    const suggestion = card.dataset.kind === "suggestion";
    const els = wrap(loc, () => makeMark(id, suggestion ? "s" : card.dataset.kind === "reading" ? "c r" : "c"));
    if (suggestion && els.length) {
      const ins = document.createElement("ins");
      ins.className = "s-ins";
      ins.dataset.ui = "";
      ins.dataset.comment = id;
      ins.contentEditable = "false";
      ins.textContent = card.dataset.replacement;
      els[els.length - 1].after(ins);
    }
    marks[id] = els;
  });

  // --- Placing threads next to their passage --------------------------------

  const wide = () => window.matchMedia("(min-width: 1000px)").matches;
  const liveCards = () => $$(":scope > .thread:not([hidden])", threads);

  function layout() {
    const cards = liveCards();
    if (!wide()) {
      cards.forEach((c) => (c.style.top = ""));
      threads.style.minHeight = "";
      return;
    }
    const base = threads.getBoundingClientRect().top;
    const anchored = [];
    const loose = [];
    for (const card of cards) {
      const els = marks[card.dataset.comment];
      if (els && els.length && els[0].isConnected) anchored.push([els[0].getBoundingClientRect().top - base, card]);
      else loose.push(card);
    }
    anchored.sort((a, b) => a[0] - b[0]);
    let y = 0;
    for (const [top, card] of anchored) {
      const at = Math.max(top - 6, y);
      card.style.top = `${at}px`;
      y = at + card.offsetHeight + 12;
    }
    for (const card of loose) {
      card.style.top = `${y}px`;
      y += card.offsetHeight + 12;
    }
    threads.style.minHeight = `${y}px`;
  }

  let frame = 0;
  const layoutSoon = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(layout);
  };
  window.addEventListener("resize", layoutSoon);
  if (document.fonts) document.fonts.ready.then(layoutSoon);
  // Show all threads, the open ones, or the resolved ones.
  const filters = $$("[data-filter]");
  function showThreads(which) {
    filters.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.filter === which)));
    $$(".thread", threads).forEach((card) => {
      const open = "open" in card.dataset;
      const shown = which === "all" || (which === "open") === open;
      if (card.dataset.comment !== "new") card.hidden = !shown;
      $$(`[data-comment="${card.dataset.comment}"]`, doc).forEach((m) => m.classList.toggle("off", !shown));
    });
    try {
      localStorage.setItem("ooda-thread-filter", which);
    } catch (e) {
      /* the choice just isn't remembered */
    }
    layoutSoon();
  }
  filters.forEach((b) => b.addEventListener("click", () => showThreads(b.dataset.filter)));
  let remembered = "open";
  try {
    remembered = localStorage.getItem("ooda-thread-filter") || "open";
  } catch (e) {
    /* no storage */
  }
  const linked = location.hash.match(/^#c(\d+)$/);
  if (linked && $(`#c${linked[1]}`) && !("open" in $(`#c${linked[1]}`).dataset)) remembered = "all";
  if (filters.length) showThreads(remembered);
  layout();

  function activate(id) {
    $$(".active", item).forEach((el) => el.classList.remove("active"));
    if (!id) return;
    $$(`[data-comment="${id}"]`, item).forEach((el) => el.classList.add("active"));
  }

  threads.addEventListener("mouseover", (e) => {
    const card = e.target.closest(".thread");
    if (card) activate(card.dataset.comment);
  });
  doc.addEventListener("click", (e) => {
    const mark = e.target.closest("mark[data-comment], ins[data-comment]");
    if (!mark) return;
    activate(mark.dataset.comment);
    const card = $(`#c${mark.dataset.comment}`);
    if (card && !wide()) card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });

  const target = location.hash.match(/^#c(\d+)$/);
  if (target) {
    activate(target[1]);
    const first = (marks[target[1]] || [])[0];
    if (first) first.scrollIntoView({ block: "center" });
  }

  // Replies open in place.
  threads.addEventListener("click", (e) => {
    const open = e.target.closest("[data-reply]");
    const close = e.target.closest("[data-cancel-reply]");
    if (!open && !close) return;
    const card = e.target.closest(".thread");
    const form = $(".reply-form", card);
    form.hidden = !!close;
    if (open) form.elements.body.focus();
    layoutSoon();
  });

  // --- Starting a comment or a suggestion ------------------------------------

  const pop = $(".selection-pop");
  const para = $(".para-comment");
  const template = $("#composer");
  let composer = null;
  let selected = null;

  // The current selection, if it lies within one paragraph of the document.
  function selection() {
    const sel = window.getSelection();
    if (!sel.rangeCount || sel.isCollapsed) return null;
    const range = sel.getRangeAt(0);
    const block = blockOf(range.startContainer);
    if (!block || block !== blockOf(range.endContainer)) return null;
    const textBefore = (container, offset) => {
      const r = document.createRange();
      r.setStart(block, 0);
      r.setEnd(container, offset);
      const frag = r.cloneContents();
      frag.querySelectorAll("[data-ui]").forEach((el) => el.remove());
      return frag.textContent;
    };
    const start = textBefore(range.startContainer, range.startOffset).length;
    const end = textBefore(range.endContainer, range.endOffset).length;
    const text = textOf(block);
    const quote = text.slice(start, end);
    if (!quote.trim()) return null;
    return { block, start, end, quote, prefix: text.slice(Math.max(0, start - 60), start), rect: range.getBoundingClientRect() };
  }

  function showPop() {
    if (!pop) return;
    selected = selection();
    if (!selected) {
      pop.hidden = true;
      return;
    }
    const page = $(".page").getBoundingClientRect();
    pop.style.top = `${window.scrollY + selected.rect.top - 4}px`;
    pop.style.left = `${window.scrollX + (wide() ? page.right + 8 : Math.max(8, selected.rect.left))}px`;
    if (!wide()) pop.style.top = `${window.scrollY + selected.rect.bottom + 8}px`;
    pop.hidden = false;
    if (para) para.hidden = true;
  }

  function startComposer(kind, sel) {
    cancelComposer();
    marks.new = wrap({ nodes: textNodes(sel.block), start: sel.start, end: sel.end }, () => makeMark("new", "pending"));
    const form = template.content.firstElementChild.cloneNode(true);
    const suggestion = kind === "suggestion";
    form.dataset.comment = "new";
    form.elements.kind.value = kind;
    form.elements.quote.value = sel.quote;
    form.elements.prefix.value = sel.prefix;
    $(".for-suggestion", form).hidden = !suggestion;
    $(".quote-preview", form).textContent = sel.quote;
    form.elements.replacement.value = sel.quote;
    const body = form.elements.body;
    body.placeholder = suggestion ? body.dataset.suggestionPlaceholder
      : kind === "reading" ? body.dataset.readingPlaceholder : body.dataset.commentPlaceholder;
    body.required = !suggestion;
    $("[data-cancel]", form).addEventListener("click", cancelComposer);
    form.addEventListener("keydown", (e) => {
      if (e.key === "Escape") cancelComposer();
    });
    threads.prepend(form);
    composer = form;
    window.getSelection().removeAllRanges();
    if (pop) pop.hidden = true;
    layout();
    const field = suggestion ? form.elements.replacement : body;
    field.focus({ preventScroll: true });
    if (suggestion) field.select();
    form.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function cancelComposer() {
    if (!composer) return;
    composer.remove();
    composer = null;
    (marks.new || []).forEach(unwrap);
    delete marks.new;
    layout();
  }

  if (pop && template) {
    document.addEventListener("mouseup", () => setTimeout(showPop, 0));
    document.addEventListener("keyup", (e) => {
      if (e.shiftKey || e.key.startsWith("Arrow")) showPop();
    });
    pop.addEventListener("mousedown", (e) => e.preventDefault());
    pop.addEventListener("click", (e) => {
      const button = e.target.closest("[data-start]");
      if (button && selected) startComposer(button.dataset.start, selected);
    });

    // A "+" in the margin comments on a whole paragraph.
    let hovered = null;
    doc.addEventListener("mousemove", (e) => {
      const block = blockOf(e.target);
      if (!block || block === hovered || !pop.hidden || !wide()) return;
      hovered = block;
      if (!textOf(block).trim()) {
        para.hidden = true;
        return;
      }
      const page = $(".page").getBoundingClientRect();
      const rect = block.getBoundingClientRect();
      para.style.top = `${window.scrollY + rect.top}px`;
      para.style.left = `${window.scrollX + page.right - 34}px`;
      para.hidden = false;
    });
    const docwrap = $(".docwrap");
    const leave = (e) => {
      const to = e.relatedTarget;
      if (to && (to === para || docwrap.contains(to))) return;
      para.hidden = true;
      hovered = null;
    };
    docwrap.addEventListener("mouseleave", leave);
    para.addEventListener("mouseleave", leave);
    para.addEventListener("click", () => {
      if (!hovered) return;
      const text = textOf(hovered);
      const start = text.length - text.trimStart().length;
      const quote = text.trim();
      startComposer("comment", { block: hovered, start, end: start + quote.length, quote, prefix: text.slice(0, start) });
      para.hidden = true;
    });
  }

  // --- Accepting a suggestion -----------------------------------------------

  let saving = null; // the editor's save in flight, if any

  threads.addEventListener("submit", (e) => {
    const form = e.target;
    if (!form.classList.contains("accept-form")) return;
    const card = form.closest(".thread");
    const id = card.dataset.comment;
    const els = (marks[id] || []).filter((m) => m.isConnected);
    if (!els.length) {
      e.preventDefault();
      return;
    }
    if (saving) {
      // Let the save finish so the version is current, then try again.
      e.preventDefault();
      saving.finally(() => form.requestSubmit());
      return;
    }
    els[0].before(document.createTextNode(card.dataset.replacement));
    els.forEach((m) => m.remove());
    $$(`[data-ui][data-comment="${id}"]`, doc).forEach((x) => x.remove());
    form.elements.body.value = cleanHTML();
    form.elements.version.value = item.dataset.version;
    if (editor) editor.cancel();
  });

  // --- Editing ---------------------------------------------------------------

  const editor = item.dataset.saveUrl ? setUpEditor() : null;

  function setUpEditor() {
    const title = $("[data-title]");
    const state = $(".save-state");
    let timer = null;
    let dirty = false;
    let stopped = false;

    document.execCommand("defaultParagraphSeparator", false, "p");

    const say = (key, error) => {
      state.textContent = state.dataset[key];
      state.classList.toggle("error", !!error);
    };

    function changed() {
      if (stopped) return;
      dirty = true;
      clearTimeout(timer);
      timer = setTimeout(save, 900);
      layoutSoon();
    }

    async function save() {
      if (stopped || !dirty) return;
      if (saving) {
        saving.finally(save);
        return;
      }
      dirty = false;
      say("saving");
      const body = new FormData();
      body.append("title", title.textContent.trim());
      body.append("body", cleanHTML());
      body.append("version", item.dataset.version);
      saving = (async () => {
        try {
          const r = await fetch(item.dataset.saveUrl, { method: "POST", body, headers: { "X-CSRFToken": csrf() } });
          if (r.status === 409) {
            stopped = true;
            doc.contentEditable = "false";
            title.contentEditable = "false";
            say("conflict", true);
            return;
          }
          if (!r.ok) throw new Error(r.status);
          item.dataset.version = (await r.json()).version;
          say(dirty ? "saving" : "saved");
        } catch (err) {
          dirty = true;
          say("failed", true);
        }
      })();
      await saving;
      saving = null;
    }

    // Typing right after a label like "How we measure:" writes the answer, not more label.
    // Browsers otherwise put the caret inside the bold label and keep typing in bold.
    doc.addEventListener("beforeinput", (e) => {
      if (e.inputType !== "insertText" || !e.data) return;
      const sel = window.getSelection();
      if (!sel.isCollapsed || !sel.rangeCount) return;
      const node = sel.anchorNode;
      const li = node && (node.nodeType === 1 ? node : node.parentElement).closest("li");
      const label = li && li.firstChild && li.firstChild.nodeName === "STRONG" ? li.firstChild : null;
      if (!label || !label.textContent.trim().endsWith(":")) return;
      const rest = document.createRange();
      rest.setStart(node, sel.anchorOffset);
      rest.setEnd(label, label.childNodes.length);
      const inLabelAtEnd = label.contains(node) && !rest.toString().length;
      const justAfter = node.nodeType === 3 && node.previousSibling === label && !node.nodeValue.slice(0, sel.anchorOffset).trim();
      if (!inLabelAtEnd && !justAfter) return;
      e.preventDefault();
      let after = label.nextSibling;
      if (!after || after.nodeType !== 3) {
        after = document.createTextNode("");
        label.after(after);
      }
      const existing = after.nodeValue.replace(/^\s+/, "");
      after.nodeValue = " " + e.data + (existing ? existing : "");
      sel.collapse(after, 1 + e.data.length);
      changed();
    });

    doc.addEventListener("input", changed);
    title.addEventListener("input", changed);
    title.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        doc.focus();
      }
    });
    doc.addEventListener("focus", () => {
      if (!doc.innerHTML.trim()) doc.innerHTML = "<p><br></p>";
    });
    // Pasted text arrives as plain text; formatting comes from the toolbar.
    doc.addEventListener("paste", (e) => {
      e.preventDefault();
      document.execCommand("insertText", false, e.clipboardData.getData("text/plain"));
    });
    window.addEventListener("beforeunload", (e) => {
      if (dirty || saving) e.preventDefault();
    });

    const toolbar = $(".toolbar");
    toolbar.addEventListener("mousedown", (e) => {
      if (e.target.closest("button")) e.preventDefault(); // keep the selection in the document
    });
    toolbar.addEventListener("click", (e) => {
      const button = e.target.closest("button");
      if (!button || !doc.contains(window.getSelection().anchorNode)) return;
      if (button.dataset.cmd) {
        document.execCommand(button.dataset.cmd);
      } else if (button.dataset.block) {
        const current = blockOf(window.getSelection().anchorNode);
        const tag = button.dataset.block;
        const same = current && current.tagName.toLowerCase() === tag;
        document.execCommand("formatBlock", false, same ? "<p>" : `<${tag}>`);
      } else if ("journal" in button.dataset) {
        // A dated line for the journal: "23 Oct, 14:20 · Name — "
        const now = new Date();
        const when = `${now.toLocaleDateString(undefined, { day: "numeric", month: "short" })}, ${now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
        const stamp = document.createElement("strong");
        stamp.textContent = `${when} · ${button.dataset.who}`;
        document.execCommand("insertHTML", false, `${stamp.outerHTML} —&nbsp;`);
      } else if ("link" in button.dataset) {
        const url = window.prompt(button.dataset.prompt, "https://");
        if (url && url !== "https://") document.execCommand("createLink", false, url);
      }
      changed();
    });

    return {
      cancel() {
        clearTimeout(timer);
        dirty = false;
      },
    };
  }
})();
