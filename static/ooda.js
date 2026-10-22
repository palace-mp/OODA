// Small enhancements. Everything works without JavaScript, only less smoothly.
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const csrf = () => (document.cookie.match(/(?:^|; )ooda_csrftoken=([^;]+)/) || [])[1] || ($("[name=csrfmiddlewaretoken]") || {}).value;

  // Filters apply as soon as they change; search waits for a pause in typing.
  $$("form[data-autosubmit]").forEach((form) => {
    let timer;
    form.addEventListener("change", () => form.requestSubmit());
    const search = $("input[type=search]", form);
    if (search) {
      search.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(() => form.requestSubmit(), 450);
      });
    }
    // Drop empty fields so the address stays readable.
    form.addEventListener("formdata", (e) => {
      for (const [key, value] of Array.from(e.formData.entries())) {
        if (value === "" || (key === "state" && value === "open")) e.formData.delete(key);
      }
      const values = e.formData.getAll("opt").filter(Boolean);
      e.formData.delete("opt");
      values.forEach((v) => e.formData.append("opt", v));
    });
  });
  $$("[data-autosubmit-one]").forEach((el) => el.addEventListener("change", () => el.form.requestSubmit()));

  // Ask before destructive actions.
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-confirm]");
    if (el && el.tagName === "BUTTON" && !confirm(el.dataset.confirm)) e.preventDefault();
  });
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (form.dataset.confirm && !confirm(form.dataset.confirm)) e.preventDefault();
  });

  // Click a link field to copy it.
  $$("input[data-copy]").forEach((input) => {
    input.addEventListener("click", () => {
      input.select();
      if (navigator.clipboard) navigator.clipboard.writeText(input.value).catch(() => {});
    });
  });

  // Board: drag cards between columns.
  $$("[data-dnd]").forEach((board) => {
    let dragged = null;
    board.addEventListener("dragstart", (e) => {
      dragged = e.target.closest(".card");
      if (!dragged) return;
      dragged.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
    });
    board.addEventListener("dragend", () => {
      if (dragged) dragged.classList.remove("dragging");
      $$(".column.over", board).forEach((c) => c.classList.remove("over"));
    });
    $$(".column", board).forEach((column) => {
      column.addEventListener("dragover", (e) => {
        if (!dragged) return;
        e.preventDefault();
        column.classList.add("over");
      });
      column.addEventListener("dragleave", (e) => {
        if (!column.contains(e.relatedTarget)) column.classList.remove("over");
      });
      column.addEventListener("drop", (e) => {
        e.preventDefault();
        column.classList.remove("over");
        if (!dragged || dragged.closest(".column") === column) return;
        $(".cards", column).prepend(dragged);
        const body = new FormData();
        body.append("group", board.dataset.group);
        body.append("value", column.dataset.value);
        fetch(dragged.dataset.move, { method: "POST", body, headers: { "X-CSRFToken": csrf() } })
          .then((r) => { if (!r.ok) location.reload(); else recount(board); })
          .catch(() => location.reload());
      });
    });
  });

  function recount(board) {
    $$(".column", board).forEach((c) => {
      const n = $("h2 .meta", c);
      if (n) n.textContent = $$(".card", c).length;
    });
  }
})();
