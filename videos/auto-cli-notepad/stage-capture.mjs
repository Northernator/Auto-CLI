// One-off provenance-preserving import from the recorded acceptance run.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const project = path.dirname(fileURLToPath(import.meta.url));
const source = path.resolve(project, '../../.test-artifacts/notepad-demo');
const capture = path.join(project, 'capture');
fs.mkdirSync(capture, { recursive: true });
for (const name of ['notepad-before.png', 'notepad-after.png', 'result.json']) {
  fs.copyFileSync(path.join(source, name), path.join(capture, name));
}
const transcript = JSON.parse(fs.readFileSync(path.join(source, 'transcript.json'), 'utf8'))
  .filter(step => step.step !== 'install');
const privateRoot = path.resolve(project, '../..');
const redact = value => {
  if (typeof value === 'string') {
    return value.replaceAll(privateRoot, '<repo>')
      .replaceAll(privateRoot.replaceAll('\\', '/'), '<repo>');
  }
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, redact(v)]));
  return value;
};
fs.writeFileSync(path.join(capture, 'commands.json'), JSON.stringify({
  source: 'Real Windows Notepad acceptance run',
  note: 'Absolute checkout paths replaced with <repo>; install logs excluded. Commands retain captured arguments and outputs.',
  steps: redact(transcript),
}, null, 2) + '\n');
console.log('Staged two unchanged Notepad captures and privacy-sanitized acceptance evidence.');
