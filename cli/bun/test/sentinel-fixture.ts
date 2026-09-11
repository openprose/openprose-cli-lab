import manifestJson from "../../shared/image/sentinel-v1/manifest.json" with { type: "json" };
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import type { RuntimeImageBundle, RuntimeImageManifest } from "../src/core/types";

const root = resolve(import.meta.dir, "../../shared/image/sentinel-v1");
const bytes = async (path: string): Promise<Uint8Array> => new Uint8Array(await readFile(resolve(root, path)));

export const sentinelFixtureImage: RuntimeImageBundle = {
  manifest: manifestJson as RuntimeImageManifest,
  files: new Map([
    ["payload/00-sentinel.md", await bytes("payload/00-sentinel.md")],
    ["payload/10-byte-canary.md", await bytes("payload/10-byte-canary.md")],
    ["contracts/task-envelope.schema.json", await bytes("contracts/task-envelope.schema.json")],
    ["contracts/one-field-framing.txt", await bytes("contracts/one-field-framing.txt")],
    ["contracts/terminal-envelope.schema.json", await bytes("contracts/terminal-envelope.schema.json")],
  ]),
};
