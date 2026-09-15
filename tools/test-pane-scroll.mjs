// Exercise the real scroll logic out of the shipped card.js, not a copy of it.
import { readFileSync } from "node:fs";
const src = readFileSync("src/web/card.js", "utf8");

// Pull the functions under test straight from the source.
const grab = (name) => {
  const i = src.indexOf("function " + name + "(");
  if (i < 0) throw new Error("not found: " + name);
  let d = 0, j = src.indexOf("{", i);
  for (let k = j; k < src.length; k++) {
    if (src[k] === "{") d++;
    else if (src[k] === "}") { d--; if (!d) return src.slice(i, k + 1); }
  }
  throw new Error("unbalanced: " + name);
};
const PANES = ["dgx-model-card-events", "dgx-model-card-logs"];
const AT_BOTTOM_SLOP = 8;
let paneText = {}, paneScroll = {};
const dom = {};
const document = { getElementById: (id) => dom[id] || null };

const code = [grab("paneState"), grab("applyPaneState"), grab("capturePanes"),
              grab("restorePanes"), grab("setPane")].join("\n");
// new Function, not eval: a module's eval keeps its declarations to itself.
const { paneState, applyPaneState, capturePanes, restorePanes, setPane } =
  new Function("document", "PANES", "AT_BOTTOM_SLOP", "paneText", "paneScroll",
    code + "\nreturn { paneState, applyPaneState, capturePanes, restorePanes, setPane };"
  )(document, PANES, AT_BOTTOM_SLOP, paneText, paneScroll);

// A <pre> stand-in: scrollHeight grows with the text, clientHeight is the box.
function pre(id, text = "", clientHeight = 150) {
  const o = {
    id, clientHeight, _top: 0,
    get scrollHeight() { return Math.max(this.clientHeight, this._lines * 15); },
    // Browsers clamp: scrollTop can never exceed scrollHeight - clientHeight.
    // A fake that does not clamp lets real bugs through, so this one does.
    get scrollTop() { return this._top; },
    set scrollTop(v) { this._top = Math.max(0, Math.min(v, this.scrollHeight - this.clientHeight)); },
    _lines: text ? text.split("\n").length : 0,
    get textContent() { return this._t; },
    set textContent(v) { this._t = v; this._lines = v ? v.split("\n").length : 0; },
  };
  o._t = text;
  dom[id] = o;
  return o;
}
const maxTop = (el) => el.scrollHeight - el.clientHeight;

const lines = (n, tag = "line") => Array.from({length: n}, (_, i) => `${tag} ${i}`).join("\n");

let fails = 0;
const check = (name, got, want) => {
  const ok = got === want;
  if (!ok) fails++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `  got ${got}, want ${want}`}`);
};

// 1. pinned to the bottom -> new content keeps it pinned
let p = pre("dgx-model-card-logs", lines(40));
p.scrollTop = maxTop(p);                            // user is at the bottom
setPane("dgx-model-card-logs", lines(50));
check("at bottom stays pinned after append", p.scrollTop, maxTop(p));

// 2. scrolled up -> new content must NOT move the view  (the actual bug)
p = pre("dgx-model-card-logs", lines(40));
p.scrollTop = 120;                                  // reading mid-log
setPane("dgx-model-card-logs", lines(50));
check("scrolled up is left alone after append", p.scrollTop, 120);

// 3. identical text is a no-op, scroll untouched
p = pre("dgx-model-card-logs", lines(40));
p.scrollTop = 77;
const before = p._t;
setPane("dgx-model-card-logs", lines(40));
check("identical text does not move scroll", p.scrollTop, 77);
check("identical text not rewritten", p._t, before);

// 4. capture then re-create the node (what replaceChild does) then restore
p = pre("dgx-model-card-events", lines(30));
p.scrollTop = 95;
capturePanes();
pre("dgx-model-card-events", lines(30));            // fresh node, scrollTop 0
restorePanes();
check("scroll survives a full re-render", dom["dgx-model-card-events"].scrollTop, 95);

// 5. same, but the user was at the bottom -> re-render should re-pin
p = pre("dgx-model-card-events", lines(30));
p.scrollTop = maxTop(p);
capturePanes();
const fresh = pre("dgx-model-card-events", lines(36));
restorePanes();
check("bottom-pinned survives re-render as bottom", fresh.scrollTop, maxTop(fresh));

// 6. slop: 5px off the bottom still counts as "at bottom"
p = pre("dgx-model-card-logs", lines(40));
p.scrollTop = maxTop(p) - 5;
check("5px from bottom counts as at-bottom", paneState(p).atBottom, true);
p.scrollTop = maxTop(p) - 40;
check("40px from bottom is not at-bottom", paneState(p).atBottom, false);

console.log(fails ? `\n${fails} FAILURE(S)` : "\nall pane-scroll tests passed");
process.exit(fails ? 1 : 0);
