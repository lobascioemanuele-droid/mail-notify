"use strict";
/*
 * ccmail web app: interface language.
 *
 * The page is written in Italian. A language catalog (lang-<code>.js) maps the Italian texts to another language;
 * this engine applies it to every text node and to the title / aria-label / placeholder / alt attributes, and keeps
 * doing so for what the app draws later (MutationObserver). No network: catalogs are static files, nothing leaves
 * the machine, and data that comes from the daemon (project names, chat titles, mail subjects) is never translated
 * because it matches no catalog entry.
 *
 * A catalog is registered as  ccLangs[code] = { exact: { "testo": "text" }, patterns: [[ "^regex$", "text $1" ]] }.
 */
(function () {
  const catalogs = (window.ccLangs = window.ccLangs || {});
  const ATTRS = ["title", "aria-label", "placeholder", "alt"];
  const KEY = "ccmail.lang";
  let current = "it";
  let compiled = null;
  const original = new WeakMap(); // node or element -> { text?: original text, attrs?: {name: original} }
  const written = new WeakMap(); // what the engine itself wrote last (to ignore our own mutations)

  function compile(code) {
    const c = catalogs[code];
    if (!c) return null;
    return {
      exact: c.exact || {},
      patterns: (c.patterns || []).map(([re, to]) => [new RegExp(re, "s"), to]),
    };
  }

  function tr(text) {
    if (!compiled || !text) return text;
    const m = /^(\s*)([\s\S]*?)(\s*)$/.exec(text);
    const core = m[2].replace(/\s+/g, " "); // a sentence wrapped over source lines is one text
    if (!core) return text;
    let out = compiled.exact[core];
    if (out === undefined) {
      for (const [re, to] of compiled.patterns) {
        const m2 = re.exec(core);
        if (m2) {
          // captured parts are translated too when they are catalog entries ("Sender x · <store name>")
          out = to.replace(/\$(\d)/g, (_, n) => { const g = m2[Number(n)]; if (g === undefined) return "";
            const whole = tr(g);
            if (whole !== g || g.indexOf(", ") < 0) return whole;
            return g.split(", ").map((x) => tr(x)).join(", "); // "type A, type B"
          });
          break;
        }
      }
    }
    return out === undefined ? text : m[1] + out + m[3];
  }

  function textNode(n) {
    const rec = original.get(n);
    const cur = n.data;
    // the app rewrote the node (value differs from what we wrote): take it as the new original
    if (!rec || written.get(n) !== cur) original.set(n, { text: cur });
    const src = original.get(n).text;
    const out = current === "it" ? src : tr(src);
    if (out !== cur) { written.set(n, out); n.data = out; } else written.set(n, cur);
  }

  function element(el) {
    for (const a of ATTRS) {
      if (!el.hasAttribute(a)) continue;
      let rec = original.get(el);
      if (!rec) { rec = { attrs: {} }; original.set(el, rec); }
      const cur = el.getAttribute(a);
      const w = rec.written || (rec.written = {});
      if (!(a in rec.attrs) || w[a] !== cur) rec.attrs[a] = cur;
      const out = current === "it" ? rec.attrs[a] : tr(rec.attrs[a]);
      if (out !== cur) { w[a] = out; el.setAttribute(a, out); } else w[a] = cur;
    }
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) { textNode(root); return; }
    if (root.nodeType !== 1) return;
    const tag = root.tagName;
    if (tag === "SCRIPT" || tag === "STYLE") return;
    element(root);
    for (const k of root.childNodes) walk(k);
  }

  let observer = null;
  function observe() {
    if (observer || !window.MutationObserver) return;
    observer = new MutationObserver((list) => {
      for (const r of list) {
        if (r.type === "characterData") textNode(r.target);
        else if (r.type === "attributes") element(r.target);
        else for (const n of r.addedNodes) walk(n);
      }
    });
    observer.observe(document.documentElement, {
      subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS,
    });
  }

  let titleOriginal = null;
  function setLang(code) {
    code = catalogs[code] || code === "it" ? code : "it";
    current = code;
    compiled = code === "it" ? null : compile(code);
    document.documentElement.setAttribute("lang", code);
    if (titleOriginal === null) titleOriginal = document.title;
    document.title = current === "it" ? titleOriginal : tr(titleOriginal);
    walk(document.body);
    observe();
    try { localStorage.setItem(KEY, code); } catch (e) { /* private window or blocked storage */ }
  }

  function available() {
    return ["it"].concat(Object.keys(catalogs));
  }

  window.ccLang = { set: setLang, get: () => current, tr, available };

  // apply the last language straight away, so a reload does not flash Italian
  let saved = "it";
  try { saved = localStorage.getItem(KEY) || "it"; } catch (e) { /* ignore */ }
  const start = () => { if (saved !== "it") setLang(saved); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
