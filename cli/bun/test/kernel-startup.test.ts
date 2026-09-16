import { expect, test } from "bun:test";
import fixture from "../../shared/fixtures/kernel-startup/release.json";
import { publishedKernel, type KernelGet } from "../src/core/kernel-startup";
const root = "https://pkg.prose.md/releases/fixture-1/core/";
const entry = "https://pkg.prose.md/kernel.md";
type Response = { status: number; location?: string; text: string };
function setup(change?: (responses: Record<string, Response>) => void) {
  const responses: Record<string, Response> = structuredClone(fixture.responses);
  change?.(responses);
  const calls: string[] = [];
  const get: KernelGet = async (url) => { calls.push(url); const r = responses[url]; if (!r) throw Error("Unexpected URL"); return { ...r, bytes: new TextEncoder().encode(r.text) }; };
  return { get, calls };
}
test("published startup verifies a stable release with task-independent instruction bytes", async () => {
  const { get, calls } = setup();
  const image = await publishedKernel(get);
  expect(image.modelVisibleBytesSha256).toBe(fixture.sha256);
  expect(new TextDecoder().decode(image.files.get("payload/kernel.md"))).toBe(fixture.kernel);
  expect(image.manifest.instructionPlacements.map(p=>p.id)).toEqual(["developer", "system-append"]);
  expect(calls).toEqual([entry, root+"descriptor.json", root+"inventory.json", root+"README.md"]);
});
const bad: Record<string, (r:Record<string,Response>)=>void> = {
  "cross-origin": r=>{r[entry]!.location="https://example.com/releases/fixture-1/core/README.md";},
  "unsafe path": r=>{r[entry]!.location="/releases/../../core/README.md";},
  "missing redirect": r=>{r[entry]!.status=200;},
  "wrong descriptor": r=>{r[root+"descriptor.json"]!.text=r[root+"descriptor.json"]!.text.replace('openprose/core','other/core');},
  "bad inventory": r=>{r[root+"inventory.json"]!.text+=' ';},
  "bad kernel": r=>{r[root+"README.md"]!.text+='tampered';},
  "oversize kernel": r=>{r[root+"README.md"]!.text='x'.repeat(32769);},
  "HTTP failure": r=>{r[root+"descriptor.json"]!.status=503;},
};
for (const [name, change] of Object.entries(bad)) test(`published startup rejects ${name} without fallback`, async()=>{
  const {get}=setup(change); await expect(publishedKernel(get)).rejects.toBeDefined();
});
test("published startup propagates cancellation without a model call",async()=>{
  const control=new AbortController();control.abort();
  await expect(publishedKernel(async (_url,_limit,signal)=>{if(signal.aborted)throw Error('aborted');throw Error('unexpected');},control.signal)).rejects.toBeDefined();
});
test("published startup rejects invalid UTF-8 even with matching release hashes", async()=>{
  const {get:original}=setup();
  const {sha256}=await import('../src/core/image');
  const bytes=new Uint8Array([0xff]);
  const inventory=new TextEncoder().encode(JSON.stringify({'README.md':{mode:'100644',sha256:await sha256(bytes)}}));
  const descriptor=JSON.parse(fixture.responses['https://pkg.prose.md/releases/fixture-1/core/descriptor.json'].text);
  descriptor.inventory_sha256=await sha256(inventory);
  const get:KernelGet=async(url,limit,signal)=>url.endsWith('/descriptor.json')?{status:200,bytes:new TextEncoder().encode(JSON.stringify(descriptor))}:url.endsWith('/inventory.json')?{status:200,bytes:inventory}:url.endsWith('/README.md')?{status:200,bytes}:original(url,limit,signal);
  await expect(publishedKernel(get)).rejects.toBeDefined();
});
