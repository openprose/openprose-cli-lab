import { Codex } from "@openai/codex-sdk";
import type {
  CodexOptions,
  ThreadEvent,
  ThreadOptions,
  TurnOptions,
} from "@openai/codex-sdk";

import {
  copiedEnv,
  type LabInvocation,
  type LabOutcome,
  validateInvocation,
} from "./contract.ts";

export const CODEX_SDK_RESEARCH_ID = "research/codex-sdk@0.150.1";

export interface CodexSdkPlan {
  readonly client: CodexOptions;
  readonly thread: ThreadOptions;
  readonly turn: TurnOptions;
  readonly taskEnvelope: string;
}

interface CodexThreadLike {
  runStreamed(
    input: string,
    options?: TurnOptions,
  ): Promise<{ events: AsyncGenerator<ThreadEvent> }>;
}

interface CodexClientLike {
  startThread(options?: ThreadOptions): CodexThreadLike;
}

export type CodexFactory = (options: CodexOptions) => CodexClientLike;

export function buildCodexSdkPlan(
  input: LabInvocation,
  codexPathOverride?: string,
): CodexSdkPlan {
  validateInvocation(input);
  const client: CodexOptions = {
    ...(codexPathOverride === undefined ? {} : { codexPathOverride }),
    config: {
      developer_instructions: input.runtimeImageUtf8,
    },
    env: copiedEnv(input.env),
  };

  return {
    client,
    thread: {
      additionalDirectories: [...(input.additionalDirectories ?? [])],
      approvalPolicy: "never",
      sandboxMode: "workspace-write",
      skipGitRepoCheck: true,
      workingDirectory: input.cwd,
    },
    turn: input.signal === undefined ? {} : { signal: input.signal },
    taskEnvelope: input.taskEnvelope,
  };
}

export async function runCodexSdk(
  input: LabInvocation,
  options: {
    readonly codexPathOverride?: string;
    readonly factory?: CodexFactory;
  } = {},
): Promise<LabOutcome> {
  const plan = buildCodexSdkPlan(input, options.codexPathOverride);
  const factory = options.factory ?? ((client) => new Codex(client));
  const thread = factory(plan.client).startThread(plan.thread);
  const { events } = await thread.runStreamed(plan.taskEnvelope, plan.turn);
  const text: string[] = [];
  const diagnostics: string[] = [];
  let terminalEvent: string | undefined;

  try {
    for await (const event of events) {
      if (event.type === "item.completed" && event.item.type === "agent_message") {
        text.push(event.item.text);
      } else if (event.type === "error") {
        diagnostics.push(event.message);
      } else if (event.type === "turn.failed") {
        terminalEvent = event.type;
        diagnostics.push(event.error.message);
      } else if (event.type === "turn.completed") {
        terminalEvent = event.type;
      }
    }
  } catch (error) {
    if (input.signal?.aborted) {
      return {
        status: "cancelled",
        terminalEvent: "abort-signal",
        text: text.join("\n"),
        diagnostics,
      };
    }
    throw error;
  }

  if (input.signal?.aborted) {
    return {
      status: "cancelled",
      terminalEvent: "abort-signal",
      text: text.join("\n"),
      diagnostics,
    };
  }
  if (terminalEvent === undefined) {
    throw new Error("Codex SDK stream ended without turn.completed or turn.failed");
  }

  return {
    status: terminalEvent === "turn.completed" ? "completed" : "failed",
    terminalEvent,
    text: text.join("\n"),
    diagnostics,
  };
}
