"use strict";

const childProcess = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const DEFAULT_TEST_TEMP = path.resolve(__dirname, "../..", ".test-tmp");

function defaultProbe(command, prefix) {
  const result = childProcess.spawnSync(
    command,
    [...prefix, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"],
    {
      encoding: "utf8",
      windowsHide: true,
      timeout: 5000,
    },
  );
  return result.status === 0 && !result.error;
}

function resolvePythonCommand({ env = process.env, probe = defaultProbe } = {}) {
  const configured = typeof env.GENSHIN_TEST_PYTHON === "string"
    ? env.GENSHIN_TEST_PYTHON.trim()
    : "";
  if (configured) return Object.freeze({ command: configured, prefix: [] });

  const current = typeof env.PYTHON === "string" && env.PYTHON.trim()
    ? env.PYTHON.trim()
    : "python";
  if (probe(current, [])) return Object.freeze({ command: current, prefix: [] });
  if (current !== "python" && probe("python", [])) {
    return Object.freeze({ command: "python", prefix: [] });
  }
  if (probe("py", ["-3.12"])) {
    return Object.freeze({ command: "py", prefix: ["-3.12"] });
  }
  throw new Error(
    "Python 3.12+ is required for GUI integration tests. "
      + "Set GENSHIN_TEST_PYTHON to an explicit interpreter path.",
  );
}

let cachedDefault = null;
function current() {
  if (!cachedDefault) cachedDefault = resolvePythonCommand();
  return cachedDefault;
}

function argumentsFor(args) {
  if (!Array.isArray(args)) throw new TypeError("python_test_arguments_required");
  return [...current().prefix, ...args];
}

function optionsFor(options = {}) {
  const testTemp = typeof process.env.GENSHIN_TEST_TMPDIR === "string"
    && process.env.GENSHIN_TEST_TMPDIR.trim()
    ? path.resolve(process.env.GENSHIN_TEST_TMPDIR.trim())
    : DEFAULT_TEST_TEMP;
  fs.mkdirSync(testTemp, { recursive: true });
  return {
    ...options,
    env: {
      ...process.env,
      TEMP: testTemp,
      TMP: testTemp,
      ...(options.env || {}),
    },
  };
}

function execFileSync(args, options) {
  return childProcess.execFileSync(current().command, argumentsFor(args), optionsFor(options));
}

function spawn(args, options) {
  return childProcess.spawn(current().command, argumentsFor(args), optionsFor(options));
}

function spawnSync(args, options) {
  return childProcess.spawnSync(current().command, argumentsFor(args), optionsFor(options));
}

module.exports = Object.freeze({
  resolvePythonCommand,
  execFileSync,
  spawn,
  spawnSync,
});
