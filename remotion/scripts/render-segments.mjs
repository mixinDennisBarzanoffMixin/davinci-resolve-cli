import fs from 'node:fs';
import crypto from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {getCompositions, renderMedia} from '@remotion/renderer';

const here = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(here, '..');
const manifestPath = path.resolve(process.argv[2] || '');
const outputDir = path.resolve(process.argv[3] || 'broll-renders');

if (!fs.existsSync(manifestPath)) {
  throw new Error(`Manifest does not exist: ${manifestPath}`);
}

const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
fs.mkdirSync(outputDir, {recursive: true});
const publicDir = path.join(path.dirname(manifestPath), 'remotion-assets');
const serveUrl = await bundle({
  entryPoint: path.join(projectRoot, 'src', 'index.ts'),
  ...(fs.existsSync(publicDir) ? {publicDir} : {}),
});

const rendered = [];
for (const placement of manifest.placements || []) {
  const durationInFrames = Math.max(1, Math.round(placement.duration_seconds * manifest.fps));
  if (placement.status !== 'ready-for-motion-graphic' && placement.status !== 'ready-with-approved-asset') {
    throw new Error(`Placement ${placement.id} is not approved for rendering: ${placement.status}`);
  }
  const inputProps = {
    placement,
    durationInFrames,
    fps: manifest.fps,
    width: manifest.width,
    height: manifest.height,
    accentColor: '#e51d2a',
  };
  const compositions = await getCompositions(serveUrl, {inputProps});
  const composition = compositions.find((row) => row.id === 'BrollSegment');
  if (!composition) {
    throw new Error('BrollSegment composition is not registered');
  }
  const safeId = String(placement.id || `segment-${rendered.length + 1}`).replace(/[^a-zA-Z0-9._-]/g, '-');
  const outputLocation = path.join(outputDir, `${safeId}.mp4`);
  await renderMedia({
    serveUrl,
    composition,
    codec: 'h264',
    outputLocation,
    inputProps,
  });
  rendered.push({...placement, output: outputLocation, durationInFrames});
}

const manifestSha256 = crypto.createHash('sha256').update(fs.readFileSync(manifestPath)).digest('hex');
const report = {success: true, manifest: manifestPath, manifestSha256, outputDir, rendered};
fs.writeFileSync(path.join(outputDir, 'render-manifest.json'), `${JSON.stringify(report, null, 2)}\n`);
process.stdout.write(`${JSON.stringify(report)}\n`);
