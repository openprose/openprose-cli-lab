import { createHash } from "node:crypto";
import { failure } from "./errors";
import type { ExternalImageArtifact, RuntimeImageBundle, VerifiedRuntimeImage } from "./types";

const encoder = new TextEncoder();
const hexadecimalSha256 = /^[a-f0-9]{64}$/u;

export async function sha256(bytes: Uint8Array | string): Promise<string> {
  return createHash("sha256").update(bytes).digest("hex");
}

// RFC 8785 uses ECMAScript scalar serialization and UTF-16 code-unit key
// ordering. Runner records contain JSON values only, so this small canonicalizer
// is sufficient and keeps digest authority independent of object insertion.
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw failure("INTERNAL_ERROR", { reason: "Non-finite number cannot be canonicalized." });
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    const entries = Object.keys(record)
      .filter((key) => record[key] !== undefined)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`);
    return `{${entries.join(",")}}`;
  }
  throw failure("INTERNAL_ERROR", { reason: `Unsupported canonical JSON value type: ${typeof value}.` });
}

export async function verifyRuntimeImage(bundle: RuntimeImageBundle): Promise<VerifiedRuntimeImage> {
  const { manifest } = bundle;
  if (manifest.schema !== "openprose.skill-runtime-image-manifest/1" || manifest.imageFormatVersion !== "openprose.skill-runtime-image/1") {
    invalid("Unsupported Skill Runtime Image manifest schema or format version.");
  }
  if (
    manifest.normalization.encoding !== "utf-8"
    || manifest.normalization.newlines !== "lf"
    || manifest.normalization.byteOrderMark !== "forbidden"
    || manifest.normalization.pathSeparator !== "/"
  ) invalid("Unsupported Skill Runtime Image normalization profile.");
  if (
    manifest.purpose !== "canonical-language-runtime"
    && manifest.purpose !== "functional-alpha-placeholder"
    && manifest.purpose !== "sentinel-transport-test"
  ) invalid("Unsupported Skill Runtime Image purpose.");
  if (manifest.purpose === "sentinel-transport-test" && manifest.releaseEligible) {
    invalid("A sentinel Skill Runtime Image cannot be release eligible.");
  }
  if (!hexadecimalSha256.test(manifest.aggregateSha256.sha256)) invalid("Skill Runtime Image aggregate digest is malformed.");
  if (manifest.aggregateSha256.algorithm !== "sha256-path-length-nul-v1") {
    invalid("Unsupported Skill Runtime Image aggregate algorithm.");
  }
  if (manifest.payload.length === 0) invalid("Skill Runtime Image has no payload entries.");
  if (
    manifest.modelVisibleBytes.serialization !== "ordered-raw-concatenation-v1"
    || !Number.isSafeInteger(manifest.modelVisibleBytes.byteLength)
    || manifest.modelVisibleBytes.byteLength <= 0
    || !hexadecimalSha256.test(manifest.modelVisibleBytes.sha256)
  ) invalid("Skill Runtime Image model-visible serialization is malformed.");
  if (manifest.instructionPlacements.length === 0) invalid("Skill Runtime Image declares no instruction placement.");

  const aggregate = createHash("sha256");
  const modelVisible = createHash("sha256");
  let modelVisibleByteLength = 0;
  const seen = new Set<string>();
  for (const descriptor of manifest.payload) {
    if (seen.has(descriptor.path)) invalid(`Duplicate Skill Runtime Image entry: ${descriptor.path}.`);
    seen.add(descriptor.path);
    if (!safeRelativePath(descriptor.path, "payload/")) {
      invalid(`Unsafe Skill Runtime Image entry path: ${descriptor.path}.`);
    }
    const bytes = bundle.files.get(descriptor.path);
    if (bytes === undefined) invalid(`Missing Skill Runtime Image entry: ${descriptor.path}.`);
    const actual = await sha256(bytes);
    if (actual !== descriptor.sha256) invalid(`Skill Runtime Image entry digest mismatch: ${descriptor.path}.`);
    if (bytes.byteLength !== descriptor.byteLength) invalid(`Skill Runtime Image entry length mismatch: ${descriptor.path}.`);
    verifyNormalizedText(bytes, descriptor.path);
    modelVisible.update(bytes);
    modelVisibleByteLength += bytes.byteLength;
    aggregate.update(encoder.encode(descriptor.path));
    aggregate.update(Uint8Array.of(0));
    aggregate.update(encoder.encode(String(bytes.byteLength)));
    aggregate.update(Uint8Array.of(0));
    aggregate.update(bytes);
    aggregate.update(Uint8Array.of(0));
  }
  await verifyArtifact(bundle, manifest.taskEnvelope);
  await verifyArtifact(bundle, manifest.oneFieldFraming);
  await verifyArtifact(bundle, manifest.terminalEnvelope);
  const allowed = new Set([...seen, manifest.taskEnvelope.path, manifest.oneFieldFraming.path, manifest.terminalEnvelope.path]);
  if (bundle.files.size !== allowed.size || [...bundle.files.keys()].some((path) => !allowed.has(path))) {
    invalid("Skill Runtime Image contains an undeclared file.");
  }
  const aggregateSha256 = aggregate.digest("hex");
  if (aggregateSha256 !== manifest.aggregateSha256.sha256) invalid("Skill Runtime Image aggregate digest mismatch.");
  const modelVisibleBytesSha256 = modelVisible.digest("hex");
  if (
    modelVisibleByteLength !== manifest.modelVisibleBytes.byteLength
    || modelVisibleBytesSha256 !== manifest.modelVisibleBytes.sha256
  ) invalid("Skill Runtime Image model-visible serialization digest mismatch.");
  return { manifest, files: bundle.files, aggregateSha256, modelVisibleBytesSha256 };
}

async function verifyArtifact(bundle: RuntimeImageBundle, artifact: ExternalImageArtifact): Promise<void> {
  if (!safeRelativePath(artifact.path, "contracts/")) invalid(`Unsafe Skill Runtime Image contract path: ${artifact.path}.`);
  const bytes = bundle.files.get(artifact.path);
  if (bytes === undefined) invalid(`Missing Skill Runtime Image contract artifact: ${artifact.path}.`);
  if (await sha256(bytes) !== artifact.sha256) invalid(`Skill Runtime Image contract digest mismatch: ${artifact.path}.`);
}

function safeRelativePath(path: string, prefix: string): boolean {
  return path.startsWith(prefix)
    && !path.includes("\\")
    && !path.split("/").some((segment) => segment === "" || segment === "." || segment === "..");
}

function verifyNormalizedText(bytes: Uint8Array, path: string): void {
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    invalid(`Skill Runtime Image payload has a forbidden byte-order mark: ${path}.`);
  }
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    invalid(`Skill Runtime Image payload is not valid UTF-8: ${path}.`);
  }
  if (text.includes("\r")) invalid(`Skill Runtime Image payload does not use LF-only newlines: ${path}.`);
}

function invalid(message: string): never {
  throw failure("IMAGE_INVALID", { reason: message });
}
