import oracleJson from "../../../shared/capabilities/adapters/oracle.v1.json" with { type: "json" };
import claudeJson from "../../../shared/capabilities/adapters/recipes/claude-print-stream-json.v1.json" with { type: "json" };
import codexJson from "../../../shared/capabilities/adapters/recipes/codex-exec-json.v1.json" with { type: "json" };
import ompJson from "../../../shared/capabilities/adapters/recipes/omp-rpc.v1.json" with { type: "json" };
import primeJson from "../../../shared/capabilities/adapters/recipes/prime-rpc.v1.json" with { type: "json" };
import { failure } from "../core/errors";
import type {
  CredentialRequirement,
  InstalledAdapterDefinition,
  InstalledAdapterId,
  InstalledAdapterRecipe,
  RuntimePrerequisiteRequirement,
} from "./types";

const recipeValues = [codexJson, claudeJson, primeJson, ompJson] as unknown as InstalledAdapterRecipe[];
const recipeDigests: Record<InstalledAdapterId, string> = {
  "codex/exec-json": "41c1fd72796defe256a338f92f5ebf850e6522d2eb265c8746512a40a2a03f82",
  "claude/print-stream-json": "491790b0857f9db771c4cb72ad3f852586d41bb3b8362d2467b0272c212e4afd",
  "prime/rpc": "d961da049ec9ea1b2d3d1c40fd4569cb41713850130841d88fd566d1e970f9b9",
  "omp/rpc": "716ac64704d38a2df4fcd16d50be2e04419930a566780109ae6eacfc607c58b5",
};
const oracle = oracleJson as unknown as {
  baseEnvironmentAllowlist: string[];
  environmentRules: {
    alwaysStrip: string[];
    adapterOwnedControls: Partial<Record<InstalledAdapterId, Record<string, string>>>;
  };
  adapters: Array<{
    adapterId: InstalledAdapterId;
    credentialGroups: Record<string, string[]>;
    credentialRequirements: Record<string, CredentialRequirement>;
    strictAdmission: { status: string };
  }>;
};

const definitions = new Map<InstalledAdapterId, InstalledAdapterDefinition>();
for (const recipe of recipeValues) {
  const admission = oracle.adapters.find((item) => item.adapterId === recipe.adapterId);
  if (admission === undefined || admission.strictAdmission.status !== "blocked") {
    throw new Error(`Installed adapter oracle drift for ${recipe.adapterId}`);
  }
  if (recipe.state !== "frozen" || recipe.admissionClaims.length !== 0) {
    throw new Error(`Unadmitted installed adapter recipe changed state for ${recipe.adapterId}`);
  }
  const runtimePrerequisites = parseRuntimePrerequisites(recipe);
  definitions.set(recipe.adapterId, {
    id: recipe.adapterId,
    recipe,
    recipeSha256: recipeDigests[recipe.adapterId],
    runtimePrerequisites,
    credentialGroups: admission.credentialGroups,
    credentialRequirements: admission.credentialRequirements,
    strictAdmission: "blocked",
    billingOwner: "user-provider",
    authCategory: "harness-managed",
  });
}

function parseRuntimePrerequisites(
  recipe: InstalledAdapterRecipe,
): readonly RuntimePrerequisiteRequirement[] {
  const values = recipe.support.runtimePrerequisites ?? [];
  if (recipe.adapterId !== "omp/rpc") {
    if (values.length !== 0) throw new Error(`Unexpected runtime prerequisite for ${recipe.adapterId}`);
    return Object.freeze([]);
  }
  if (values.length !== 1) throw new Error("OMP must declare exactly one runtime prerequisite");
  const value = values[0];
  if (
    value?.runtime !== "bun"
    || value.versionRange !== ">=1.3.14"
    || value.repairCommand !== "npm install --global bun@1.3.14 @oh-my-pi/pi-coding-agent@18.0.9"
  ) {
    throw new Error("OMP runtime prerequisite drifted from the admitted contract");
  }
  return Object.freeze([{ ...value }]);
}

export const installedAdapterIds = Object.freeze([...definitions.keys()]);
export const adapterBaseEnvironmentAllowlist = Object.freeze([...oracle.baseEnvironmentAllowlist]);
export const adapterAlwaysStrip = Object.freeze([...oracle.environmentRules.alwaysStrip]);

export function adapterOwnedEnvironmentControls(id: InstalledAdapterId): Readonly<Record<string, string>> {
  return Object.freeze({ ...(oracle.environmentRules.adapterOwnedControls[id] ?? {}) });
}

export function installedAdapterDefinition(id: string): InstalledAdapterDefinition {
  const definition = definitions.get(id as InstalledAdapterId);
  if (definition === undefined) {
    throw failure("TRANSPORT_UNSUPPORTED", { adapterId: id, fallbackAttempted: false });
  }
  return definition;
}
