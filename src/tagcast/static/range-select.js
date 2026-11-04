"use strict";

// Fork v73: select many albums quickly. Shift-click a checkbox to select everything from the last one you clicked to
// this one; or press on a checkbox and drag down (or up) over the others to tick or untick them all.
const rangeSelect = {anchor: "", drag: null};

function rangeSelectIds() { return [...document.querySelectorAll("[data-select]")].map(box => box.dataset.select); }

function rangeSelectApply(keys, on) {
  for (const key of keys) for (const id of shelfIds(key)) { if (on) state.selected.add(id); else state.selected.delete(id); }
}

document.addEventListener("click", event => {
  const box = event.target.closest?.("[data-select]");
  if (!box) return;
  const key = box.dataset.select, keys = rangeSelectIds();
  if (event.shiftKey && rangeSelect.anchor && keys.includes(rangeSelect.anchor) && keys.includes(key)) {
    const [a, b] = [keys.indexOf(rangeSelect.anchor), keys.indexOf(key)].sort((x, y) => x - y);
    rangeSelectApply(keys.slice(a, b + 1), box.checked);
    render();
  }
  rangeSelect.anchor = key;
});

document.addEventListener("mousedown", event => {
  const box = event.target.closest?.("[data-select]");
  if (!box || event.button !== 0 || event.shiftKey) return;
  rangeSelect.drag = {from: box.dataset.select, on: !box.checked, moved: false, seen: new Set([box.dataset.select])};
});

document.addEventListener("mouseover", event => {
  const drag = rangeSelect.drag;
  if (!drag) return;
  const row = event.target.closest?.("tr, .album-card, li, article");
  const box = row?.querySelector?.("[data-select]");
  if (!box || drag.seen.has(box.dataset.select)) return;
  drag.moved = true;
  drag.seen.add(box.dataset.select);
  box.checked = drag.on;
  rangeSelectApply([box.dataset.select], drag.on);
});

document.addEventListener("mouseup", () => {
  const drag = rangeSelect.drag;
  rangeSelect.drag = null;
  if (drag?.moved) {
    rangeSelectApply([drag.from], drag.on);
    rangeSelect.anchor = drag.from;
    render();
  }
});
