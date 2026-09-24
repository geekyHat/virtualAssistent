#!/usr/bin/env node
/**
 * Budget bundle della build di produzione (NewRay.md §22.4).
 *
 * Da eseguire DOPO `npm run build`: somma i byte dei JS e CSS in
 * `dist/assets` e li confronta con i budget. Se un budget viene superato,
 * il gate fallisce: o si riduce il bundle o il budget viene alzato con una
 * decisione documentata (commit/README), mai silenziosamente.
 *
 * Budget P-04 (24 settembre 2026): JS 480 KB / CSS 64 KB. Il rendering
 * Markdown sicuro (marked + DOMPurify) porta la build misurata da ~380 KB
 * a ~455 KB; il margine residuo è ~25 KB. Il totale include tutti i chunk.
 */
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const BUDGET_JS_KB = 480;
const BUDGET_CSS_KB = 64;
const ASSETS = join(process.cwd(), "dist", "assets");

let jsBytes = 0;
let cssBytes = 0;
for (const name of readdirSync(ASSETS)) {
  const size = statSync(join(ASSETS, name)).size;
  if (name.endsWith(".js")) jsBytes += size;
  if (name.endsWith(".css")) cssBytes += size;
}

const kb = (bytes) => Math.round((bytes / 1024) * 10) / 10;
const problems = [];
if (jsBytes > BUDGET_JS_KB * 1024)
  problems.push(`JS ${kb(jsBytes)} KB > budget ${BUDGET_JS_KB} KB`);
if (cssBytes > BUDGET_CSS_KB * 1024)
  problems.push(`CSS ${kb(cssBytes)} KB > budget ${BUDGET_CSS_KB} KB`);

if (problems.length > 0) {
  console.error("Budget bundle superato:");
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
console.log(`Budget bundle rispettati: JS ${kb(jsBytes)} KB, CSS ${kb(cssBytes)} KB.`);
