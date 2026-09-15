#!/usr/bin/env node
/* Resolve every bare UPPER_CASE identifier in a file.
 *
 * node --check only validates syntax. A constant that exists in one version of
 * the file and not another passes --check and then throws at runtime — and if
 * the throw happens inside a promise chain it can surface as something
 * unrelated, such as "the engine is down". This catches that class. */
const fs = require("fs");
const file = process.argv[2];
const src = fs.readFileSync(file, "utf8");

const declared = new Set();
for (const m of src.matchAll(/\b(?:let|const|function)\s+([A-Za-z_$][\w$]*)/g)) declared.add(m[1]);
for (const m of src.matchAll(/\bvar\s+([^;]+);/g)) {
  for (const d of m[1].split(",")) {
    const n = d.trim().split(/[\s=]/)[0];
    if (n) declared.add(n);
  }
}

const globals = new Set(["URL", "JSON", "Math", "Date", "Object", "Array",
                         "Number", "String", "Promise", "NaN", "Infinity"]);

// Strip comments and string literals so prose and CSS do not look like code.
//
// This was four chained regexes and it only removed comments that started a
// line, so a TRAILING `// ... NC_ETA_MIN_GIB` was read as code and reported
// unresolved. A checker that cries wolf gets ignored, which costs more than
// having no checker, so this is a real scanner instead.
//
// Known limit: a regex literal containing a quote or a `//` can still confuse
// it. Neither appears in this codebase; if one ever does, the symptom is a
// false positive here, never a missed identifier at runtime.
function stripNonCode(text) {
  let out = "", i = 0;
  const n = text.length;
  while (i < n) {
    const c = text[i], d = text[i + 1];
    if (c === "/" && d === "/") {                   // line comment, anywhere
      while (i < n && text[i] !== "\n") i++;
      continue;
    }
    if (c === "/" && d === "*") {                   // block comment
      i += 2;
      while (i < n && !(text[i] === "*" && text[i + 1] === "/")) i++;
      i += 2;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") {      // string literal
      const q = c;
      i++;
      while (i < n && text[i] !== q) i += text[i] === "\\" ? 2 : 1;
      i++;
      out += q + q;
      continue;
    }
    out += c;
    i++;
  }
  return out;
}
const code = stripNonCode(src);

const used = new Set();
for (const m of code.matchAll(/(?<![.\w$])\b([A-Z][A-Z0-9_]{2,})\b/g)) used.add(m[1]);

const missing = [...used].filter((u) => !declared.has(u) && !globals.has(u));
if (missing.length) {
  console.error("  UNRESOLVED: " + missing.join(", "));
  process.exit(1);
}
console.log("  identifier check: clean");
