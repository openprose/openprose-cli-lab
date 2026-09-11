import { afterEach, describe, expect, test } from "bun:test";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { parseEmbeddedImageBundle } from "../src/assets/sentinel";
import { verifyRuntimeImage } from "../src/core/image";

const bunRoot = resolve(import.meta.dir, "..");
const repositoryCliRoot = resolve(bunRoot, "..");
const generator = resolve(repositoryCliRoot, "shared/image/bundle/image_bundle.py");
const currentBundle = resolve(repositoryCliRoot, "shared/image/embedded/current.bundle.bin");
const buildScript = resolve(bunRoot, "scripts/image-bundle.ts");
const fakeHarness = resolve(repositoryCliRoot, "conformance/fake-harness/fake_harness.py");
const sentinelContracts = resolve(repositoryCliRoot, "shared/image/sentinel-v1/contracts");
const syntheticAggregate = "04a8fd447aea030a8839e17957ea04f2eb5d0216630b439a4b9fb0e8d36dab33";
const syntheticModelVisible = "ec42270e03e7163904e5a02d46351e1e543a3cac0ddcd42ecc1885c1561be3d6";
const roots: string[] = [];

afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

function digest(value: Uint8Array | string): string {
  return createHash("sha256").update(value).digest("hex");
}

function replaceEvery(value: Uint8Array, before: Uint8Array, after: Uint8Array): { bytes: Uint8Array; count: number } {
  if (before.byteLength !== after.byteLength) throw new Error("test replacement must preserve bundle offsets");
  const bytes = value.slice();
  let count = 0;
  for (let offset = 0; offset <= bytes.byteLength - before.byteLength; offset += 1) {
    if (before.every((byte, index) => bytes[offset + index] === byte)) {
      bytes.set(after, offset);
      count += 1;
    }
  }
  return { bytes, count };
}

async function put(root: string, path: string, value: Uint8Array | string): Promise<void> {
  const target = join(root, path);
  await mkdir(dirname(target), { recursive: true });
  await writeFile(target, value);
}

async function syntheticImage(root: string) {
  const payloads: Array<[string, Uint8Array]> = [
    ["payload/first.md", Buffer.from("first\n")],
    ["payload/nested/second.md", Buffer.from("second snowman ☃\n")],
    ["payload/third.txt", Buffer.from("third\n")],
    ["payload/fourth.prose", Buffer.from("fourth\n")],
  ];
  const artifacts = [
    ["contracts/renamed-task.json", await readFile(join(sentinelContracts, "task-envelope.schema.json"))],
    ["contracts/renamed-frame.txt", await readFile(join(sentinelContracts, "one-field-framing.txt"))],
    ["contracts/renamed-terminal.json", await readFile(join(sentinelContracts, "terminal-envelope.schema.json"))],
  ] as const;
  for (const [path, bytes] of [...payloads, ...artifacts]) await put(root, path, bytes);

  const aggregate = createHash("sha256");
  for (const [path, bytes] of payloads) {
    aggregate.update(path);
    aggregate.update(Uint8Array.of(0));
    aggregate.update(String(bytes.byteLength));
    aggregate.update(Uint8Array.of(0));
    aggregate.update(bytes);
    aggregate.update(Uint8Array.of(0));
  }
  const modelVisible = Buffer.concat(payloads.map(([, bytes]) => bytes));
  const manifest = {
    schema: "openprose.skill-runtime-image-manifest/1",
    imageFormatVersion: "openprose.skill-runtime-image/1",
    imageVersion: "synthetic-four-v1",
    languageVersion: "synthetic",
    skillVersion: "synthetic",
    runtimeContractVersion: "synthetic",
    semanticSourceRevision: "synthetic",
    purpose: "canonical-language-runtime",
    releaseEligible: false,
    normalization: { encoding: "utf-8", newlines: "lf", byteOrderMark: "forbidden", pathSeparator: "/" },
    payload: payloads.map(([path, bytes]) => ({
      path,
      mediaType: "text/markdown; charset=utf-8",
      byteLength: bytes.byteLength,
      sha256: digest(bytes),
    })),
    modelVisibleBytes: {
      serialization: "ordered-raw-concatenation-v1",
      byteLength: modelVisible.byteLength,
      sha256: digest(modelVisible),
    },
    aggregateSha256: { algorithm: "sha256-path-length-nul-v1", sha256: aggregate.digest("hex") },
    instructionPlacements: [{ id: "developer", strictness: "strict", preservesHarnessBasePrompt: true }],
    taskEnvelope: {
      schemaId: "openprose.task-envelope/1",
      path: artifacts[0][0],
      sha256: digest(artifacts[0][1]),
    },
    oneFieldFraming: {
      id: "openprose.one-field-framing/1",
      path: artifacts[1][0],
      sha256: digest(artifacts[1][1]),
    },
    terminalEnvelope: {
      schemaId: "openprose.sentinel-terminal-envelope/1",
      path: artifacts[2][0],
      sha256: digest(artifacts[2][1]),
    },
    minimumTransportRequirements: {
      nonInteractive: "required",
      structuredOutput: "required",
      ambientIsolation: "required",
      terminalEnvelope: "required",
      boundedStreaming: "required",
    },
  };
  await put(root, "manifest.json", `${JSON.stringify(manifest, null, 2)}\n`);
  return { manifest };
}

