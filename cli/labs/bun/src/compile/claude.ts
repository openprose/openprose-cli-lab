// The package documents this file-asset import for Bun standalone executables.
// @ts-expect-error Platform binary subpaths intentionally have no TypeScript declaration.
import binPath from "@anthropic-ai/claude-agent-sdk-darwin-arm64/claude" with {
  type: "file",
};
import { extractFromBunfs } from "@anthropic-ai/claude-agent-sdk/extract";
import { query } from "@anthropic-ai/claude-agent-sdk";

import { CLAUDE_AGENT_SDK_RESEARCH_ID } from "../claude-agent-sdk.ts";

const extracted = extractFromBunfs(binPath);
process.stdout.write(
  `${JSON.stringify({
    id: CLAUDE_AGENT_SDK_RESEARCH_ID,
    queryType: typeof query,
    extracted: typeof extracted === "string" && extracted.length > 0,
  })}\n`,
);
