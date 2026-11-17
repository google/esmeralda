// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Generates src/api/schema.ts from ../openapi.json (the contract the BFF publishes).
//   node scripts/gen-types.mjs           rewrite src/api/schema.ts
//   node scripts/gen-types.mjs --check   exit 1 if src/api/schema.ts is stale (part of `npm test`)
import { readFile, writeFile } from "node:fs/promises";
import openapiTS, { astToString } from "openapi-typescript";

const SPEC = new URL("../../openapi.json", import.meta.url);
const OUT = new URL("../src/api/schema.ts", import.meta.url);
const HEADER =
  "// Generated from apps/frontends/esmeralda-ui/openapi.json by `npm run gen:types`. Do not edit.\n" +
  "// The BFF regenerates openapi.json with `python -m esmeralda_ui.openapi`.\n\n";

const ast = await openapiTS(SPEC, { alphabetize: true });
const expected = HEADER + astToString(ast);

if (process.argv.includes("--check")) {
  const current = await readFile(OUT, "utf8").catch(() => "");
  if (current !== expected) {
    console.error("src/api/schema.ts is stale: run `npm run gen:types`");
    process.exit(1);
  }
  console.log("src/api/schema.ts is up to date with openapi.json");
} else {
  await writeFile(OUT, expected);
  console.log("wrote src/api/schema.ts");
}
