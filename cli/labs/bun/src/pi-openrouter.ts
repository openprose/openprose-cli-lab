import { getModels } from "@mariozechner/pi-ai";
import {
  AuthStorage,
  createAgentSession,
  createExtensionRuntime,
  ModelRegistry,
  type ResourceLoader,
  SessionManager,
  SettingsManager,
} from "@mariozechner/pi-coding-agent";
import type {
  AgentSession,
  CreateAgentSessionOptions,
} from "@mariozechner/pi-coding-agent";

import {
  type LabInvocation,
  type LabOutcome,
  validateInvocation,
} from "./contract.ts";

export const PI_OPENROUTER_RESEARCH_ID =
  "research/pi-openrouter@pi-coding-agent-0.73.1";

export interface PiOpenRouterPlan {
  readonly auth: "openrouter-api-key-or-oauth";
  readonly billingOwner: "openrouter";
  readonly envIsolation: "requires-isolated-process";
  readonly modelId: string;
  readonly options: CreateAgentSessionOptions;
  readonly taskEnvelope: string;
}

interface PiSessionLike {
  abort(): Promise<void>;
  dispose(): void;
  prompt(text: string): Promise<void>;
  subscribe(listener: (event: unknown) => void): () => void;
}

type PiSessionFactory = (
  options: CreateAgentSessionOptions,
) => Promise<{ session: PiSessionLike }>;

export function buildPiOpenRouterPlan(
  input: LabInvocation,
  modelId?: string,
): PiOpenRouterPlan {
  validateInvocation(input);
  const models = getModels("openrouter");
  const model =
    modelId === undefined
      ? models[0]
      : models.find((candidate) => candidate.id === modelId);
  if (model === undefined) {
    throw new Error(`unknown OpenRouter model: ${modelId ?? "<none>"}`);
  }

  const authStorage = AuthStorage.inMemory();
  const resourceLoader = opaqueResourceLoader(input.runtimeImageUtf8);
  const options: CreateAgentSessionOptions = {
    agentDir: `${input.cwd}/.openprose-lab/pi`,
    authStorage,
    cwd: input.cwd,
    model,
    modelRegistry: ModelRegistry.inMemory(authStorage),
    resourceLoader,
    sessionManager: SessionManager.inMemory(input.cwd),
    settingsManager: SettingsManager.inMemory({
      compaction: { enabled: false },
      extensions: [],
      packages: [],
      prompts: [],
      retry: { enabled: false },
      skills: [],
      themes: [],
    }),
    tools: ["read", "bash", "edit", "write"],
  };

  return {
    auth: "openrouter-api-key-or-oauth",
    billingOwner: "openrouter",
    envIsolation: "requires-isolated-process",
    modelId: model.id,
    options,
    taskEnvelope: input.taskEnvelope,
  };
}

export async function createPiOpenRouterSession(
  input: LabInvocation,
  modelId?: string,
): Promise<AgentSession> {
  const plan = buildPiOpenRouterPlan(input, modelId);
  const { session } = await createAgentSession(plan.options);
  return session;
}

export async function runPiOpenRouter(
  input: LabInvocation,
  options: {
    readonly factory?: PiSessionFactory;
    readonly modelId?: string;
  } = {},
): Promise<LabOutcome> {
  const plan = buildPiOpenRouterPlan(input, options.modelId);
  const { session } = await (options.factory ?? createAgentSession)(plan.options);
  const eventTypes: string[] = [];
  const unsubscribe = session.subscribe((event) => {
    if (isEventWithType(event)) {
      eventTypes.push(event.type);
    }
  });
  const onAbort = () => void session.abort();
  input.signal?.addEventListener("abort", onAbort, { once: true });

  try {
    await session.prompt(plan.taskEnvelope);
  } finally {
    input.signal?.removeEventListener("abort", onAbort);
    unsubscribe();
    session.dispose();
  }

  if (input.signal?.aborted) {
    return {
      status: "cancelled",
      terminalEvent: "abort-signal",
      text: "",
      diagnostics: eventTypes,
    };
  }
  if (!eventTypes.includes("agent_end")) {
    throw new Error("Pi session settled without agent_end");
  }
  return {
    status: "completed",
    terminalEvent: "agent_end",
    text: "",
    diagnostics: eventTypes,
  };
}

function opaqueResourceLoader(runtimeImageUtf8: string): ResourceLoader {
  return {
    extendResources: () => {},
    getAgentsFiles: () => ({ agentsFiles: [] }),
    getAppendSystemPrompt: () => [],
    getExtensions: () => ({
      errors: [],
      extensions: [],
      runtime: createExtensionRuntime(),
    }),
    getPrompts: () => ({ diagnostics: [], prompts: [] }),
    getSkills: () => ({ diagnostics: [], skills: [] }),
    getSystemPrompt: () => runtimeImageUtf8,
    getThemes: () => ({ diagnostics: [], themes: [] }),
    reload: async () => {},
  };
}

function isEventWithType(value: unknown): value is { type: string } {
  return (
    typeof value === "object" &&
    value !== null &&
    "type" in value &&
    typeof value.type === "string"
  );
}
