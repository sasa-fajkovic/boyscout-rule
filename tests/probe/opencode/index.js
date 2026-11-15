// Test-only observer: capture context after the shipped plugin has modified it.
import { appendFile } from "node:fs/promises";
export default {
  id: "boyscout-test-observer",
  async setup(ctx) {
    const log = process.env.BOYSCOUT_PROBE_LOG;
    await ctx.session.hook("prompt", async (event) => {
      let info;
      try { info = await ctx.session.get({ sessionID: event.sessionID }); } catch (e) { info = String(e); }
      await appendFile(log, JSON.stringify({ promptEvent: event, info, location: ctx.location }) + "\n");
    });
    await ctx.session.hook("context", async (event) => {
      await appendFile(log, JSON.stringify(event.system) + "\n");
    });
    await ctx.tool.hook("execute.after", async (event) => {
      await appendFile(log, JSON.stringify(event) + "\n");
    });
  },
};
