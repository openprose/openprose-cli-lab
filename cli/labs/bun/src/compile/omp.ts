import { createAgentSession, VERSION } from "@oh-my-pi/pi-coding-agent";

import { OMP_EMBEDDED_RESEARCH_ID } from "../omp-embedded.ts";

process.stdout.write(
  `${JSON.stringify({
    id: OMP_EMBEDDED_RESEARCH_ID,
    packageVersion: VERSION,
    sdkType: typeof createAgentSession,
  })}\n`,
);
