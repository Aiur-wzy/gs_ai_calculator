/** Same browser boundary as upstream apps/web/src/node-module-stub.ts.
 * Loader.fromInternal sees process.versions.node=0.0.0; the shell supplies
 * its public internal module table. Entering Node's loader is a hard error.
 */
export const createRequire = (): never => { throw new Error("node:module is not available in the browser"); };
