import embeddedBundlePath from "../../../shared/image/embedded/current.bundle.bin" with { type: "file" };
import type { RuntimeImageBundle, RuntimeImageManifest } from "../core/types";

const magic = new Uint8Array([
  ...new TextEncoder().encode("OPENPROSE-IMAGE-BUNDLE"),
  0,
  1,
]);
const maximumManifestBytes = 1024 * 1024;
const maximumEntryBytes = 16 * 1024 * 1024;
const maximumImageBytes = 64 * 1024 * 1024;
const maximumFiles = 4096;
const maximumPathBytes = 4096;
const utf8 = new TextDecoder("utf-8", { fatal: true });

class Reader {
  #offset = 0;

  constructor(readonly bytes: Uint8Array) {}

  get offset(): number {
    return this.#offset;
  }

  take(length: number, field: string): Uint8Array {
    if (!Number.isSafeInteger(length) || length < 0 || this.#offset + length > this.bytes.byteLength) {
      throw new Error(`IMAGE_INVALID: embedded bundle is truncated at ${field}`);
    }
    const value = this.bytes.subarray(this.#offset, this.#offset + length);
    this.#offset += length;
    return value;
  }

  u32(field: string): number {
    const value = this.take(4, field);
    return (((value[0]! << 24) >>> 0) + (value[1]! << 16) + (value[2]! << 8) + value[3]!) >>> 0;
  }

  u64(field: string): number {
    const value = this.take(8, field);
    const high = (((value[0]! << 24) >>> 0) + (value[1]! << 16) + (value[2]! << 8) + value[3]!) >>> 0;
    const low = (((value[4]! << 24) >>> 0) + (value[5]! << 16) + (value[6]! << 8) + value[7]!) >>> 0;
    const result = high * 2 ** 32 + low;
    if (!Number.isSafeInteger(result)) throw new Error(`IMAGE_TOO_LARGE: ${field} exceeds the safe integer limit`);
    return result;
  }
}

export function parseEmbeddedImageBundle(bytes: Uint8Array): RuntimeImageBundle {
  const maximumBundleBytes = maximumImageBytes + magic.byteLength + 8 + maximumFiles * (12 + maximumPathBytes);
  if (bytes.byteLength > maximumBundleBytes) throw new Error("IMAGE_TOO_LARGE: embedded bundle exceeds aggregate limit");
  const reader = new Reader(bytes);
  if (!equalBytes(reader.take(magic.byteLength, "magic"), magic)) {
    throw new Error("IMAGE_INVALID: embedded bundle magic/version is unsupported");
  }
  const manifestLength = reader.u32("manifest length");
  if (manifestLength > maximumManifestBytes) throw new Error("IMAGE_TOO_LARGE: embedded manifest exceeds its limit");
  const manifestBytes = reader.take(manifestLength, "manifest bytes");
  const manifestText = normalizedText(manifestBytes, "manifest.json");
  let manifest: RuntimeImageManifest;
  try {
    manifest = JSON.parse(manifestText) as RuntimeImageManifest;
  } catch {
    throw new Error("IMAGE_INVALID: embedded manifest is not valid JSON");
  }
  const count = reader.u32("file count");
  if (count > maximumFiles) throw new Error("IMAGE_TOO_LARGE: embedded file count exceeds its limit");
  const files = new Map<string, Uint8Array>();
  let total = manifestBytes.byteLength;
  for (let index = 0; index < count; index += 1) {
    const pathLength = reader.u32(`file[${index}] path length`);
    if (pathLength === 0 || pathLength > maximumPathBytes) {
      throw new Error("IMAGE_INVALID: embedded path length is invalid");
    }
    const path = decodeUtf8(reader.take(pathLength, `file[${index}] path`), "bundle path");
    validatePath(path);
    if (files.has(path)) throw new Error(`IMAGE_INVALID: embedded bundle contains duplicate path ${path}`);
    const length = reader.u64(`file[${index}] length`);
    if (length > maximumEntryBytes) throw new Error("IMAGE_TOO_LARGE: embedded entry exceeds its limit");
    const body = reader.take(length, `file[${index}] bytes`);
    normalizedText(body, path);
    total += body.byteLength;
    if (total > maximumImageBytes) throw new Error("IMAGE_TOO_LARGE: embedded files exceed aggregate limit");
    files.set(path, body);
  }
  if (reader.offset !== bytes.byteLength) throw new Error("IMAGE_INVALID: embedded bundle contains trailing bytes");
  const expectedPaths = [
    ...manifest.payload.map((entry) => entry.path),
    manifest.taskEnvelope.path,
    manifest.oneFieldFraming.path,
    manifest.terminalEnvelope.path,
  ];
  const observedPaths = [...files.keys()];
  if (expectedPaths.length !== files.size || !expectedPaths.every((path, index) => observedPaths[index] === path)) {
    throw new Error("IMAGE_INVALID: embedded bundle order does not match manifest authority");
  }
  return { manifest, files };
}

function decodeUtf8(bytes: Uint8Array, path: string): string {
  try {
    return utf8.decode(bytes);
  } catch {
    throw new Error(`IMAGE_INVALID: ${path} is not UTF-8`);
  }
}

function normalizedText(bytes: Uint8Array, path: string): string {
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    throw new Error(`IMAGE_INVALID: ${path} contains a byte-order mark`);
  }
  if (bytes.includes(0)) throw new Error(`IMAGE_INVALID: ${path} contains NUL`);
  if (bytes.includes(13)) throw new Error(`IMAGE_INVALID: ${path} does not use LF newlines`);
  return decodeUtf8(bytes, path);
}

function validatePath(path: string): void {
  if (
    (!path.startsWith("payload/") && !path.startsWith("contracts/"))
    || path.includes("\\")
    || path.includes("\0")
    || !/^[A-Za-z0-9._/-]+$/u.test(path)
    || path.split("/").some((part) => part === "" || part === "." || part === "..")
  ) throw new Error(`IMAGE_INVALID: embedded bundle contains unsafe path ${path}`);
}

function equalBytes(left: Uint8Array, right: Uint8Array): boolean {
  return left.byteLength === right.byteLength && left.every((value, index) => value === right[index]);
}

const embeddedBundleBytes = await Bun.file(embeddedBundlePath).bytes();

export const embeddedRuntimeImage = parseEmbeddedImageBundle(embeddedBundleBytes);
/** @deprecated Test-era name retained for source compatibility. */
export const sentinelImage = embeddedRuntimeImage;