async function run(argv: string[], cwd: string, env: Record<string, string | undefined> = {}) {
  const child = Bun.spawn(argv, {
    cwd,
    env: {
      PATH: process.env.PATH,
      HOME: join(tmpdir(), "openprose-image-bundle-test-home"),
      XDG_CONFIG_HOME: join(tmpdir(), "openprose-image-bundle-test-config"),
      LANG: "C.UTF-8",
      ...env,
    },
    stdin: "ignore",
    stdout: "pipe",
    stderr: "pipe",
  });
  const [exitCode, stdout, stderr] = await Promise.all([
    child.exited,
    new Response(child.stdout).text(),
    new Response(child.stderr).text(),
  ]);
  return { exitCode, stdout, stderr };
}

describe("embedded image bundle", () => {
  test("parses the committed bundle and rejects trailing tamper", async () => {
    const bytes = await Bun.file(currentBundle).bytes();
    const image = await verifyRuntimeImage(parseEmbeddedImageBundle(bytes));
    expect(image.manifest).toMatchObject({
      imageVersion: "echo-v0",
      purpose: "functional-alpha-placeholder",
      releaseEligible: true,
    });
    expect(image.aggregateSha256).toBe("daf3fab11a27b6c982efdad0d223823b35bd7c8de05d9b1d464a30ed8ec146f2");
    expect(image.modelVisibleBytesSha256).toBe("5b10702a77d29104cc0f145b8971001e0f0d07e30de07debd09b3cba2ffb68ae");
    const tampered = new Uint8Array(bytes.byteLength + 1);
    tampered.set(bytes);
    tampered[tampered.byteLength - 1] = 1;
    expect(() => parseEmbeddedImageBundle(tampered)).toThrow("trailing bytes");

    const encoder = new TextEncoder();
    const unsafe = replaceEvery(
      bytes,
      encoder.encode("payload/00-echo-placeholder.md"),
      encoder.encode("payload/00 echo-placeholder.md"),
    );
    expect(unsafe.count).toBe(2);
    expect(() => parseEmbeddedImageBundle(unsafe.bytes)).toThrow("unsafe path");
  });

  test("builds an arbitrary four-payload image but refuses sentinel-only mock completion", async () => {
    const temporary = await mkdtemp(join(tmpdir(), "openprose-bun-image-bundle-"));
    roots.push(temporary);
    const imageRoot = join(temporary, "image");
    const bundle = join(temporary, "custom.bundle.bin");
    const checksum = join(temporary, "custom.bundle.sha256");
    const executable = join(temporary, "prose-custom");
    const observation = join(temporary, "observation.json");
    await mkdir(imageRoot);
    const expected = await syntheticImage(imageRoot);
    expect(expected.manifest.aggregateSha256.sha256).toBe(syntheticAggregate);
    expect(expected.manifest.modelVisibleBytes.sha256).toBe(syntheticModelVisible);

    const generated = await run(["python3", generator, "build", imageRoot, bundle, "--checksum", checksum], temporary);
    expect(generated).toMatchObject({ exitCode: 0, stderr: "" });
    const parsed = await verifyRuntimeImage(parseEmbeddedImageBundle(await Bun.file(bundle).bytes()));
    expect(parsed.manifest.payload.map((entry) => entry.path)).toEqual(expected.manifest.payload.map((entry) => entry.path));
    expect(parsed.modelVisibleBytesSha256).toBe(expected.manifest.modelVisibleBytes.sha256);
    expect(parsed.files.get("contracts/renamed-frame.txt")).toEqual(
      new Uint8Array(await readFile(join(imageRoot, "contracts/renamed-frame.txt"))),
    );

    const built = await run([
      process.execPath, "--no-env-file", buildScript, "build",
      "--image-dir", imageRoot,
      "--bundle", bundle,
      "--checksum", checksum,
      "--outfile", executable,
      "--test-seams",
    ], bunRoot);
    expect(built).toMatchObject({ exitCode: 0, stderr: "" });

    const diagnosed = await run([
      executable, "--harness", "mock", "--output", "json", "cli", "doctor",
    ], temporary);
    expect(diagnosed.exitCode).toBe(10);
    expect(JSON.parse(diagnosed.stdout)).toMatchObject({
      ready: false,
      selectedHarnessVersion: null,
      problems: [{ code: "HARNESS_UNAVAILABLE", exitCode: 10 }],
      image: {
        version: "synthetic-four-v1",
        sha256: expected.manifest.aggregateSha256.sha256,
      },
    });

    const executed = await run([
      executable,
      "--harness", "mock",
      "--transport", "fake-process",
      "--output", "json",
      "run", "portable.prose.md",
    ], temporary, {
      OPENPROSE_CONFORMANCE_FAKE_HARNESS: fakeHarness,
      OPENPROSE_CONFORMANCE_FAKE_SCENARIO: "success",
      OPENPROSE_CONFORMANCE_FAKE_OBSERVATION: observation,
    });
    expect(executed.exitCode).toBe(10);
    expect(executed.stderr).toBe("");
    const result = JSON.parse(executed.stdout);
    expect(result).toMatchObject({
      schema: "openprose.runner-result/1",
      adapter: {
        id: "mock/unavailable",
        harnessVersion: null,
        descriptorDigestSha256: digest("mock/unavailable"),
      },
      negotiatedCapabilities: {
        promptPlacement: "unsupported",
        isolation: "unsupported",
        streaming: "unsupported",
        cancellation: "unsupported",
        terminal: "unsupported",
      },
      languageImage: { sha256: expected.manifest.aggregateSha256.sha256 },
      digests: { deliveredImageSha256: null },
      terminal: { transportCompleted: false, terminalEventObserved: false },
      semantic: { status: "unknown", terminalEnvelopeDigestSha256: null },
      runnerExitCode: 10,
      error: { code: "HARNESS_UNAVAILABLE", exitCode: 10 },
    });
    expect(await Bun.file(observation).exists()).toBeFalse();
  }, 30_000);
});
