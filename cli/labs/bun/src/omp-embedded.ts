import {
  type CreateAgentSessionOptions,
  createAgentSession,
  type AgentSession,
  SessionManager,
  Settings,
} from "@oh-my-pi/pi-coding-agent";

import {
  type LabInvocation,
  type LabOutcome,
  validateInvocation,
} from "./contract.ts";

export const OMP_EMBEDDED_RESEARCH_ID =
  "research/omp-embedded@pi-coding-agent-18.0.8";

export interface OmpEmbeddedPlan {
  readonly auth: "harness-managed";
  readonly billingOwner: "harness-managed";
  readonly envIsolation: "requires-isolated-process";
  readonly options: CreateAgentSessionOptions;
  readonly taskEnvelope: string;
}

interface OmpSessionLike {
  abort(reason?: unknown): Promise<void>;
  dispose(): Promise<void>;
  prompt(text: string): Promise<boolean | void>;
  subscribe(listener: (event: unknown) => void): () => void;
}

type OmpSessionFactory = (
  options: CreateAgentSessionOptions,
) => Promise<{ session: OmpSessionLike }>;

export function buildOmpEmbeddedPlan(input: LabInvocation): OmpEmbeddedPlan {
  validateInvocation(input);
  const settings = Settings.isolated({
    "advisor.enabled": false,
    "async.enabled": false,
    "bash.autoBackground.enabled": false,
    "eval.autoBackground.enabled": false,
    "includeWorkspaceTree": false,
    "memory.backend": "none",
    "memories.enabled": false,
    "startup.checkUpdate": false,
  });

  return {
    auth: "harness-managed",
    billingOwner: "harness-managed",
    envIsolation: "requires-isolated-process",
    options: {
      agentDir: `${input.cwd}/.openprose-lab/omp`,
      contextFiles: [],
      cwd: input.cwd,
      disableExtensionDiscovery: true,
      enableIrc: false,
      enableLsp: false,
      enableMCP: false,
      hasUI: false,
      interactivePrompts: false,
      promptTemplates: [],
      restrictToolNames: true,
      rules: [],
      sessionManager: SessionManager.inMemory(input.cwd),
      settings,
      skills: [],
      slashCommands: [],
      systemPrompt: [input.runtimeImageUtf8],
      toolNames: ["read", "bash", "edit", "write", "task"],
    },
    taskEnvelope: input.taskEnvelope,
  };
}

export async function createOmpEmbeddedSession(
  input: LabInvocation,
): Promise<AgentSession> {
  const plan = buildOmpEmbeddedPlan(input);
  const { session } = await createAgentSession(plan.options);
  return session;
}

export async function runOmpEmbedded(
  input: LabInvocation,
  factory: OmpSessionFactory = createAgentSession,
): Promise<LabOutcome> {
  const plan = buildOmpEmbeddedPlan(input);
  const { session } = await factory(plan.options);
  const eventTypes: string[] = [];
  const unsubscribe = session.subscribe((event) => {
    if (isEventWithType(event)) {
      eventTypes.push(event.type);
    }
  });
  const onAbort = () => void session.abort(input.signal?.reason);
  input.signal?.addEventListener("abort", onAbort, { once: true });

  try {
    await session.prompt(plan.taskEnvelope);
  } finally {
    input.signal?.removeEventListener("abort", onAbort);
    unsubscribe();
    await session.dispose();
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
    throw new Error("OMP session settled without agent_end");
  }
  return {
    status: "completed",
    terminalEvent: "agent_end",
    text: "",
    diagnostics: eventTypes,
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
