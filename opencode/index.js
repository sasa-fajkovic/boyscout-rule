import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { invoke, sessionHash } from "./bridge.js";

/** @type {import('@opencode/plugin').Plugin.Plugin} */
const plugin = {
  id: "boyscout-rule",
  async setup(ctx) {
    if ("CI" in process.env) return;
    // Only hashes and a pending-correction flag live in adapter state.
    const corrections = new Set();
    const controller = new AbortController();
    /** @type {Array<{dispose: () => Promise<void>}>} */
    const registrations = [];
    const root = async (/** @type {Parameters<typeof ctx.session.get>[0]['sessionID']} */ sessionID) => {
      const controller = new AbortController();
      let timer;
      try {
        const info = await Promise.race([
          ctx.session.get({ sessionID }, { signal: controller.signal }),
          new Promise((_, reject) => {
            timer = setTimeout(() => { controller.abort(); reject(new Error("session lookup timed out")); }, 15);
          }),
        ]);
        return !info.parentID && info.location.directory === ctx.location.directory;
      } finally { clearTimeout(timer); }
    };
    const safe = (/** @type {() => Promise<void>} */ work) => work().catch(() => {});
    try {
      // Warm stdlib pages during plugin loading, outside the 80 ms per-event deadline.
      await invoke("SessionStart", { source: "resume" }, { timeout: 1000 });
      const path = fileURLToPath(new URL("../skills/boyscout-rule/SKILL.md", import.meta.url));
      const skill = await readFile(path, "utf8");
      const description = /^description: (.+)$/m.exec(skill)?.[1] ?? "Publish verified repository improvements.";
      registrations.push(await ctx.skill.transform((editor) => {
        editor.add(/** @type {import('@opencode/plugin').Skill.Info} */ ({
          id: "boyscout-rule", name: "boyscout-rule", path, description,
          content: skill.replace(/^---\n[\s\S]*?\n---\n/, ""),
        }));
      }));
      registrations.push(await ctx.session.hook("prompt", (event) => safe(async () => {
        if (!await root(event.sessionID)) return;
        // The v2 CLI can submit a JSON-quoted message argument. Interactive prompts are plain text.
        let text = event.prompt.text;
        try {
          const decoded = JSON.parse(text ?? "");
          if (typeof decoded === "string") text = decoded;
        } catch { /* Plain user text. */ }
        const message = await invoke("UserPromptSubmit", {
          session_id: event.sessionID, prompt: text, message_id: event.messageID,
        });
        if (message) corrections.add(sessionHash(event.sessionID));
      })));
      registrations.push(await ctx.session.hook("context", (event) => safe(async () => {
        if (!await root(event.sessionID)) return;
        const key = sessionHash(event.sessionID);
        const message = await invoke("SessionStart", {
          session_id: event.sessionID, source: "resume", correction_context: corrections.has(key),
        });
        if (message) {
          event.system.push({ type: "text", text: message });
          corrections.delete(key);
        }
      })));
      registrations.push(await ctx.tool.hook("execute.after", (event) => safe(async () => {
        if (!["shell", "bash"].includes(event.tool) || !await root(event.sessionID)) return;
        const input = /** @type {{command?: unknown}} */ (event.input);
        if (typeof input?.command !== "string") return;
        const result = event.status === "completed" ? event.result : undefined;
        const content = result?.content;
        const output = typeof content === "string" ? content.slice(-8000)
          : (content ?? []).slice(-8).map((p) => p.type === "text" ? p.text.slice(-8000) : "").join("\n").slice(-8000);
        const message = await invoke("PostToolUse", {
          session_id: event.sessionID, tool_name: "Bash", tool_input: { command: input.command },
          tool_response: output, exit_code: result?.metadata?.exit,
          interrupted: event.status === "error", call_id: event.id,
        });
        if (message && result && event.status === "completed") {
          const parts = typeof content === "string" ? [{ type: /** @type {const} */ ("text"), text: content }] : [...(content ?? [])];
          event.result = { ...result, content: [...parts, { type: "text", text: message }] };
        }
      })));
      void safe(async () => {
        for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
          if (event.type !== "session.deleted") continue;
          corrections.delete(sessionHash(event.data.sessionID));
          await invoke("SessionEnd", { session_id: event.data.sessionID });
        }
      });
    } catch {
      controller.abort();
      await Promise.allSettled(registrations.map((r) => r.dispose()));
      return;
    }
    return async () => {
      controller.abort();
      corrections.clear();
      await Promise.allSettled(registrations.map((r) => r.dispose()));
    };
  },
};
export default plugin;
