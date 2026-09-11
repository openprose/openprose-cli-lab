import { query as sdkQuery } from "@anthropic-ai/claude-agent-sdk";
import type {
  Options,
  Query,
  SDKMessage,
} from "@anthropic-ai/claude-agent-sdk";

import {
  copiedEnv,
  type LabInvocation,
  type LabOutcome,
  validateInvocation,
} from "./contract.ts";

export const CLAUDE_AGENT_SDK_RESEARCH_ID =
  "research/claude-agent-sdk@0.3.250";

export type ClaudeQuery = (params: {
  prompt: string;
  options?: Options;
}) => Query;

export interface ClaudeAgentSdkPlan {
  readonly prompt: string;
  readonly options: Options;
}

export function buildClaudeAgentSdkPlan(
  input: LabInvocation,
  pathToClaudeCodeExecutable?: string,
): ClaudeAgentSdkPlan {
  validateInvocation(input);
  const abortController = new AbortController();
  if (input.signal?.aborted) {
    abortController.abort(input.signal.reason);
  } else {
    input.signal?.addEventListener(
      "abort",
      () => abortController.abort(input.signal?.reason),
      { once: true },
    );
  }

  return {
    prompt: input.taskEnvelope,
    options: {
      abortController,
      additionalDirectories: [...(input.additionalDirectories ?? [])],
      cwd: input.cwd,
      env: copiedEnv(input.env),
      includePartialMessages: true,
      ...(pathToClaudeCodeExecutable === undefined
        ? {}
        : { pathToClaudeCodeExecutable }),
      permissionMode: "dontAsk",
      persistSession: false,
      plugins: [],
      settingSources: [],
      skills: [],
      strictMcpConfig: true,
      systemPrompt: {
        type: "preset",
        preset: "claude_code",
        append: input.runtimeImageUtf8,
      },
    },
  };
}

export async function runClaudeAgentSdk(
  input: LabInvocation,
  options: {
    readonly pathToClaudeCodeExecutable?: string;
    readonly query?: ClaudeQuery;
  } = {},
): Promise<LabOutcome> {
  const plan = buildClaudeAgentSdkPlan(
    input,
    options.pathToClaudeCodeExecutable,
  );
  const stream = (options.query ?? sdkQuery)(plan);
  const text: string[] = [];
  const diagnostics: string[] = [];
  let terminalEvent: string | undefined;
  let failed = false;

  try {
    for await (const message of stream) {
      collectMessage(message, text, diagnostics);
      if (message.type === "result") {
        terminalEvent = `result:${message.subtype}`;
        failed = message.subtype !== "success";
      }
    }
  } catch (error) {
    if (input.signal?.aborted) {
      return {
        status: "cancelled",
        terminalEvent: "abort-signal",
        text: text.join(""),
        diagnostics,
      };
    }
    throw error;
  } finally {
    stream.close();
  }

  if (input.signal?.aborted) {
    return {
      status: "cancelled",
      terminalEvent: "abort-signal",
      text: text.join(""),
      diagnostics,
    };
  }
  if (terminalEvent === undefined) {
    throw new Error("Claude Agent SDK stream ended without a result message");
  }

  return {
    status: failed ? "failed" : "completed",
    terminalEvent,
    text: text.join(""),
    diagnostics,
  };
}

function collectMessage(
  message: SDKMessage,
  text: string[],
  diagnostics: string[],
): void {
  if (message.type === "stream_event") {
    const event = message.event;
    if (
      event.type === "content_block_delta" &&
      event.delta.type === "text_delta"
    ) {
      text.push(event.delta.text);
    }
    return;
  }

  if (message.type === "result" && message.subtype !== "success") {
    diagnostics.push(...message.errors);
  }
}
