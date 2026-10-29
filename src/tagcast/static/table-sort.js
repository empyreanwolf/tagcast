// Fork v54: every table gets clickable headers. Click once for A→Z / low→high, again for the reverse,
// a third time for the original order. Works on the rows shown (tables that load page by page sort that page).
(() => {
  const remembered = new Map();   // table signature (its header texts) → {col, dir}
  const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: "base"});
  const sortableTable = table => {
    if (table.dataset.noSort !== undefined || !table.tHead || !table.tBodies.length) return false;
    const heads = [...table.tHead.rows[0]?.cells || []];
    if (!heads.length || table.tBodies.length > 1 && !table.dataset.sortAll) return false;
    const rows = [...table.tBodies[0].rows];
    if (rows.length < 2) return false;
    // Grouped tables (disc headings, spanning cells) cannot be sorted without breaking their meaning.
    return rows.every(row => row.cells.length === heads.length && ![...row.cells].some(c => c.colSpan > 1));
  };
  const keyOf = cell => {
    const raw = (cell.dataset?.sort ?? cell.textContent ?? "").trim();
    if (!raw || raw === "—" || raw === "-") return {empty: true};
    const time = raw.match(/^(\d+):(\d{2})(?::(\d{2}))?$/);
    if (time) return {num: time[3] ? +time[1] * 3600 + +time[2] * 60 + +time[3] : +time[1] * 60 + +time[2]};
    if (/^-?\d[\d,]*(\.\d+)?$/.test(raw)) return {num: parseFloat(raw.replace(/,/g, ""))};
    return {text: raw};
  };
  const compare = (a, b) => {
    if (a.empty || b.empty) return a.empty === b.empty ? 0 : a.empty ? 1 : -1;
    if (a.num !== undefined && b.num !== undefined) return a.num - b.num;
    return collator.compare(String(a.num ?? a.text), String(b.num ?? b.text));
  };
  const signature = table => [...table.tHead.rows[0].cells].map(c => c.dataset.sortLabel ?? c.textContent.trim()).join("|");
  const mark = (table, col, dir) => {
    [...table.tHead.rows[0].cells].forEach((th, i) => {
      th.setAttribute("aria-sort", i === col && dir ? (dir === 1 ? "ascending" : "descending") : "none");
      const arrow = th.querySelector(".sort-arrow");
      if (arrow) arrow.textContent = i === col && dir ? (dir === 1 ? " ▲" : " ▼") : "";
    });
  };
  const apply = (table, col, dir) => {
    const body = table.tBodies[0], rows = [...body.rows];
    rows.forEach((row, i) => { if (row.dataset.origIndex === undefined) row.dataset.origIndex = String(i); });
    const keyed = rows.map(row => ({row, key: dir ? keyOf(row.cells[col]) : null, index: +row.dataset.origIndex}));
    keyed.sort((a, b) => dir ? dir * compare(a.key, b.key) || a.index - b.index : a.index - b.index);
    keyed.forEach(({row}) => body.appendChild(row));
    mark(table, col, dir);
  };
  const prepare = table => {
    if (table.dataset.sortReady || !sortableTable(table)) return;
    table.dataset.sortReady = "1";
    [...table.tHead.rows[0].cells].forEach((th, col) => {
      if (!th.textContent.trim() || th.querySelector("input, button")) return;   // checkbox / action columns
      th.dataset.sortCol = String(col); th.tabIndex = 0; th.classList.add("sortable-th");
      th.dataset.sortLabel = th.textContent.trim();
      th.insertAdjacentHTML("beforeend", '<span class="sort-arrow" aria-hidden="true"></span>');
      th.setAttribute("aria-sort", "none");
    });
    const was = remembered.get(signature(table));
    if (was) apply(table, was.col, was.dir);
  };
  const toggle = th => {
    const table = th.closest("table");
    if (!table || th.dataset.sortCol === undefined) return;
    const col = +th.dataset.sortCol, sig = signature(table), was = remembered.get(sig);
    const dir = !was || was.col !== col ? 1 : was.dir === 1 ? -1 : 0;
    if (dir) remembered.set(sig, {col, dir}); else remembered.delete(sig);
    apply(table, col, dir);
  };
  document.addEventListener("click", event => {
    const th = event.target.closest?.("th.sortable-th");
    if (th) toggle(th);
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const th = event.target.closest?.("th.sortable-th");
    if (th) { event.preventDefault(); toggle(th); }
  });
  const scan = () => document.querySelectorAll("table").forEach(prepare);
  let queued = 0;
  if (typeof MutationObserver === "function") {
    new MutationObserver(() => {
      if (queued) return;
      queued = setTimeout(() => { queued = 0; scan(); }, 120);   // one pass after a render, not one per change
    }).observe(document.body, {childList: true, subtree: true});
  }
  scan();
  globalThis.tagcastTableSort = {scan, toggle, prepare};
})();
