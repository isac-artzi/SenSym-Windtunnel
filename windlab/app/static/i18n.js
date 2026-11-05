// Message catalog and number formatting for the page (PRD ACC-3, first step).
//
// t(key, params) returns the English text of ./i18n/en.json with {name} placeholders filled in;
// nf(x, digits) formats a number with Intl.NumberFormat for the catalog's locale. The catalog is
// loaded once at start-up (same origin, next to this module, so the installed app and the static
// demo both find it). A missing key shows as ⟦key⟧ so it is noticed, never a blank or a crash.
//
// Scope today: the messages app.js builds for Steps 1-4 (shape, conditions, run, results) and the
// tour. The fixed text of index.html is still English in the markup; see docs/I18N.md.

let locale = "en";
let messages = {};

export async function loadMessages(url = new URL("./i18n/en.json", import.meta.url)) {
  try {
    const r = await fetch(url);
    if (!r.ok) throw new Error(String(r.status));
    const data = await r.json();
    setMessages(data.messages || {}, data.locale || "en");
    return true;
  } catch {
    return false;  // t() then shows ⟦key⟧: visible, and the page still works
  }
}

export function setMessages(table, loc = "en") {
  messages = { ...table };
  locale = loc;
}

export function currentLocale() { return locale; }

export function hasMessage(key) { return Object.prototype.hasOwnProperty.call(messages, key); }

// "{name}" placeholders; a parameter that is missing stays visible as {name}.
export function t(key, params = {}) {
  const s = messages[key];
  if (s === undefined) return `⟦${key}⟧`;
  return s.replace(/\{(\w+)\}/g, (m, k) => (k in params && params[k] !== undefined ? String(params[k]) : m));
}

// Fixed decimals, no digit grouping (what toFixed gave), in the catalog's locale.
export function nf(x, digits = 3) {
  if (x == null || !isFinite(x)) return "–";
  return new Intl.NumberFormat(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits,
    useGrouping: false }).format(x);
}

// Whole numbers with digit grouping (iteration counts, Reynolds numbers).
export function nfInt(x) {
  if (x == null || !isFinite(x)) return "–";
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(x);
}
