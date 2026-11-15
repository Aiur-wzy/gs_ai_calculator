import {readFile, writeFile} from "node:fs/promises";
import {resolve} from "node:path";

import {
  FRONTEND_ROOT,
  buildAcceptedManifest,
  composeReviewedRegistry,
  readCompositionContract,
  stableJson,
} from "./dsh-client-review-lib.mjs";

const target = resolve(FRONTEND_ROOT, "dsh-client-accepted-manifest.json");
const check = process.argv.includes("--check");
const contract = await readCompositionContract();
const {ctx, graph} = await composeReviewedRegistry(contract);
try {
  const output = stableJson(await buildAcceptedManifest(contract, ctx, graph));
  if (check) {
    const current = await readFile(target, "utf8");
    if (current !== output) throw new Error("dsh_client_accepted_manifest_stale");
  } else {
    await writeFile(target, output, "utf8");
  }
} finally {
  await ctx.fiber.dispose();
}
