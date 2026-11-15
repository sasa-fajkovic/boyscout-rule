import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(new URL("../hooks/boyscout.py", import.meta.url));
export const sessionHash = (/** @type {string} */ id) => createHash("sha256").update(id).digest("hex");

/** Run the shared engine without a shell. Never retain input, log it, or reject a hook.
 * @param {string} event
 * @param {Record<string, unknown>} payload
 * @param {{python?: string, timeout?: number, env?: NodeJS.ProcessEnv}} [options]
 * @returns {Promise<string>}
 */
export function invoke(event, payload, options = {}) {
  const env = options.env ?? process.env;
  if ("CI" in env) return Promise.resolve("");
  return new Promise((resolve) => {
    let output = "";
    let settled = false;
    const finish = (text = "") => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(text);
    };
    const child = spawn(options.python ?? "python3", ["-I", "-S", script, "opencode", event], {
      env, stdio: ["pipe", "pipe", "ignore"],
    });
    const timer = setTimeout(() => { child.kill("SIGKILL"); finish(); }, options.timeout ?? 80);
    child.on("error", () => finish());
    child.stdin.on("error", () => { child.kill("SIGKILL"); finish(); });
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      output += chunk;
      if (output.length > 16384) { child.kill("SIGKILL"); finish(); }
    });
    child.on("close", (code) => {
      if (code !== 0) return finish();
      try { finish(JSON.parse(output).hookSpecificOutput?.additionalContext ?? ""); }
      catch { finish(); }
    });
    try { child.stdin.end(JSON.stringify(payload)); }
    catch { child.kill("SIGKILL"); finish(); }
  });
}
