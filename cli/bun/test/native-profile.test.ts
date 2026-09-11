import { test, expect } from "bun:test";
import { mkdtemp, mkdir, writeFile, rm, realpath } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import fixture from "../../shared/fixtures/adapters/native-profile.json";
import { parseEntrypoint } from "../src/core/args";
import { resolveConfiguration } from "../src/core/config";
import { nativeProfileArgv, nativeConfiguration, observeNativeInit } from "../src/adapters/native-profile";
import { buildInstalledAdapterEnvironment } from "../src/adapters/environment";
import { installedAdapterDefinition } from "../src/adapters/recipes";

test("shared profile has explicit availability and no implicit permission grants",()=>{
 expect(nativeProfileArgv({harness:"claude"})).toEqual([]);
 expect(nativeProfileArgv({harness:"claude",nativeProfile:"default"})).toEqual([]);
 const input={harness:"claude",nativeProfile:fixture.profile};
 expect(nativeProfileArgv(input)).toEqual(fixture.flags);
 expect(nativeProfileArgv({...input,nativeAddDirs:["/tmp/a b"],nativeAllowTools:["Agent","Bash(cat *)"]})).toEqual([...fixture.flags,"--add-dir","/tmp/a b","--allowedTools","Agent","--allowedTools","Bash(cat *)"]);
 for(const harness of fixture.rejectedHarnesses) expect(()=>nativeProfileArgv({...input,harness})).toThrow();
 for(const nativeProfile of fixture.rejectedProfiles) expect(()=>nativeProfileArgv({...input,nativeProfile})).toThrow();
 expect(()=>nativeProfileArgv({harness:"claude",nativeAllowTools:["Agent"]})).toThrow();
 expect(()=>nativeProfileArgv({...input,nativeAllowTools:[" "]})).toThrow();
 expect(()=>nativeProfileArgv({...input,nativeAllowTools:["--dangerously-skip-permissions"]})).toThrow();
 expect(nativeConfiguration(input)?.observed).toBeNull();
 expect(nativeConfiguration({harness:"claude"})).toBeUndefined();
});

test("flags preserve repeats and config arrays use normal precedence",async()=>{
 const root=await mkdtemp(join(tmpdir(),"native-profile-"));
 try {
 await mkdir(join(root,"a b"));
 const config=join(root,"user.toml");
 await writeFile(config,'harness="claude"\nnative_profile="claude-workspace-tools"\nnative_add_dirs=["a b"]\nnative_allow_tools=[\'Read\', "Bash(cat *)",]\n');
 const parsed=parseEntrypoint(["--native-allow-tool","Agent","--native-allow-tool","Read","run","task"]);
 const result=await resolveConfiguration(parsed.global,{processCwd:root,env:{},userConfigPath:config});
 expect(result.values.nativeAllowTools).toEqual(["Agent","Read"]);
 expect(result.values.nativeAddDirs).toEqual([await realpath(join(root,"a b"))]);
 expect(result.values.nativeProfile).toBe(fixture.profile);
 await expect(resolveConfiguration({...parsed.global,nativeProfile:"default"},{processCwd:root,env:{},userConfigPath:config})).rejects.toThrow();
 await expect(resolveConfiguration({harness:"claude",nativeProfile:fixture.profile,nativeAddDirs:["missing"]},{processCwd:root,env:{},userConfigPath:join(root,"absent")})).rejects.toThrow();
 await expect(resolveConfiguration({harness:"codex",nativeProfile:fixture.profile},{processCwd:root,env:{},userConfigPath:join(root,"absent")})).rejects.toThrow();
 } finally {await rm(root,{recursive:true,force:true});}
});

