import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { mkdtemp, readFile, readdir, rm, writeFile, chmod } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import plugin from "../../opencode/index.js";
import { invoke } from "../../opencode/bridge.js";

let directory, saved, dispose;
beforeEach(async () => {
  directory = await mkdtemp(join(tmpdir(), "boyscout-adapter-"));
  saved = { ...process.env };
  delete process.env.CI;
  process.env.CLAUDE_PLUGIN_DATA = join(directory, "data");
});
afterEach(async () => {
  await dispose?.();
  dispose = undefined;
  process.env = saved;
  await rm(directory, { recursive: true, force: true });
});

async function harness({ child = false, broken = false } = {}) {
  const hooks = new Map();
  let skill, stopped = 0, deleted;
  const ctx = {
    location: { directory },
    skill: { transform: async (fn) => {
      fn({ add: (s) => { skill = s; } });
      return { dispose: async () => { stopped++; } };
    } },
    session: {
      get: async () => {
        if (broken === "hang") return new Promise(() => {});
        if (broken) throw Error("unavailable");
        return { location: { directory }, parentID: child ? "parent" : undefined };
      },
      hook: async (name, fn) => {
        hooks.set(name, fn);
        return { dispose: async () => { stopped++; } };
      },
    },
    tool: { hook: async (name, fn) => {
      hooks.set(name, fn);
      return { dispose: async () => { stopped++; } };
    } },
    event: { subscribe: async function* ({ signal }) {
      yield await new Promise((resolve) => {
        deleted = resolve;
        signal.addEventListener("abort", () => resolve({ type: "shutdown" }), { once: true });
      });
    } },
  };
  dispose = await plugin.setup(ctx);
  return {
    hooks, skill, stopped: () => stopped,
    context: async (sessionID = "root") => {
      const event = { sessionID, system: [], messages: [], tools: {}, options: {} };
      await hooks.get("context")(event);
      return event.system.map((p) => p.text).join("\n");
    },
    prompt: (text, id = "message-1", sessionID = "root") => hooks.get("prompt")({ sessionID, messageID: id, prompt: { text } }),
    shell: async (fixture, command, sessionID = "root", id = Math.random().toString()) => {
      const event = JSON.parse(await readFile(new URL(`../fixtures/opencode/${fixture}.json`, import.meta.url)));
      event.sessionID = sessionID;
      event.id = id;
      if (command) event.input.command = command;
      await hooks.get("execute.after")(event);
      return event;
    },
    remove: async (sessionID = "root") => {
      deleted({ type: "session.deleted", data: { sessionID } });
      await new Promise((resolve) => setTimeout(resolve, 100));
    },
  };
}
const text = (e) => e.result.content.map((p) => p.text ?? "").join("\n");

test("registers the shared skill and injects context on resumed requests", async () => {
  const h = await harness();
  assert.equal(h.skill.id, "boyscout-rule");
  assert.match(h.skill.content, /Never push to the default branch/);
  const first = await h.context();
  assert.match(first, /boyscout-rule is active/);
  assert.match(first, /Session id for branch names: [a-f0-9]{10}/);
  assert.equal(await h.context(), first);
  await dispose();
  assert.equal(h.stopped(), 4);
  dispose = undefined;
});

test("captured v2 shell results trigger a nudge for silent failures", async () => {
  const h = await harness();
  const failure = await h.shell("shell-failure");
  assert.doesNotMatch(text(failure), /node failed/);
  const success = await h.shell("shell-success");
  assert.match(text(success), /node failed/);
  assert.equal(success.result.output.exit, 0);
  assert.equal(success.result.content[0].text, "42\n");
  const again = await h.shell("shell-success");
  assert.doesNotMatch(text(again), /node failed/);
});

test("corrections reach context once without changing the user prompt", async () => {
  const h = await harness();
  await h.prompt(JSON.stringify("no, use pnpm"));
  assert.match(await h.context(), /the user corrected you/);
  assert.doesNotMatch(await h.context(), /the user corrected you/);
  // Duplicate prompt admission must not advance counters or spend the budget.
  await h.prompt(JSON.stringify("no, use pnpm"));
  assert.doesNotMatch(await h.context(), /the user corrected you/);
});

test("command sequences and the shared three-nudge cap", async () => {
  const h = await harness();
  for (const cmd of ["make a", "make b", "node build.js", "make a", "make b"]) {
    assert.doesNotMatch(text(await h.shell("shell-success", cmd)), /the same sequence/);
  }
  assert.match(text(await h.shell("shell-success", "node build.js")), /the same sequence/);
  for (const program of ["npm", "cargo", "pytest"]) {
    await h.shell("shell-failure", program + " wrong");
    const response = text(await h.shell("shell-success", program + " right"));
    if (program === "pytest") assert.doesNotMatch(response, /pytest failed/);
    else assert.match(response, /failed/);
  }
});

test("no shared state between sessions and state contains no input", async () => {
  const h = await harness();
  await h.shell("shell-failure", "npm private-token-123");
  assert.doesNotMatch(text(await h.shell("shell-success", "npm right", "other")), /npm failed/);
  const folder = join(directory, "data", "sessions");
  for (const name of await readdir(folder)) {
    if (!name.endsWith(".json")) continue;
    const state = await readFile(join(folder, name), "utf8");
    assert.doesNotMatch(state, /private-token|npm|root|other|no output/);
  }
  await h.remove();
  assert.equal((await readdir(folder)).filter((n) => n.endsWith(".json")).length, 1);
});

test("skips children and fails open when session lookup fails", async () => {
  for (const options of [{ child: true }, { broken: true }, { broken: "hang" }]) {
    const h = await harness(options);
    assert.equal(await h.context(), "");
    await h.prompt("no, stop");
    assert.doesNotMatch(text(await h.shell("shell-success")), /boyscout-rule/);
    await dispose();
    dispose = undefined;
  }
});

test("errors, interrupted calls and missing exit status never imply success", async () => {
  const h = await harness();
  await h.shell("shell-failure");
  const invokeHook = h.hooks.get("execute.after");
  const event = { tool: "shell", sessionID: "root", id: "error", input: { command: "node success.js" }, status: "error", error: { message: "interrupted" } };
  await invokeHook(event);
  assert.equal(event.status, "error");
  const unknown = { ...event, id: "unknown", status: "completed", result: { content: "success?" } };
  await invokeHook(unknown);
  assert.equal(unknown.result.content, "success?");
  assert.match(text(await h.shell("shell-success")), /node failed/);
});

test("CI disables all registrations even when CI=false", async () => {
  process.env.CI = "false";
  const h = await harness();
  assert.equal(h.hooks.size, 0);
  assert.equal(h.skill, undefined);
  assert.equal(await invoke("SessionStart", { session_id: "root" }), "");
});

test("bridge survives a missing Python and kills a hung process", async () => {
  assert.equal(await invoke("SessionStart", {}, { python: join(directory, "missing") }), "");
  const slow = join(directory, "slow-python");
  await writeFile(slow, "#!/bin/sh\nexec sleep 30\n");
  await chmod(slow, 0o700);
  const start = performance.now();
  assert.equal(await invoke("SessionStart", {}, { python: slow, timeout: 25 }), "");
  assert.ok(performance.now() - start < 300);
});
