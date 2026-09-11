import {nativeLimitsArgv} from "./sdk-limits";
import { nativeProfileArgv } from "./native-profile";
import codexApiSettings from "../../../shared/capabilities/adapters/codex-env-route.v1.json";
import { failure } from "../core/errors";
import { canonicalJson, sha256 } from "../core/image";
import type { RunnerInvocation } from "../core/types";
import { installedAdapterDefinition } from "./recipes";
import type { InstalledAdapterId, InstalledLaunchPlan, RecipeValue } from "./types";
import { assertInstalledAdapterArgv, assertInstalledAdapterPlatform } from "./admission";

const decoder = new TextDecoder("utf-8", { fatal: true });
const encoder = new TextEncoder();
const inlineImageMaximumBytes = 128 * 1024;

export interface BuildInstalledLaunchPlanInput {
  adapterId: InstalledAdapterId;
  executable: string;
  invocation: RunnerInvocation;
  imageBytes: Uint8Array;
  expectedImageByteLength: number;
  expectedImageSha256: string;
  framingTemplateBytes: Uint8Array;
  imagePath: string;
  taskPath: string;
  credentialGroup: string;
  model?: string | null;
  nativeProfile?: string;
  nativeMaxTurns?: string;
  nativeTimeout?: string;
  nativeToolTimeout?: string;
  nativeAddDirs?: string[];
  nativeAllowTools?: string[];
  permissionMode?: string | null;
  renderedConfigPath?: string;
  daemonSocketPath?: string;
  platform?: NodeJS.Platform;
  arch?: NodeJS.Architecture;
}

export async function buildInstalledLaunchPlan(input: BuildInstalledLaunchPlanInput): Promise<InstalledLaunchPlan> {
  if(input.adapterId === "agents-sdk/jsonl" && !input.model) throw failure("CONFIG_INVALID",{reason:"Agents SDK requires an explicit model"});
  const definition = installedAdapterDefinition(input.adapterId);
  assertInstalledAdapterPlatform(input.adapterId, { platform: input.platform, arch: input.arch });
  if (definition.credentialGroups[input.credentialGroup] === undefined) {
    throw failure("CONFIG_INVALID", {
      adapterId: input.adapterId,
      reason: `Unknown or ambiguous credential group: ${input.credentialGroup}.`,
    });
  }
  const imageText = decodeImage(input.imageBytes);
  const imageSha256 = await sha256(input.imageBytes);
  if (input.imageBytes.byteLength !== input.expectedImageByteLength || imageSha256 !== input.expectedImageSha256) {
    throw failure("IMAGE_INVALID", { reason: "The model-visible image bytes changed after verification." });
  }
  const taskJson = canonicalJson(input.invocation.task);
  const taskSha256 = await sha256(taskJson);
  if (taskSha256 !== input.invocation.taskDigestSha256) {
    throw failure("INTERNAL_ERROR", { reason: "The model-facing task digest changed before adapter construction." });
  }
  const framingTemplate = decodeFramingTemplate(input.framingTemplateBytes);
  const framed = renderOneFieldFrame(framingTemplate, imageText, imageSha256, taskJson, taskSha256);
  const values: Record<RecipeValue, string | undefined> = {
    executable: input.executable,
    "image-path": input.imagePath,
    "image-utf8": imageText,
    "rendered-config-path": input.renderedConfigPath,
    "task-json": taskJson,
    "task-path": input.taskPath,
    cwd: input.invocation.cwd,
    model: input.model ?? undefined,
    "invocation-id": input.invocation.invocationId,
    "daemon-socket-path": input.daemonSocketPath,
  };
  const argv: string[] = [];
  const recipeArgv = [...definition.recipe.launch.argv];
  for (const group of definition.recipe.launch.optionalArgv) {
    if (group.when !== "model-present" || input.model === undefined || input.model === null) continue;
    const insertion = group.placement === "before-final-argument"
      ? recipeArgv.length - 1
      : group.placement === "before-final-pair"
        ? recipeArgv.length - 2
        : recipeArgv.length;
    recipeArgv.splice(insertion, 0, ...group.argv);
  }
  for (const token of recipeArgv) {
    if ("literal" in token) {
      argv.push(token.literal);
      continue;
    }
    const value = values[token.value];
    if (value === undefined) {
      throw failure("CONFIG_INVALID", { adapterId: input.adapterId, reason: `Recipe value is unavailable: ${token.value}.` });
    }
    if (token.value === "image-utf8") assertInlineImage(value);
    argv.push(value);
  }
  if (input.adapterId === "claude/print-stream-json" && input.credentialGroup === "anthropic-api-key" && (input.nativeProfile ?? "default") === "default") {
    argv.splice(1, 0, "--bare");
  }
  if(input.adapterId === "codex/exec-json" && input.credentialGroup === "openai-api-key") argv.splice(2,0,...codexApiSettings.flatMap(setting=>["-c",setting]));
  argv.splice(1,0,...nativeProfileArgv(input),...nativeLimitsArgv(input));
  if (input.permissionMode != null) {
    if(input.adapterId === "claude/print-stream-json" && ["default","acceptEdits"].includes(input.permissionMode)) argv.splice(1,0,"--permission-mode",input.permissionMode);
    else if(input.adapterId === "codex/exec-json" && ["workspace-write","read-only"].includes(input.permissionMode)) argv.splice(2,0,"--sandbox",input.permissionMode);
    else throw failure("CONFIG_INVALID", {reason:"Unsupported explicit permission mode for this harness."});
  }
  assertInstalledAdapterArgv(input.adapterId, argv, { platform: input.platform, arch: input.arch });
  let stdinBytes: Uint8Array | null = null;
  let renderedPayloadSha256: string | null = null;
  if (input.adapterId === "codex/exec-json") {
    stdinBytes = encoder.encode(framed);
    renderedPayloadSha256 = await sha256(stdinBytes);
  } else if (input.adapterId === "prime/rpc") {
    stdinBytes = rpcPrompt(input.invocation.invocationId, taskJson, true);
  } else if (input.adapterId === "omp/rpc") {
    stdinBytes = rpcPrompt(ompRpcId(input.invocation.invocationId, "omp.prompt.1"), taskJson, true);
  }

  return {
    adapterId: input.adapterId,
    executable: input.executable,
    argv,
    stdinBytes,
    stdinLifecycle: definition.recipe.launch.stdinLifecycle,
    imagePath: input.imagePath,
    taskPath: input.taskPath,
    daemonSocketPath: input.adapterId === "prime/rpc" ? input.daemonSocketPath ?? null : null,
    imageSha256,
    taskSha256,
    renderedPayloadSha256,
    credentialGroup: input.credentialGroup,
    shell: false,
    outerPty: false,
    interactionMode: "non-interactive",
    billingOwner: "user-provider",
    authCategory: "harness-managed",
    admissionStatus: "blocked",
  };
}

