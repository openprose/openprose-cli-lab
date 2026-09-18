/** Synthetic local protocol fixture, not an assessment of OpenProse semantics. */
import { readFileSync, writeFileSync, appendFileSync } from 'node:fs';
let text = ''; for await (const chunk of process.stdin) text += chunk;
const input = JSON.parse(text), files = JSON.parse(input.evidence.payload).files;
const value = name => files.find(file => file.path.endsWith('/' + name)).content;
if (process.argv[2] === 'assess') {
  appendFileSync('calls.log', 'assess\n');
  console.log(JSON.stringify({ judgment: value('source.txt') === value('report.txt') ? 'satisfied' : 'work-needed' }));
} else {
  appendFileSync('calls.log', 'act\n');
  writeFileSync('report.txt', readFileSync('source.txt'));
  if (process.argv[2] === 'fail') process.exitCode = 1;
}
