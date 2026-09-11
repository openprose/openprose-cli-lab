import { chmod, lstat, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { canonicalJson } from "../core/image";
import type { RunnerInvocation, VerifiedRuntimeImage } from "../core/types";

export const OMP_CONTROL_OVERLAY_BYTES = new TextEncoder().encode(
  "retry:\n"
  + "  enabled: false\n"
  + "disabledProviders:\n"
  + "  - native\n"
  + "  - omp-plugins\n"
  + "  - claude\n"
  + "  - agent-plugins\n"
  + "  - claude-plugins\n"
  + "  - codex\n"
  + "  - gemini\n"
  + "  - opencode\n"
  + "  - cursor\n"
  + "  - windsurf\n"
  + "  - vscode\n"
  + "  - mcp-json\n",
);

export interface PrivateTransportFiles {
  directory: string;
  imagePath: string;
  taskPath: string;
  observationPath: string;
  descendantPidPath: string;
  daemonSocketPath: string;
  imageBytes: Uint8Array;
  taskBytes: Uint8Array;
  cleanup(): Promise<void>;
}

export class PrivateTransportCleanupError extends Error {
  constructor() {
    super("Private transport cleanup could not be verified.");
    this.name = "PrivateTransportCleanupError";
  }
}

export interface PrivateTransportFileTestHooks {
  afterDirectoryCreated?(directory: string): void | Promise<void>;
  beforeCleanup?(): void | Promise<void>;
}

export async function createPrivateTransportFiles(
  image: VerifiedRuntimeImage,
  invocation: RunnerInvocation,
  temporaryRoot = tmpdir(),
  directoryPrefix = "openprose-transport-",
  testHooks?: PrivateTransportFileTestHooks,
): Promise<PrivateTransportFiles> {
  // Encode all caller-controlled values before acquiring the private root so
  // a validation/serialization failure cannot strand an owned directory.
  const imageBytes = encodeRuntimeImage(image);
  const taskBytes = new TextEncoder().encode(canonicalJson(invocation.task));
  const directory = await mkdtemp(join(temporaryRoot, directoryPrefix));
  const imagePath = join(directory, "runtime-image.bin");
  const taskPath = join(directory, "task.json");
  const observationPath = join(directory, "observation.json");
  const descendantPidPath = join(directory, "descendants.json");
  const daemonSocketPath = join(directory, "prime.sock");
  try {
    await testHooks?.afterDirectoryCreated?.(directory);
    await chmod(directory, 0o700);
    await writePrivateFile(imagePath, imageBytes);
    await writePrivateFile(taskPath, taskBytes);
  } catch (caught) {
    try {
      await testHooks?.beforeCleanup?.();
      await removeAndVerifyPrivateRoot(directory);
    } catch {
      throw new PrivateTransportCleanupError();
    }
    throw caught;
  }
  return {
    directory,
    imagePath,
    taskPath,
    observationPath,
    descendantPidPath,
    daemonSocketPath,
    imageBytes,
    taskBytes,
    cleanup: async () => removeAndVerifyPrivateRoot(directory),
  };
}

export function encodeRuntimeImage(image: VerifiedRuntimeImage): Uint8Array {
  const chunks = image.manifest.payload.map((entry) => image.files.get(entry.path)!);
  const size = chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

/** Create a private owned subdirectory whose lifetime is bound to the transport files. */
export async function createPrivateTransportDirectory(
  files: Pick<PrivateTransportFiles, "directory">,
  name: string,
): Promise<string> {
  const directory = join(files.directory, name);
  await mkdir(directory, { mode: 0o700 });
  await chmod(directory, 0o700);
  return directory;
}

/** Writes the exact OMP retry and ambient-provider overlay under the run guard. */
export async function createOmpControlOverlay(
  files: Pick<PrivateTransportFiles, "directory">,
): Promise<string> {
  const path = join(files.directory, "omp-control-overlay.yml");
  await writePrivateFile(path, OMP_CONTROL_OVERLAY_BYTES);
  return path;
}

async function writePrivateFile(path: string, bytes: Uint8Array): Promise<void> {
  await writeFile(path, bytes, { mode: 0o600, flag: "wx" });
  await chmod(path, 0o600);
}

async function removeAndVerifyPrivateRoot(directory: string): Promise<void> {
  await rm(directory, { recursive: true, force: true });
  const remains = await lstat(directory).then(
    () => true,
    (caught: NodeJS.ErrnoException) => {
      if (caught.code === "ENOENT") return false;
      throw caught;
    },
  );
  if (remains) throw new PrivateTransportCleanupError();
}
