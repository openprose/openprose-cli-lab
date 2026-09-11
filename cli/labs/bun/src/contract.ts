export interface LabInvocation {
  readonly runtimeImageUtf8: string;
  readonly taskEnvelope: string;
  readonly cwd: string;
  readonly additionalDirectories?: readonly string[];
  readonly env: Readonly<Record<string, string>>;
  readonly signal?: AbortSignal;
}

export interface LabOutcome {
  readonly status: "completed" | "failed" | "cancelled";
  readonly terminalEvent: string;
  readonly text: string;
  readonly diagnostics: readonly string[];
}

export function validateInvocation(input: LabInvocation): void {
  for (const [name, value] of [
    ["runtimeImageUtf8", input.runtimeImageUtf8],
    ["taskEnvelope", input.taskEnvelope],
    ["cwd", input.cwd],
  ] as const) {
    if (value.length === 0) {
      throw new Error(`${name} must not be empty`);
    }
    if (value.includes("\0")) {
      throw new Error(`${name} must not contain NUL`);
    }
  }

  if (!input.cwd.startsWith("/")) {
    throw new Error("cwd must be absolute");
  }
}

export function copiedEnv(env: Readonly<Record<string, string>>): Record<string, string> {
  return Object.fromEntries(Object.entries(env));
}
