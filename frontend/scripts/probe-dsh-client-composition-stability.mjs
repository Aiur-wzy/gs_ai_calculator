import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {resolve} from "node:path";

import {FRONTEND_ROOT} from "./dsh-client-review-lib.mjs";

const run = promisify(execFile);
const probe = resolve(FRONTEND_ROOT, "scripts", "probe-dsh-client-composition.mjs");
const samples = await Promise.all(Array.from({length: 6}, async () => {
  const {stdout, stderr} = await run(process.execPath, [probe], {
    cwd: FRONTEND_ROOT,
    maxBuffer: 4 * 1024 * 1024,
  });
  if (stderr !== "") throw new Error(`dsh_client_composition_stability_stderr:${stderr}`);
  return stdout;
}));
if (samples.some(sample => sample !== samples[0])) {
  throw new Error("dsh_client_composition_probe_output_unstable");
}
process.stdout.write(samples[0]);