export function ompRpcId(invocationId: string, suffix: "omp.state.1" | "omp.prompt.1"): string {
  return `${invocationId}.${suffix}`;
}

export function renderOneFieldFrame(
  framingTemplate: string,
  imageText: string,
  imageSha256: string,
  taskJson: string,
  taskSha256: string,
): string {
  if (framingTemplate.includes("{{") && !framingTemplate.includes("{{IMAGE_SHA256}}")) {
    throw failure("IMAGE_INVALID", { reason: "The image-owned one-field framing template is unsupported." });
  }
  const rendered = framingTemplate
    .replace("{{IMAGE_SHA256}}", imageSha256)
    .replace("{{IMAGE_BYTES}}", imageText)
    .replace("{{TASK_SHA256}}", taskSha256)
    .replace("{{TASK_JSON}}", taskJson);
  if (rendered.includes("{{")) {
    throw failure("IMAGE_INVALID", { reason: "The image-owned one-field framing template has unresolved fields." });
  }
  return rendered;
}

function rpcPrompt(invocationId: string, message: string, canonical = false): Uint8Array {
  const record = { id: invocationId, type: "prompt", message };
  return encoder.encode(`${canonical ? canonicalJson(record) : JSON.stringify(record)}\n`);
}

function decodeImage(bytes: Uint8Array): string {
  let text: string;
  try {
    text = decoder.decode(bytes);
  } catch {
    throw failure("IMAGE_INVALID", { reason: "The verified model-visible image is not valid UTF-8." });
  }
  if (text.includes("\0")) {
    throw failure("IMAGE_INVALID", { reason: "The model-visible image contains a forbidden NUL byte." });
  }
  return text;
}

function decodeFramingTemplate(bytes: Uint8Array): string {
  let text: string;
  try {
    text = decoder.decode(bytes);
  } catch {
    throw failure("IMAGE_INVALID", { reason: "The image-owned one-field framing template is not valid UTF-8." });
  }
  if (text.includes("\0") || text.includes("\r")) {
    throw failure("IMAGE_INVALID", { reason: "The image-owned one-field framing template violates normalization." });
  }
  return text;
}

function assertInlineImage(text: string): void {
  const byteLength = encoder.encode(text).byteLength;
  if (byteLength > inlineImageMaximumBytes) {
    throw failure("IMAGE_TOO_LARGE", { byteLength, maximumBytes: inlineImageMaximumBytes, placement: "image-utf8" });
  }
}
