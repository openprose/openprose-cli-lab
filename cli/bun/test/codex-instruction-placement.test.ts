import { expect, test } from "bun:test";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import fixture from "../../shared/fixtures/adapters/codex-instruction-placement.json";
import { buildInstalledLaunchPlan } from "../src/adapters/plan";
import { installedAdapterDefinition } from "../src/adapters/recipes";
import { canonicalJson, sha256 } from "../src/core/image";
import type { RunnerInvocation, TaskEnvelope } from "../src/core/types";

test("shared Codex placements preserve task boundaries and exact native configuration", async () => {
  const task: TaskEnvelope = { schema: "openprose.task-envelope/1", argv: fixture.task.argv, interactionMode: "non-interactive" };
  const taskBytes = canonicalJson(task);
  const imageBytes = new TextEncoder().encode(fixture.image);
  const invocation: RunnerInvocation = {
    schema: "openprose.runner-invocation/1", invocationId: "placement-fixture", cwd: "/tmp/subject",
    languageImage: { formatVersion: "openprose.skill-runtime-image/1", version: "fixture", sha256: "a".repeat(64) },
    runner: { name: "bun", version: "0.1.0", commit: "fixture" }, harness: "codex", transport: "exec-json",
    recursionToken: "fixture", task, taskDigestSha256: await sha256(taskBytes),
  };
  const framing = new TextEncoder().encode("<image sha256=\"{{IMAGE_SHA256}}\">{{IMAGE_BYTES}}</image><task sha256=\"{{TASK_SHA256}}\">{{TASK_JSON}}</task>");
  for (const mode of ["developer", "base"] as const) {
    const expected = fixture.placements.find(p => p.mode === mode)!;
    const definition = installedAdapterDefinition("codex/exec-json", mode);
    const plan = await buildInstalledLaunchPlan({
      adapterId: "codex/exec-json", executable: "/bin/codex", invocation, imageBytes,
      expectedImageByteLength: imageBytes.length, expectedImageSha256: await sha256(imageBytes),
      framingTemplateBytes: framing, imagePath: fixture.imagePath, taskPath: "/tmp/task.json",
      credentialGroup: "cached-chatgpt-login", permissionMode: "workspace-write", model: "fixture-model",
      platform: "darwin", arch: "arm64", codexInstructionPlacement: mode,
    });
    const configValue = expected.configValue === "image" ? fixture.image : fixture.imagePath;
    const configArg = expected.configKey + "=" + JSON.stringify(configValue);
    expect(plan.argv).toContain(configArg);
    expect(plan.argv.indexOf(configArg)).toBeGreaterThan(plan.argv.indexOf("exec"));
    expect(plan.argv[plan.argv.indexOf(configArg) - 1]).toBe("-c");
    expect(plan.argv).toContain("workspace-write");
    expect(JSON.parse(configArg.slice(configArg.indexOf("=") + 1))).toBe(configValue);
    expect(new TextDecoder().decode(plan.stdinBytes!)).toBe(taskBytes);
    expect(new TextDecoder().decode(plan.stdinBytes!)).not.toContain(fixture.image);
    expect(plan.renderedPayloadSha256).toBe(await sha256(taskBytes));
    expect(plan.imageSha256).toBe(await sha256(imageBytes));
    expect(definition.recipe.launch.instructionPlacement).toEqual({
      manifestPlacementId: expected.placementId, strictness: "strict", preservesHarnessBasePrompt: expected.preservesHarnessBasePrompt,
    });
    expect(definition.recipe.support.admittedVersions).toEqual(["0.149.0-alpha.4.1"]);
    const bytes = await readFile(new URL(`../../shared/capabilities/adapters/recipes/codex-exec-json-${mode}.v1.json`, import.meta.url));
    expect(definition.recipeSha256).toBe(createHash("sha256").update(bytes).digest("hex"));
  }
  expect(installedAdapterDefinition("codex/exec-json").recipe.launch.instructionPlacement.manifestPlacementId).toBe("user-prefix-framed");
});
