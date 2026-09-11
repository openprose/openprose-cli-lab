import {expect, test} from "bun:test";
import {buildInstalledAdapterEnvironment} from "../src/adapters/environment";
import {installedAdapterDefinition} from "../src/adapters/recipes";
import {buildInstalledLaunchPlan} from "../src/adapters/plan";
import {canonicalJson, sha256} from "../src/core/image";
import type {RunnerInvocation} from "../src/core/types";
test("Claude API profile requires key and excludes competing credentials", () => {
 const definition=installedAdapterDefinition("claude/print-stream-json");
 expect(()=>buildInstalledAdapterEnvironment({definition,ambient:{},credentialGroup:"anthropic-api-key"})).toThrow();
 const ambient={ANTHROPIC_API_KEY:"fixture-key",ANTHROPIC_AUTH_TOKEN:"other",OPENAI_API_KEY:"other"};
 expect(buildInstalledAdapterEnvironment({definition,ambient,credentialGroup:"anthropic-api-key"})).toEqual({ANTHROPIC_API_KEY:"fixture-key"});
 expect(buildInstalledAdapterEnvironment({definition,ambient,credentialGroup:"claude-subscription"})).toEqual({});
});
test("only explicit API route uses bare mode without credential argv",async()=>{
 const task={schema:"fixture",argv:["run"],interactionMode:"non-interactive"};
 const invocation={task,taskDigestSha256:await sha256(canonicalJson(task)),cwd:"/tmp",invocationId:"fixture"} as RunnerInvocation;
 const imageBytes=new TextEncoder().encode("fixture image");
 for(const credentialGroup of ["anthropic-api-key","claude-subscription"]){
 const p=await buildInstalledLaunchPlan({adapterId:"claude/print-stream-json",executable:"/claude",invocation,imageBytes,expectedImageByteLength:imageBytes.length,expectedImageSha256:await sha256(imageBytes),framingTemplateBytes:new TextEncoder().encode("{{IMAGE_BYTES}} {{IMAGE_SHA256}} {{TASK_JSON}} {{TASK_SHA256}}"),imagePath:"/image",taskPath:"/task",credentialGroup,platform:"darwin",arch:"arm64"});
 expect(p.argv.includes("--bare")).toBe(credentialGroup==="anthropic-api-key");
 expect(p.argv.join(" ")).not.toContain("fixture-key");
 }
});

test("Codex API profile selects explicit env provider and workspace writes",async()=>{
 const task={schema:"fixture",argv:["run"],interactionMode:"non-interactive"};
 const invocation={task,taskDigestSha256:await sha256(canonicalJson(task)),cwd:"/tmp",invocationId:"fixture"} as RunnerInvocation;
 const imageBytes=new TextEncoder().encode("fixture image");
 const p=await buildInstalledLaunchPlan({adapterId:"codex/exec-json",executable:"/codex",invocation,imageBytes,expectedImageByteLength:imageBytes.length,expectedImageSha256:await sha256(imageBytes),framingTemplateBytes:new TextEncoder().encode("{{IMAGE_BYTES}} {{IMAGE_SHA256}} {{TASK_JSON}} {{TASK_SHA256}}"),imagePath:"/image",taskPath:"/task",credentialGroup:"openai-api-key",permissionMode:"workspace-write",platform:"darwin",arch:"arm64"});
 expect(p.argv.slice(0,4)).toEqual(["/codex","exec","--sandbox","workspace-write"]);
 expect(p.argv).toContain('model_provider="openai-env"');
 expect(p.argv).toContain('model_providers.openai-env.env_key="OPENAI_API_KEY"');
 expect(p.argv).toContain('model_providers.openai-env.requires_openai_auth=false');
});