test("native init verifies explicit credential source without inferring tool availability",()=>{
 const record={type:"system",subtype:"init",tools:["Read","Task"],apiKeySource:fixture.acceptedAuthSource};
 expect(observeNativeInit(record,"anthropic-api-key")).toEqual({tools:["Read","Task"],apiKeySource:fixture.acceptedAuthSource});
 expect(observeNativeInit({type:"result"},"anthropic-api-key")).toBeNull();
 expect(()=>observeNativeInit({...record,apiKeySource:"subscription"},"anthropic-api-key")).toThrow();
 expect(()=>observeNativeInit({...record,apiKeySource:undefined},"anthropic-api-key")).toThrow();
 expect(observeNativeInit({...record,apiKeySource:"subscription"},"claude-subscription")?.apiKeySource).toBe("subscription");
});

test("explicit API workspace config strips competing credentials and inherited SIMPLE",()=>{
 const environment=buildInstalledAdapterEnvironment({definition:installedAdapterDefinition("claude/print-stream-json"),ambient:{ANTHROPIC_API_KEY:"synthetic",CLAUDE_CODE_OAUTH_TOKEN:"competitor",CLAUDE_CODE_SIMPLE:"1",CLAUDE_CONFIG_DIR:"/ambient"},credentialGroup:"anthropic-api-key",credentialConfigDirectory:"/private"});
 expect(environment.ANTHROPIC_API_KEY).toBe("synthetic");
 expect(environment.CLAUDE_CONFIG_DIR).toBe("/private");
 expect(environment.CLAUDE_CODE_SIMPLE).toBeUndefined();
 expect(environment.CLAUDE_CODE_OAUTH_TOKEN).toBeUndefined();
});

import { buildInstalledLaunchPlan } from "../src/adapters/plan";
import { verifyRuntimeImage, canonicalJson, sha256 } from "../src/core/image";
import { sentinelFixtureImage } from "./sentinel-fixture";
import { encodeRuntimeImage } from "../src/supervision/files";
import type { RunnerInvocation } from "../src/core/types";

test("default launches are byte-identical and workspace profile removes bare only explicitly",async()=>{
 const image=await verifyRuntimeImage(sentinelFixtureImage);
 const task={schema:image.manifest.taskEnvelope.schemaId,argv:["test"],interactionMode:"non-interactive" as const};
 const invocation:RunnerInvocation={schema:"openprose.runner-invocation/1",invocationId:"native-profile-fixture",cwd:"/tmp",languageImage:{formatVersion:image.manifest.imageFormatVersion,version:image.manifest.imageVersion,sha256:image.aggregateSha256},runner:{name:"bun",version:"test",commit:"test"},harness:"claude",transport:"print-stream-json",recursionToken:"fixture",task,taskDigestSha256:await sha256(canonicalJson(task))};
 const input={adapterId:"claude/print-stream-json" as const,executable:"/native/claude",invocation,imageBytes:encodeRuntimeImage(image),expectedImageByteLength:image.manifest.modelVisibleBytes.byteLength,expectedImageSha256:image.manifest.modelVisibleBytes.sha256,framingTemplateBytes:image.files.get(image.manifest.oneFieldFraming.path)!,imagePath:"/private/image",taskPath:"/private/task",credentialGroup:"anthropic-api-key",platform:"darwin" as const,arch:"arm64" as const};
 for(const credentialGroup of ["anthropic-api-key","claude-subscription"]) {
  const baseline=await buildInstalledLaunchPlan({...input,credentialGroup});
  expect(await buildInstalledLaunchPlan({...input,credentialGroup,nativeProfile:"default"})).toEqual(baseline);
  expect(baseline.argv.includes("--bare")).toBe(credentialGroup==="anthropic-api-key");
  const selected=await buildInstalledLaunchPlan({...input,credentialGroup,nativeProfile:fixture.profile});
  expect(selected.argv).not.toContain("--bare");
  expect(selected.argv).not.toContain("--allowedTools");
  expect(selected.argv).not.toContain("--dangerously-skip-permissions");
  expect(selected.argv).toContain("--safe-mode");
  expect(selected.argv.slice(-1)).toEqual(baseline.argv.slice(-1));
 }
});
