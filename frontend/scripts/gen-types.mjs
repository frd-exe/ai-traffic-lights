// Generate src/types/contract.gen.ts from docs/schemas/contract.bundle.json.
//   npm run gen:types            write
//   npm run gen:types -- --check exit 1 if the committed file is stale
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { compile } from "json-schema-to-typescript";

const here = dirname(fileURLToPath(import.meta.url));
const bundlePath = resolve(here, "../../docs/schemas/contract.bundle.json");
const outPath = resolve(here, "../src/types/contract.gen.ts");

// Pydantic puts a `title` on every property; json-schema-to-typescript would turn each into a
// named alias (Id, Id1, ...). Keep titles only on the top-level $defs.
function stripTitles(node, keep) {
  if (Array.isArray(node)) return node.map((n) => stripTitles(n, false));
  if (node && typeof node === "object") {
    const out = {};
    for (const [k, v] of Object.entries(node)) {
      if (k === "title" && !keep) continue;
      out[k] = k === "$defs" ? Object.fromEntries(Object.entries(v).map(([n, d]) => [n, stripTitles(d, true)])) : stripTitles(v, false);
    }
    return out;
  }
  return node;
}

const schema = stripTitles(JSON.parse(readFileSync(bundlePath, "utf8")), true);
let ts = await compile(schema, "Contract", {
  bannerComment: "/* Generated from docs/schemas/contract.bundle.json by `npm run gen:types`. Do not edit. */",
  additionalProperties: false,
  unreachableDefinitions: true,
  style: { singleQuote: false },
});
ts = ts.replace(/\r\n/g, "\n");

if (process.argv.includes("--check")) {
  const current = existsSync(outPath) ? readFileSync(outPath, "utf8") : "";
  if (current !== ts) {
    console.error("src/types/contract.gen.ts is stale; run: npm run gen:types");
    process.exit(1);
  }
  console.log("contract.gen.ts is up to date");
} else {
  writeFileSync(outPath, ts, "utf8");
  console.log("wrote src/types/contract.gen.ts");
}
