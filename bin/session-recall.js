#!/usr/bin/env node

const { execFileSync } = require("child_process");
const { resolve } = require("path");

const script = resolve(__dirname, "..", "session_recall.py");
const args = process.argv.slice(2);

try {
  execFileSync("python3", [script, ...args], { stdio: "inherit" });
} catch (e) {
  process.exit(e.status || 1);
}
