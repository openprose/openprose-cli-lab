import { getModels } from "@mariozechner/pi-ai";
import { createAgentSession } from "@mariozechner/pi-coding-agent";

import { PI_OPENROUTER_RESEARCH_ID } from "../pi-openrouter.ts";

process.stdout.write(
  `${JSON.stringify({
    id: PI_OPENROUTER_RESEARCH_ID,
    openRouterModels: getModels("openrouter").length,
    sdkType: typeof createAgentSession,
  })}\n`,
);
