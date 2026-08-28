/**
 * Offline transition inventory and clone-based editing.
 *
 * Only Cross Dissolve/AlignmentType=2 is synthesized elsewhere.  This module can
 * copy a transition already present in a project because its opaque effect payload
 * is preserved verbatim; it never invents an encoding for an unobserved effect.
 */

const {
  loadDrpZip,
  selectTargetSeq,
  getTrackVec,
  replaceTrackVec,
  getItemsInner,
  setItemsInner,
  freshDbIds,
} = require('./seq-surgery');
const { createHash } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { DURATION_PRESETS_SECONDS, resolveTransitionDuration } = require('./place-transition');

const ITEM_RE = /<Element>\s*<(Sm2TiVideoClip|Sm2TiAudioClip|Sm2TiGenerator|Sm2TiTransition)\b[\s\S]*?<\/(?:Sm2TiVideoClip|Sm2TiAudioClip|Sm2TiGenerator|Sm2TiTransition)>\s*<\/Element>/g;
const TRANSITION_RE = /<Sm2TiTransition\b/;

function tagText(xml, tag) {
  const m = xml.match(new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`));
  return m ? m[1] : null;
}
function intTag(xml, tag) {
  const raw = tagText(xml, tag);
  if (raw == null || !/^-?\d+$/.test(raw.trim())) return null;
  return Number.parseInt(raw, 10);
}
function dbId(xml) {
  return (xml.match(/<Sm2Ti(?:VideoClip|AudioClip|Generator|Transition)\s+DbId="([^"]+)"/) || [])[1] || null;
}
function itemKind(xml) {
  return (xml.match(/<(Sm2TiVideoClip|Sm2TiAudioClip|Sm2TiGenerator|Sm2TiTransition)\b/) || [])[1] || null;
}
function splitItems(inner) {
  return inner.match(ITEM_RE) || [];
}
function isTransition(xml) {
  return TRANSITION_RE.test(xml);
}
function clipSummary(xml) {
  if (!xml || isTransition(xml)) return null;
  const start = intTag(xml, 'Start');
  const duration = intTag(xml, 'Duration');
  return {
    kind: itemKind(xml),
    dbId: dbId(xml),
    name: tagText(xml, 'Name'),
    start,
    duration,
    end: Number.isInteger(start) && Number.isInteger(duration) ? start + duration : null,
  };
}

function encodingSummary(raw) {
  const fieldsBlob = (tagText(raw, 'FieldsBlob') || '').replace(/\s+/g, '');
  const effectFilters = (tagText(raw, 'EffectFiltersBA') || '').replace(/\s+/g, '');
  return {
    fieldsBlobBytes: /^[0-9a-f]*$/i.test(fieldsBlob) ? Math.floor(fieldsBlob.length / 2) : null,
    effectFiltersBytes: /^[0-9a-f]*$/i.test(effectFilters) ? Math.floor(effectFilters.length / 2) : null,
    sha256: createHash('sha256').update(fieldsBlob).update('\0').update(effectFilters).digest('hex'),
  };
}

const FIXTURE_PATH = path.join(__dirname, 'templates', 'transition-cross-dissolve.xml');
const FIXTURE_ENCODING = encodingSummary(fs.readFileSync(FIXTURE_PATH, 'utf8'));

function transitionSummary(raw, context) {
  const start = intTag(raw, 'Start');
  const durationFrames = intTag(raw, 'Duration');
  const alignmentCode = intTag(raw, 'AlignmentType');
  const alignment = alignmentCode === 2 ? 'center' : 'unknown';
  const atFrame = alignment === 'center' && Number.isInteger(start) && Number.isInteger(durationFrames)
    ? start + Math.floor(durationFrames / 2)
    : null;
  const left = clipSummary(context.left);
  const right = clipSummary(context.right);
  const issues = [];
  if (!dbId(raw)) issues.push({ severity: 'error', code: 'missing_db_id' });
  if (!Number.isInteger(start)) issues.push({ severity: 'error', code: 'invalid_start' });
  if (!Number.isInteger(durationFrames) || durationFrames < 1) issues.push({ severity: 'error', code: 'invalid_duration' });
  if (!left || !right) issues.push({ severity: 'error', code: 'not_between_timeline_items' });
  if (left && right && left.end !== right.start) issues.push({ severity: 'error', code: 'non_abutting_boundary', leftEnd: left.end, rightStart: right.start });
  if (alignment !== 'center') issues.push({ severity: 'warning', code: 'unverified_alignment', alignmentCode });
  if (alignment === 'center' && left && right && atFrame !== left.end) issues.push({ severity: 'error', code: 'center_not_on_cut', inferredCut: atFrame, actualCut: left.end });
  const prettyType = tagText(raw, 'PrettyType');
  const encoding = encodingSummary(raw);
  if (!encoding.fieldsBlobBytes) issues.push({ severity: 'error', code: 'missing_fields_blob' });
  if (!encoding.effectFiltersBytes) issues.push({ severity: 'error', code: 'missing_effect_filters' });
  const fixtureTypeRecognized = prettyType === 'Cross Dissolve' && alignmentCode === 2;
  const fixtureVerified = fixtureTypeRecognized && encoding.sha256 === FIXTURE_ENCODING.sha256;
  if (!fixtureVerified) issues.push({ severity: 'warning', code: 'no_bundled_fixture', prettyType, alignmentCode });
  return {
    trackType: context.trackType,
    track: context.track,
    transitionIndex: context.transitionIndex,
    itemIndex: context.itemIndex,
    dbId: dbId(raw),
    name: tagText(raw, 'Name'),
    prettyType,
    start,
    durationFrames,
    alignment,
    alignmentCode,
    atFrame,
    left,
    right,
    fixtureTypeRecognized,
    fixtureVerified,
    encoding,
    structurallyValid: !issues.some((x) => x.severity === 'error'),
    issues,
  };
}

function summariesFromTracks(tracks, trackType) {
  const transitions = [];
  tracks.forEach((trackXml, trackIndex) => {
    const items = splitItems(getItemsInner(trackXml));
    let transitionIndex = 0;
    items.forEach((raw, itemIndex) => {
      if (!isTransition(raw)) return;
      transitions.push(transitionSummary(raw, {
        trackType,
        track: trackIndex + 1,
        transitionIndex,
        itemIndex,
        left: items[itemIndex - 1],
        right: items[itemIndex + 1],
      }));
      transitionIndex += 1;
    });
  });
  return transitions;
}

async function loadContext(drpInput, opts = {}) {
  const trackType = opts.trackType || 'video';
  const zip = await loadDrpZip(drpInput);
  const selected = await selectTargetSeq(zip, opts.timelineUuid);
  const vec = getTrackVec(selected.xml, trackType);
  return { zip, selected, vec, trackType };
}

async function listTransitions(drpInput, opts = {}) {
  const { selected, vec, trackType } = await loadContext(drpInput, opts);
  const transitions = summariesFromTracks(vec.tracks, trackType);
  return {
    entry: selected.entry,
    timelineUuid: selected.seqId,
    trackType,
    transitionCount: transitions.length,
    transitions,
  };
}

async function validateTransitions(drpInput, opts = {}) {
  const report = await listTransitions(drpInput, opts);
  const duplicateIds = report.transitions
    .map((x) => x.dbId)
    .filter((id, i, ids) => id && ids.indexOf(id) !== i)
    .filter((id, i, ids) => ids.indexOf(id) === i);
  const errors = report.transitions.flatMap((x) => x.issues.filter((i) => i.severity === 'error').map((i) => ({ transitionDbId: x.dbId, ...i })));
  duplicateIds.forEach((id) => errors.push({ severity: 'error', code: 'duplicate_transition_db_id', transitionDbId: id }));
  const warnings = report.transitions.flatMap((x) => x.issues.filter((i) => i.severity === 'warning').map((i) => ({ transitionDbId: x.dbId, ...i })));
  return {
    ...report,
    valid: errors.length === 0,
    errors,
    warnings,
    validationScope: 'DRP structure only; source handles, decoded effect semantics, import acceptance, and rendered pixels are not verified',
    liveRoundTripRequired: true,
  };
}

function selectTransition(items, opts) {
  const matches = items.map((raw, itemIndex) => ({ raw, itemIndex })).filter((x) => isTransition(x.raw));
  if (opts.sourceTransitionDbId) {
    const hit = matches.find((x) => dbId(x.raw) === opts.sourceTransitionDbId);
    if (!hit) throw new Error(`cloneTransition: source transition ${opts.sourceTransitionDbId} not found`);
    return hit;
  }
  const index = opts.sourceTransitionIndex == null ? 0 : opts.sourceTransitionIndex;
  if (!Number.isInteger(index) || index < 0 || index >= matches.length) throw new Error(`cloneTransition: sourceTransitionIndex ${index} does not exist`);
  return matches[index];
}

function boundaryIndex(items, atFrame) {
  for (let i = 0; i < items.length - 1; i += 1) {
    if (isTransition(items[i]) || isTransition(items[i + 1])) continue;
    const left = clipSummary(items[i]);
    const right = clipSummary(items[i + 1]);
    if (left && right && left.end === atFrame && right.start === atFrame) return i;
  }
  return -1;
}

async function cloneTransition(drpInput, opts = {}) {
  const sourceTrack = opts.sourceTrack || 1;
  const targetTrack = opts.targetTrack || sourceTrack;
  if (!Number.isInteger(sourceTrack) || sourceTrack < 1 || !Number.isInteger(targetTrack) || targetTrack < 1) throw new TypeError('cloneTransition: sourceTrack/targetTrack must be positive integers');
  if (!Number.isInteger(opts.atFrame)) throw new TypeError('cloneTransition: atFrame must be an integer');
  const context = await loadContext(drpInput, opts);
  if (sourceTrack > context.vec.tracks.length || targetTrack > context.vec.tracks.length) throw new Error(`cloneTransition: track out of range (${context.vec.tracks.length} ${context.trackType} tracks)`);
  const sourceItems = splitItems(getItemsInner(context.vec.tracks[sourceTrack - 1]));
  const selected = selectTransition(sourceItems, opts);
  const sourceAlignment = intTag(selected.raw, 'AlignmentType');
  if (sourceAlignment !== 2) throw new Error(`cloneTransition: only centered source transitions (AlignmentType 2) can be positioned safely; got ${sourceAlignment}`);

  const targetItems = splitItems(getItemsInner(context.vec.tracks[targetTrack - 1]));
  const leftIndex = boundaryIndex(targetItems, opts.atFrame);
  if (leftIndex < 0) throw new Error(`cloneTransition: no bare abutting boundary at frame ${opts.atFrame} on track ${targetTrack}`);
  const sourceDuration = intTag(selected.raw, 'Duration');
  const duration = opts.durationFrames == null && opts.durationSeconds == null && opts.durationPreset == null
    ? { durationFrames: sourceDuration, durationSource: 'source-transition' }
    : resolveTransitionDuration(opts);
  let cloned = freshDbIds(selected.raw);
  const start = opts.atFrame - Math.floor(duration.durationFrames / 2);
  cloned = cloned.replace(/<Start>-?\d+<\/Start>/, `<Start>${start}</Start>`);
  cloned = cloned.replace(/<Duration>-?\d+<\/Duration>/, `<Duration>${duration.durationFrames}</Duration>`);
  const transitionDbId = dbId(cloned);
  const inner = getItemsInner(context.vec.tracks[targetTrack - 1]);
  const newInner = inner.replace(targetItems[leftIndex], `${targetItems[leftIndex]}${cloned}`);
  context.vec.tracks[targetTrack - 1] = setItemsInner(context.vec.tracks[targetTrack - 1], newInner);
  const xml = replaceTrackVec(context.selected.xml, context.trackType, context.vec.match, context.vec.tracks);
  context.zip.file(context.selected.entry, xml);
  const buffer = await context.zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' });
  const prettyType = tagText(cloned, 'PrettyType');
  const encoding = encodingSummary(cloned);
  return {
    buffer,
    entry: context.selected.entry,
    timelineUuid: context.selected.seqId,
    trackType: context.trackType,
    sourceTrack,
    targetTrack,
    atFrame: opts.atFrame,
    start,
    transitionDbId,
    prettyType,
    alignment: 'center',
    ...duration,
    opaquePayloadPreserved: true,
    fixtureTypeRecognized: prettyType === 'Cross Dissolve',
    fixtureVerified: prettyType === 'Cross Dissolve' && encoding.sha256 === FIXTURE_ENCODING.sha256,
    encoding,
    liveRoundTripRequired: true,
  };
}

function selectEditableTransition(items, opts, operation) {
  const matches = items
    .map((raw, itemIndex) => ({ raw, itemIndex }))
    .filter((x) => isTransition(x.raw))
    .map((match, transitionIndex) => ({ ...match, transitionIndex }));
  if (opts.transitionDbId) {
    const hit = matches.find((x) => dbId(x.raw) === opts.transitionDbId);
    if (!hit) throw new Error(`${operation}: transition ${opts.transitionDbId} not found`);
    return hit;
  }
  const index = opts.transitionIndex == null ? 0 : opts.transitionIndex;
  if (!Number.isInteger(index) || index < 0 || index >= matches.length) throw new Error(`${operation}: transitionIndex ${index} does not exist`);
  return matches[index];
}

async function setTransitionDuration(drpInput, opts = {}) {
  const track = opts.track || 1;
  if (!Number.isInteger(track) || track < 1) throw new TypeError('setTransitionDuration: track must be a positive integer');
  const context = await loadContext(drpInput, opts);
  if (track > context.vec.tracks.length) throw new Error(`setTransitionDuration: track ${track} does not exist`);
  const items = splitItems(getItemsInner(context.vec.tracks[track - 1]));
  const selected = selectEditableTransition(items, opts, 'setTransitionDuration');
  if (intTag(selected.raw, 'AlignmentType') !== 2) throw new Error('setTransitionDuration: only centered transitions can be retimed safely');
  const previousStart = intTag(selected.raw, 'Start');
  const previousDurationFrames = intTag(selected.raw, 'Duration');
  const atFrame = previousStart + Math.floor(previousDurationFrames / 2);
  const left = clipSummary(items[selected.itemIndex - 1]);
  const right = clipSummary(items[selected.itemIndex + 1]);
  if (!left || !right || left.end !== right.start || left.end !== atFrame) throw new Error('setTransitionDuration: transition is not centered on an abutting cut');
  const duration = resolveTransitionDuration(opts);
  const start = atFrame - Math.floor(duration.durationFrames / 2);
  let replacement = selected.raw.replace(/<Start>-?\d+<\/Start>/, `<Start>${start}</Start>`);
  replacement = replacement.replace(/<Duration>-?\d+<\/Duration>/, `<Duration>${duration.durationFrames}</Duration>`);
  const inner = getItemsInner(context.vec.tracks[track - 1]);
  context.vec.tracks[track - 1] = setItemsInner(context.vec.tracks[track - 1], inner.replace(selected.raw, replacement));
  const xml = replaceTrackVec(context.selected.xml, context.trackType, context.vec.match, context.vec.tracks);
  context.zip.file(context.selected.entry, xml);
  const buffer = await context.zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' });
  return {
    buffer,
    entry: context.selected.entry,
    timelineUuid: context.selected.seqId,
    trackType: context.trackType,
    track,
    transitionDbId: dbId(replacement),
    prettyType: tagText(replacement, 'PrettyType'),
    atFrame,
    start,
    previousStart,
    previousDurationFrames,
    ...duration,
    alignment: 'center',
    opaquePayloadPreserved: true,
    liveRoundTripRequired: true,
  };
}

async function deleteTransition(drpInput, opts = {}) {
  const track = opts.track || 1;
  if (!Number.isInteger(track) || track < 1) throw new TypeError('deleteTransition: track must be a positive integer');
  const context = await loadContext(drpInput, opts);
  if (track > context.vec.tracks.length) throw new Error(`deleteTransition: track ${track} does not exist`);
  const items = splitItems(getItemsInner(context.vec.tracks[track - 1]));
  const selected = selectEditableTransition(items, opts, 'deleteTransition');
  const deleted = transitionSummary(selected.raw, {
    trackType: context.trackType,
    track,
    transitionIndex: selected.transitionIndex,
    itemIndex: selected.itemIndex,
    left: items[selected.itemIndex - 1],
    right: items[selected.itemIndex + 1],
  });
  const inner = getItemsInner(context.vec.tracks[track - 1]);
  context.vec.tracks[track - 1] = setItemsInner(context.vec.tracks[track - 1], inner.replace(selected.raw, ''));
  const xml = replaceTrackVec(context.selected.xml, context.trackType, context.vec.match, context.vec.tracks);
  context.zip.file(context.selected.entry, xml);
  const buffer = await context.zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' });
  return {
    buffer,
    entry: context.selected.entry,
    timelineUuid: context.selected.seqId,
    trackType: context.trackType,
    track,
    deleted,
    ripple: false,
    clipsChanged: false,
    liveRoundTripRequired: true,
  };
}

function transitionCapabilities() {
  return {
    synthesized: [{ transitionType: 'cross_dissolve', prettyType: 'Cross Dissolve', trackType: 'video', alignment: 'center', fixture: 'Resolve 21 GUI-authored XML' }],
    durationPresetsSeconds: DURATION_PRESETS_SECONDS,
    cloneExisting: { trackTypes: ['video', 'audio'], alignment: ['center'], opaquePayloadPreserved: true },
    unsupported: ['synthesizing unobserved transition types', 'start/end alignment without fixtures', 'audio transition synthesis'],
    validationScope: {
      checked: ['transition identity', 'duration/start fields', 'item ordering', 'abutting cut relationship', 'duplicate transition identities', 'opaque encoding fingerprint'],
      notChecked: ['source media handles', 'Resolve import acceptance', 'rendered pixels', 'semantics of unrecognized opaque payloads'],
    },
    liveRoundTripRequired: true,
  };
}

module.exports = {
  listTransitions,
  validateTransitions,
  cloneTransition,
  setTransitionDuration,
  deleteTransition,
  transitionCapabilities,
  _internals: { splitItems, transitionSummary },
};
