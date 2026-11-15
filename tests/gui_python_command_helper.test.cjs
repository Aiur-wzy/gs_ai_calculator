"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const Python = require("./helpers/python_command.cjs");

test("explicit GUI test Python path takes precedence without invoking another interpreter", () => {
  let probes = 0;
  const result = Python.resolvePythonCommand({
    env: { GENSHIN_TEST_PYTHON: "C:/controlled/python.exe", PYTHON: "ignored" },
    probe: () => { probes += 1; return false; },
  });
  assert.deepEqual(result, { command: "C:/controlled/python.exe", prefix: [] });
  assert.equal(probes, 0);
});

test("GUI test Python resolution prefers the current python before the launcher", () => {
  const calls = [];
  const result = Python.resolvePythonCommand({
    env: {},
    probe: (command, prefix) => {
      calls.push([command, prefix]);
      return command === "python";
    },
  });
  assert.deepEqual(result, { command: "python", prefix: [] });
  assert.deepEqual(calls, [["python", []]]);
});

test("GUI test Python resolution uses py 3.12 only after python is unavailable", () => {
  const calls = [];
  const result = Python.resolvePythonCommand({
    env: {},
    probe: (command, prefix) => {
      calls.push([command, prefix]);
      return command === "py";
    },
  });
  assert.deepEqual(result, { command: "py", prefix: ["-3.12"] });
  assert.deepEqual(calls, [["python", []], ["py", ["-3.12"]]]);
});

test("resolved GUI test Python is a real Python 3.12+ interpreter", () => {
  const version = Python.execFileSync(
    ["-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
    { encoding: "utf8", windowsHide: true },
  ).trim();
  const [major, minor] = version.split(".").map(Number);
  assert.ok(major > 3 || (major === 3 && minor >= 12), version);
});
