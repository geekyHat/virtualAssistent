#!/usr/bin/env node
/**
 * Verifica licenze dell'intero albero npm (NewRay.md §4, §22.4).
 *
 * Fonte: `package-lock.json`, che fissa l'esatto albero installato da
 * `npm ci --ignore-scripts`. Ogni pacchetto deve dichiarare una licenza
 * permissiva dell'allowlist; le espressioni OR/AND vengono espanse e ogni
 * parte verificata. Un pacchetto senza licenza o fuori allowlist fa
 * fallire il gate (exit 1).
 *
 * CC-BY-4.0 è ammessa per `caniuse-lite`: dati di compatibilità browser
 * solo di sviluppo (browserslist), mai spediti nel bundle; l'attribuzione
 * è garantita dall'originale a monte.
 *
 * MPL-2.0 è ammessa per `lightningcss` (+ binari platform): motore CSS di
 * Tailwind v4, scelta esplicita in ADR 0005. Licenza file-level: il codice
 * generato in `dist/` non incorpora file sorgente MPL, quindi l'obbligo di
 * restituzione non si applica al bundle spedito.
 */
import { readFileSync } from "node:fs";

const ALLOWED = new Set([
  "MIT",
  "MIT-0",
  "ISC",
  "BSD-2-Clause",
  "BSD-3-Clause",
  "Apache-2.0",
  "0BSD",
  "BlueOak-1.0.0",
  "CC0-1.0",
  "CC-BY-4.0",
  "MPL-2.0",
  "Zlib",
  "Python-2.0",
  "Unicode-3.0",
]);

const lock = JSON.parse(readFileSync(new URL("../package-lock.json", import.meta.url), "utf-8"));
const packages = lock.packages ?? {};
const problems = [];
let checked = 0;

for (const [location, info] of Object.entries(packages)) {
  if (location === "") continue; // root del progetto
  const name = location.replace(/^node_modules\//, "").replace(/node_modules\//g, "/");
  const license = info.license;
  if (!license) {
    problems.push(`${name}: nessuna licenza dichiarata`);
    continue;
  }
  checked += 1;
  // "MIT", "(MIT OR CC0-1.0)", "Apache-2.0 AND BSD-3-Clause" …
  const parts = String(license)
    .replace(/[()]/g, " ")
    .split(/\s+(?:AND|OR)\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
  for (const part of parts) {
    if (!ALLOWED.has(part)) {
      problems.push(`${name}: licenza non approvata '${part}'`);
      break;
    }
  }
}

if (problems.length > 0) {
  console.error("Licenze non approvate:");
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
console.log(`Licenze verificate: ${checked} pacchetti del lockfile, tutte nell'allowlist.`);
