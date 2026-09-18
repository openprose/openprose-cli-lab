/** Create a fresh synthetic offline example. Does not execute capabilities. */
import { mkdirSync, writeFileSync, readFileSync, realpathSync } from 'node:fs';
import { resolve, join, isAbsolute, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
const directory = dirname(fileURLToPath(import.meta.url));
const fixture = resolve(directory, '../local/fixture.mjs');
const runner = resolve(directory, '../local/run.mjs');
const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
export function createExample(destination) {
  if (typeof Bun === 'undefined') throw Error('Bun is required for this experimental walkthrough');
  if (typeof destination !== 'string' || !destination.trim() || !isAbsolute(destination)) throw Error('an absolute new directory is required');
  const target = resolve(destination);
  // Atomic exclusive claim; neither an existing directory nor a symlink is accepted.
  mkdirSync(target, {mode:0o700});
  const root = realpathSync(target);
  const write = (name, value) => writeFileSync(join(root,name),value,{flag:'wx',mode:0o600});
  const runtime = realpathSync(process.execPath);
  const configPath = join(root,'config.json');
  const config = {
    schema:1,root:'.',kernel:'kernel.md',contracts:['program.md'],evidence:['source.txt','report.txt'],
    capabilityVersion:'synthetic-offline-v1-'+createHash('sha256').update(readFileSync(fixture)).digest('hex'),
    assessor:[runtime,'--no-env-file',fixture,'assess'],actor:[runtime,'--no-env-file',fixture,'act'],
    environmentKeys:[],checkpointDirectory:'host',maxAttempts:3,ttlMs:60000,timeoutMs:2000,
  };
  write('kernel.md','# Synthetic fixture kernel\n\nThis file labels an offline protocol test. It is not the OpenProse kernel.\n');
  write('program.md','# Synthetic equality fixture\n\nThe fixture copies source.txt to report.txt and checks exact text equality. It does not interpret this Markdown.\n');
  write('source.txt','one\n');write('report.txt','');write('config.json',JSON.stringify(config,null,2)+'\n');
  const command = (...args) => [runtime,'--no-env-file',runner,...args];
  const commands = {check:command('check',configPath),status:command('status',configPath),step:command('step',configPath),serve:command('serve',configPath,'--poll-ms','250','--max-steps','3')};
  write('README.md',`# Offline synthetic example\n\nNo login, provider key or network is used. This fixture demonstrates persisted loop mechanics, not OpenProse semantic assessment. The selected executable and source checkout remain required.\n\nRun offline check, then status, step, step again, and bounded serve:\n\n${['check','status','step','step','serve'].map(name=>'```sh\n'+commands[name].map(quote).join(' ')+'\n```').join('\n\n')}\n\nThe first step repairs report.txt; the second reuses fresh satisfaction. Editing source.txt causes reassessment. The cumulative budget is three actions, including the initial repair. Removing source.txt produces evidence-gap without invoking a capability. calls.log records fixture capability invocations. Never delete a checkpoint or lock to bypass an unresolved effect.\n`);
  return {schema:'openprose.weave-offline-example/1',kind:'synthetic-no-network',root,config:configPath,commands};
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    if(process.argv.length!==3) throw Error('usage: bun --no-env-file getting-started/create.mjs /absolute/new-directory');
    console.log(JSON.stringify(createExample(process.argv[2])));
  } catch(error) {console.error(error.code==='EEXIST'?'destination already exists; choose a new directory':error.message);process.exitCode=1;}
}
