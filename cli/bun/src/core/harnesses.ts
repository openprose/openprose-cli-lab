import { TEST_SEAMS_ENABLED } from "./build";
import type { RuntimePrerequisiteObservation } from "./types";

export interface HarnessDescriptor {
  id: string;
  runtime: "hosted-runtime" | "installed-process" | "deterministic-mock";
  availability: "available" | "unavailable" | "not-implemented" | "missing" | "incompatible" | "needs-auth" | "blocked";
  detectedVersion: string | null;
  transports: string[];
  authCategory: "openprose-account" | "harness-managed" | "none-test-only";
  billingOwner: "openprose" | "user-provider" | "test-fixture";
  strictWrapperConformant: boolean;
  testOnly: boolean;
  runtimePrerequisites?: RuntimePrerequisiteObservation[];
  admissionBlock?:
    | "prompt-channel"
    | "strict-admission"
    | "terminal-envelope"
    | "release-image"
    | "test-seams-disabled";
}

export const harnesses: readonly HarnessDescriptor[] = [
  {
    id: "openprose",
    runtime: "hosted-runtime",
    availability: "not-implemented",
    detectedVersion: null,
    transports: ["hosted"],
    authCategory: "openprose-account",
    billingOwner: "openprose",
    strictWrapperConformant: false,
    testOnly: false,
  },
  {
    id: "prime",
    runtime: "installed-process",
    availability: "missing",
    detectedVersion: null,
    transports: ["rpc"],
    authCategory: "harness-managed",
    billingOwner: "user-provider",
    strictWrapperConformant: false,
    testOnly: false,
  },
  {
    id: "omp",
    runtime: "installed-process",
    availability: "missing",
    detectedVersion: null,
    transports: ["rpc"],
    authCategory: "harness-managed",
    billingOwner: "user-provider",
    strictWrapperConformant: false,
    testOnly: false,
    runtimePrerequisites: [{
      runtime: "bun",
      versionRange: ">=1.3.14",
      detectedVersion: null,
      availability: "missing",
      repairCommand: "npm install --global bun@1.3.14 @oh-my-pi/pi-coding-agent@18.0.9",
    }],
  },
  {
    id: "codex",
    runtime: "installed-process",
    availability: "missing",
    detectedVersion: null,
    transports: ["exec-json"],
    authCategory: "harness-managed",
    billingOwner: "user-provider",
    strictWrapperConformant: false,
    testOnly: false,
  },
  {
    id: "claude",
    runtime: "installed-process",
    availability: "missing",
    detectedVersion: null,
    transports: ["print-stream-json"],
    authCategory: "harness-managed",
    billingOwner: "user-provider",
    strictWrapperConformant: false,
    testOnly: false,
  },
  {
    id: "mock",
    runtime: "deterministic-mock",
    availability: TEST_SEAMS_ENABLED ? "available" : "unavailable",
    detectedVersion: TEST_SEAMS_ENABLED ? "1.0.0" : null,
    transports: ["deterministic", "fake-process"],
    authCategory: "none-test-only",
    billingOwner: "test-fixture",
    strictWrapperConformant: TEST_SEAMS_ENABLED,
    testOnly: true,
    ...(TEST_SEAMS_ENABLED ? {} : { admissionBlock: "test-seams-disabled" as const }),
  },
];

export function harnessById(id: string): HarnessDescriptor | undefined {
  return harnesses.find((harness) => harness.id === id);
}
