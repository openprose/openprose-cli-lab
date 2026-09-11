import { Codex } from "@openai/codex-sdk";

import { CODEX_SDK_RESEARCH_ID } from "../codex-sdk.ts";

process.stdout.write(
  `${JSON.stringify({
    id: CODEX_SDK_RESEARCH_ID,
    sdkType: typeof Codex,
    clientConstructed: new Codex() instanceof Codex,
  })}\n`,
);
