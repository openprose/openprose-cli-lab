import { describe, expect, test } from "bun:test";

import { validateInvocation } from "../src/contract.ts";

const valid = {
  cwd: "/workspace",
  env: {},
  runtimeImageUtf8: "runtime-image",
  taskEnvelope: "task-envelope",
};

describe("lab contract", () => {
  test("accepts opaque runtime image and task envelope", () => {
    expect(() => validateInvocation(valid)).not.toThrow();
  });

  test.each([
    ["runtimeImageUtf8", { ...valid, runtimeImageUtf8: "bad\0image" }],
    ["taskEnvelope", { ...valid, taskEnvelope: "bad\0task" }],
    ["cwd", { ...valid, cwd: "relative" }],
  ])("rejects invalid %s", (_name, input) => {
    expect(() => validateInvocation(input)).toThrow();
  });
});
