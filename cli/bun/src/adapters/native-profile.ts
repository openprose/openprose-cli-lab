import { realpath, stat } from "node:fs/promises";
import { resolve } from "node:path";
import { failure } from "../core/errors";

export const workspaceTools = ["Read", "Write", "Edit", "Glob", "Grep", "Agent", "Bash"];
interface Selection {
  nativeProfile?: string;
  nativeAddDirs?: string[];
  nativeAllowTools?: string[];
  harness?: string;
  adapterId?: string;
  authProfile?: string | null;
  permissionMode?: string | null;
}
export interface NativeObservation { tools: string[]; apiKeySource: string | null }

export function nativeProfileArgv(input: Selection): string[] {
  const profile=input.nativeProfile ?? "default";
  if (!["default","claude-workspace-tools"].includes(profile)) throw failure("CONFIG_INVALID",{reason:"Unknown native profile."});
  const dirs=input.nativeAddDirs ?? [], rules=input.nativeAllowTools ?? [];
  if (profile === "default") {
    if (dirs.length || rules.length) throw failure("CONFIG_INVALID",{reason:"Native directory and tool rules require claude-workspace-tools."});
    return [];
  }
  if (input.harness !== "claude" && input.adapterId !== "claude/print-stream-json") throw failure("CONFIG_INVALID",{reason:"Native workspace profile is supported only by Claude."});
  for (const value of [...dirs,...rules]) if (!value.trim() || value.includes("\0")) throw failure("CONFIG_INVALID",{reason:"Native directory and tool rules must be nonempty strings."});
  if(rules.some(rule=>rule.startsWith("-"))) throw failure("CONFIG_INVALID",{reason:"Native permission rules cannot start with a dash."});
  return ["--setting-sources","","--tools",workspaceTools.join(","),...dirs.flatMap(p=>["--add-dir",p]),...rules.flatMap(r=>["--allowedTools",r])];
}

export async function validateNativeConfiguration(input:Selection,cwd:string):Promise<void> {
  nativeProfileArgv(input);
  if (input.nativeAddDirs !== undefined) input.nativeAddDirs=await Promise.all(input.nativeAddDirs.map(async p=>{
    try {const path=await realpath(resolve(cwd,p));if(!(await stat(path)).isDirectory()) throw new Error();return path;}
    catch {throw failure("CONFIG_INVALID",{reason:"Native additional directory does not exist or is not a directory."});}
  }));
}

export function nativeConfiguration(input:Selection,observed:NativeObservation|null=null):Record<string,unknown>|undefined {
  if ((input.nativeProfile??"default")==="default") return undefined;
  return {profile:input.nativeProfile,toolsRequested:[...workspaceTools],additionalDirectories:input.nativeAddDirs??[],allowedToolRules:input.nativeAllowTools??[],permissionMode:input.permissionMode??null,authProfile:input.authProfile??null,configOwnership:input.authProfile==="anthropic-api-key"?"runner-private":"native-auth-store",observed};
}

export function observeNativeInit(value:unknown,credentialGroup:string):NativeObservation|null {
  if (value===null || typeof value!=="object") return null;
  const record=value as Record<string,unknown>;
  if(record.type!=="system" || record.subtype!=="init") return null;
  const apiKeySource=typeof record.apiKeySource==="string"?record.apiKeySource:null;
  if(credentialGroup==="anthropic-api-key" && apiKeySource!=="ANTHROPIC_API_KEY") throw failure("HARNESS_NEEDS_AUTH",{reason:"Native credential source did not confirm the selected API environment key.",fallbackAttempted:false});
  if(!Array.isArray(record.tools) || record.tools.some(v=>typeof v!=="string")) throw failure("PROTOCOL_MALFORMED",{reason:"Native tool inventory is missing or invalid."});
  return {tools:record.tools as string[],apiKeySource};
}
