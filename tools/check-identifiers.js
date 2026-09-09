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

// strip comments and string literals so prose and CSS do not look like code
const code = src
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/^\s*\/\/.*$/gm, "")
  .replace(/"(?:[^"\\]|\\.)*"/g, '""')
  .replace(/'(?:[^'\\]|\\.)*'/g, "''")
  .replace(/`(?:[^`\\]|\\.)*`/g, "``");

const used = new Set();
for (const m of code.matchAll(/(?<![.\w$])\b([A-Z][A-Z0-9_]{2,})\b/g)) used.add(m[1]);

const missing = [...used].filter((u) => !declared.has(u) && !globals.has(u));
if (missing.length) {
  console.error("  UNRESOLVED: " + missing.join(", "));
  process.exit(1);
}
console.log("  identifier check: clean");
