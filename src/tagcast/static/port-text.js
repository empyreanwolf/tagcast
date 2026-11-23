"use strict";

// fork v115: the features ported from upstream Tagcast 1.9.x carry their English strings in small tables.
// portFormat fills {{name}} and {{name, number}} and picks key_one / key_other for a count.
function portFormat(table, key, vars = {}, html = false) {
  const plural = typeof vars.count === "number" && `${key}_one` in table ? (vars.count === 1 ? `${key}_one` : `${key}_other`) : key;
  const template = table[plural] ?? table[key] ?? key;
  const filled = template.replace(/\{\{\s*(\w+)\s*(?:,\s*number\s*)?\}\}/g, (_, name) => {
    const value = vars[name];
    const text = typeof value === "number" ? value.toLocaleString("en") : String(value ?? "");
    return html ? escapeHtml(text) : text;
  });
  return filled;
}
