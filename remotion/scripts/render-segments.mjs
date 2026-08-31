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
const publicDir = path.join(path.dirname(manifestPath), 'remotion-assets');
const graphicStatuses = new Set(['ready-for-motion-graphic']);
const assetStatuses = new Set([
  'ready-with-approved-asset',
  'ready-with-reviewed-source',
  'ready-with-source-cutaway',
  'ready-for-source-cutaway',
  'ready-with-generated-asset',
]);
const imageExtensions = new Set(['.jpg', '.jpeg', '.png', '.webp', '.avif']);
const videoExtensions = new Set(['.mp4', '.mov', '.m4v', '.webm']);

const placementDurationSeconds = (placement) => {
  const value = placement.duration_seconds
    ?? placement.duration_sec
    ?? (placement.end_seconds === undefined
      ? undefined
      : Number(placement.end_seconds) - Number(placement.start_seconds));
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) {
    throw new Error(`Placement ${placement.id} has an invalid duration`);
  }
  return seconds;
};

const sceneKind = (placement) => {
  if (placement.treatment?.kind) {
    return placement.treatment.kind === 'generated_image'
      ? 'generated_illustration'
      : placement.treatment.kind;
  }
  if (placement.asset?.origin === 'project_source' || placement.asset?.origin === 'source_cutaway') {
    return 'source_cutaway';
  }
  if (placement.asset?.origin === 'generated' || placement.asset?.origin === 'ai_generated') {
    return 'generated_illustration';
  }
  if (placement.visual_type === 'generated_image') {
    return 'generated_illustration';
  }
  if (placement.visual_type === 'source_cutaway') {
    return 'source_cutaway';
  }
  if (placement.asset?.kind === 'video') {
    return 'source_cutaway';
  }
  if (placement.asset?.kind === 'image') {
    return 'evidence_image';
  }
  return placement.visual_type === 'diagram' ? 'diagram' : 'motion_graphic';
};

const checkedLocalAsset = (placement) => {
  const asset = placement.asset;
  if (!asset?.src || typeof asset.src !== 'string') {
    throw new Error(`Placement ${placement.id} requires a local asset`);
  }
  if (/^(?:https?:|data:|file:)/i.test(asset.src) || path.isAbsolute(asset.src)) {
    throw new Error(`Placement ${placement.id} asset must be a relative local path in remotion-assets`);
  }
  const normalized = asset.src.replaceAll('\\', '/');
  const localPath = path.resolve(publicDir, normalized);
  const relative = path.relative(publicDir, localPath);
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`Placement ${placement.id} asset escapes remotion-assets`);
  }
  if (!fs.existsSync(localPath) || !fs.statSync(localPath).isFile()) {
    throw new Error(`Placement ${placement.id} local asset does not exist: ${asset.src}`);
  }
  const extension = path.extname(localPath).toLowerCase();
  if (asset.kind === 'video' && !videoExtensions.has(extension)) {
    throw new Error(`Placement ${placement.id} video asset has unsupported extension: ${extension}`);
  }
  if (asset.kind !== 'video' && !imageExtensions.has(extension)) {
    throw new Error(`Placement ${placement.id} image asset has unsupported extension: ${extension}`);
  }
};

const validatePlacement = (placement) => {
  placementDurationSeconds(placement);
  const kind = sceneKind(placement);
  const hasAsset = Boolean(placement.asset?.src);
  const isGraphic = kind === 'motion_graphic' || kind === 'diagram';
  if (isGraphic && !hasAsset) {
    if (!graphicStatuses.has(placement.status)) {
      throw new Error(`Placement ${placement.id} is not approved for graphic rendering: ${placement.status}`);
    }
    return;
  }
  if (!assetStatuses.has(placement.status)) {
    throw new Error(`Placement ${placement.id} is not approved for asset rendering: ${placement.status}`);
  }
  checkedLocalAsset(placement);
  if (kind === 'source_cutaway' && placement.asset.kind !== 'video') {
    throw new Error(`Placement ${placement.id} source cutaway must use a video asset`);
  }
  if (kind === 'generated_illustration') {
    if (placement.asset.kind !== 'image') {
      throw new Error(`Placement ${placement.id} generated illustration must use an image asset`);
    }
    if (placement.asset.exact_item === true) {
      throw new Error(`Placement ${placement.id} generated imagery cannot claim to depict the exact item`);
    }
  }
  const before = placement.asset.trimBefore
    ?? placement.asset.trim_before_frames
    ?? Math.round((placement.asset.source_in_seconds ?? placement.asset.trim_before_seconds ?? 0) * manifest.fps);
  const afterSeconds = placement.asset.source_out_seconds ?? placement.asset.trim_after_seconds;
  const after = placement.asset.trimAfter
    ?? placement.asset.trim_after_frames
    ?? (afterSeconds === undefined ? undefined : Math.round(afterSeconds * manifest.fps));
  if (before < 0 || (after !== undefined && after <= before)) {
    throw new Error(`Placement ${placement.id} has an invalid source trim window`);
  }
};

for (const placement of manifest.placements || []) {
  validatePlacement(placement);
}

fs.mkdirSync(outputDir, {recursive: true});
const serveUrl = await bundle({
  entryPoint: path.join(projectRoot, 'src', 'index.ts'),
  ...(fs.existsSync(publicDir) ? {publicDir} : {}),
});

const rendered = [];
for (const placement of manifest.placements || []) {
  const durationInFrames = Math.max(1, Math.round(placementDurationSeconds(placement) * manifest.fps));
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
