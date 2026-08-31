import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {getCompositions, renderMedia} from '@remotion/renderer';

const here = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(here, '..');
const manifestPath = path.resolve(process.argv[2] || '');
const outputLocation = path.resolve(process.argv[3] || 'captions-overlay.mov');

if (!fs.existsSync(manifestPath)) {
  throw new Error(`Manifest does not exist: ${manifestPath}`);
}
const inputProps = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
if (!inputProps.captionReviewApproved) {
  throw new Error('Caption overlay rendering requires an audio-verified reviewed transcript');
}
const serveUrl = await bundle({entryPoint: path.join(projectRoot, 'src', 'index.ts')});
const compositions = await getCompositions(serveUrl, {inputProps});
const composition = compositions.find((row) => row.id === 'CaptionOverlay');
if (!composition) {
  throw new Error('CaptionOverlay composition is not registered');
}
fs.mkdirSync(path.dirname(outputLocation), {recursive: true});
await renderMedia({
  serveUrl,
  composition,
  codec: 'prores',
  proResProfile: '4444',
  pixelFormat: 'yuva444p10le',
  outputLocation,
  inputProps,
});
process.stdout.write(`${JSON.stringify({success: true, output: outputLocation})}\n`);
