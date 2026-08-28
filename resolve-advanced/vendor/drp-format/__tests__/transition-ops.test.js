const test = require('node:test');
const assert = require('node:assert');
const JSZip = require('jszip');
const { placeTransition } = require('../place-transition');
const {
  listTransitions,
  validateTransitions,
  cloneTransition,
  setTransitionDuration,
  deleteTransition,
  transitionCapabilities,
} = require('../transition-ops');

const UUID = {
  c0: '10000000-0000-4000-8000-000000000000',
  c1: '20000000-0000-4000-8000-000000000000',
  c2: '30000000-0000-4000-8000-000000000000',
};

async function synth3() {
  const clip = (i) => `<Element><Sm2TiVideoClip DbId="${UUID[`c${i}`]}"><FieldsBlob/><Name>c${i}</Name><Start>${i * 100}</Start><Duration>100</Duration><In/></Sm2TiVideoClip></Element>`;
  const track = `<Element><Sm2TiTrack DbId="40000000-0000-4000-8000-000000000000"><FieldsBlob/><Type>0</Type><SubType>0</SubType><Flags>0</Flags><Sequence>s</Sequence><Items>${clip(0)}${clip(1)}${clip(2)}</Items><FusionCompHolderItems/><UserDefinedName/><LayersVec/></Sm2TiTrack></Element>`;
  const seq = `<?xml version="1.0"?>\n<Sm2SequenceContainer DbId="50000000-0000-4000-8000-000000000000"><FieldsBlob/><VideoTrackVec>${track}</VideoTrackVec><AudioTrackVec/></Sm2SequenceContainer>`;
  const zip = new JSZip();
  zip.file('SeqContainer/s1.xml', seq);
  return zip.generateAsync({ type: 'nodebuffer' });
}

test('transition inventory reports cut neighbors, fixture status, and structural validity', async () => {
  const placed = await placeTransition(await synth3(), { track: 1, atFrame: 100, durationFrames: 24 });
  const out = await listTransitions(placed.buffer);
  assert.equal(out.transitionCount, 1);
  assert.deepEqual(out.transitions[0].left.name, 'c0');
  assert.deepEqual(out.transitions[0].right.name, 'c1');
  assert.equal(out.transitions[0].atFrame, 100);
  assert.equal(out.transitions[0].alignment, 'center');
  assert.equal(out.transitions[0].fixtureVerified, true);
  assert.equal(out.transitions[0].structurallyValid, true);
  assert.equal(out.transitions[0].encoding.sha256.length, 64);
  assert.ok(out.transitions[0].encoding.effectFiltersBytes > 0);

  const validated = await validateTransitions(placed.buffer);
  assert.equal(validated.valid, true);
  assert.deepEqual(validated.errors, []);
  assert.match(validated.validationScope, /source handles/);
});

test('fixture verification requires the actual bundled opaque payload', async () => {
  const placed = await placeTransition(await synth3(), { track: 1, atFrame: 100, durationFrames: 24 });
  const zip = await JSZip.loadAsync(placed.buffer);
  const entry = zip.file('SeqContainer/s1.xml');
  const xml = (await entry.async('string'))
    .replace(/<FieldsBlob>[0-9a-f]+<\/FieldsBlob>(?=[\s\S]*?<PrettyType>Cross Dissolve)/i, '<FieldsBlob/>')
    .replace(/<EffectFiltersBA>[0-9a-f]+<\/EffectFiltersBA>/i, '<EffectFiltersBA/>');
  zip.file('SeqContainer/s1.xml', xml);
  const stripped = await zip.generateAsync({ type: 'nodebuffer' });
  const report = await validateTransitions(stripped);
  assert.equal(report.valid, false);
  assert.equal(report.transitions[0].fixtureTypeRecognized, true);
  assert.equal(report.transitions[0].fixtureVerified, false);
  assert.ok(report.errors.some((x) => x.code === 'missing_fields_blob'));
  assert.ok(report.errors.some((x) => x.code === 'missing_effect_filters'));
});

test('cloneTransition preserves opaque effect payload and positions a fresh identity at another bare cut', async () => {
  const placed = await placeTransition(await synth3(), { track: 1, atFrame: 100, durationFrames: 20 });
  const cloned = await cloneTransition(placed.buffer, { sourceTrack: 1, sourceTransitionIndex: 0, targetTrack: 1, atFrame: 200, durationFrames: 10 });
  assert.equal(cloned.atFrame, 200);
  assert.equal(cloned.start, 195);
  assert.equal(cloned.durationFrames, 10);
  assert.equal(cloned.opaquePayloadPreserved, true);
  assert.notEqual(cloned.transitionDbId, placed.transitionDbId);

  const out = await listTransitions(cloned.buffer);
  assert.equal(out.transitionCount, 2);
  assert.deepEqual(out.transitions.map((x) => x.atFrame), [100, 200]);
  assert.deepEqual(out.transitions.map((x) => x.durationFrames), [20, 10]);
  assert.equal(out.transitions[0].encoding.sha256, out.transitions[1].encoding.sha256, 'opaque encoding was copied byte-for-byte');
  assert.equal((await validateTransitions(cloned.buffer)).valid, true);
});

test('cloneTransition refuses occupied cuts and unverified source alignment', async () => {
  const placed = await placeTransition(await synth3(), { track: 1, atFrame: 100 });
  await assert.rejects(
    () => cloneTransition(placed.buffer, { sourceTrack: 1, targetTrack: 1, atFrame: 100 }),
    /no bare abutting boundary/,
  );

  const zip = await JSZip.loadAsync(placed.buffer);
  const entry = zip.file('SeqContainer/s1.xml');
  const xml = (await entry.async('string')).replace('<AlignmentType>2</AlignmentType>', '<AlignmentType>9</AlignmentType>');
  zip.file('SeqContainer/s1.xml', xml);
  const unknown = await zip.generateAsync({ type: 'nodebuffer' });
  await assert.rejects(
    () => cloneTransition(unknown, { sourceTrack: 1, targetTrack: 1, atFrame: 200 }),
    /only centered source transitions/,
  );
  const report = await validateTransitions(unknown);
  assert.equal(report.valid, true, 'unknown encoding is warned, not declared corrupt');
  assert.ok(report.warnings.some((x) => x.code === 'unverified_alignment'));
});

test('setTransitionDuration preserves the cut and opaque payload; deleteTransition is non-ripple', async () => {
  const placed = await placeTransition(await synth3(), { track: 1, atFrame: 100, durationFrames: 20 });
  const retimed = await setTransitionDuration(placed.buffer, { track: 1, transitionIndex: 0, durationPreset: 'slow', frameRate: 24 });
  assert.equal(retimed.atFrame, 100);
  assert.equal(retimed.start, 88);
  assert.equal(retimed.durationFrames, 24);
  assert.equal(retimed.previousDurationFrames, 20);
  assert.equal(retimed.opaquePayloadPreserved, true);

  const afterRetime = await listTransitions(retimed.buffer);
  assert.equal(afterRetime.transitions[0].atFrame, 100);
  assert.equal(afterRetime.transitions[0].durationFrames, 24);

  const removed = await deleteTransition(retimed.buffer, { track: 1, transitionDbId: retimed.transitionDbId });
  assert.equal(removed.deleted.prettyType, 'Cross Dissolve');
  assert.equal(removed.ripple, false);
  assert.equal(removed.clipsChanged, false);
  assert.equal((await listTransitions(removed.buffer)).transitionCount, 0);
});

test('deleteTransition reports the selected transition index when selecting by DbId', async () => {
  const first = await placeTransition(await synth3(), { track: 1, atFrame: 100 });
  const second = await placeTransition(first.buffer, { track: 1, atFrame: 200 });
  const inventory = await listTransitions(second.buffer);
  const removed = await deleteTransition(second.buffer, {
    track: 1,
    transitionDbId: inventory.transitions[1].dbId,
  });
  assert.equal(removed.deleted.transitionIndex, 1);
  assert.equal(removed.deleted.atFrame, 200);
});

test('transitionCapabilities is explicit about fixture-backed and unsupported authoring', () => {
  const out = transitionCapabilities();
  assert.equal(out.synthesized.length, 1);
  assert.equal(out.synthesized[0].transitionType, 'cross_dissolve');
  assert.deepEqual(out.cloneExisting.alignment, ['center']);
  assert.ok(out.unsupported.some((x) => /unobserved/.test(x)));
  assert.ok(out.validationScope.notChecked.includes('rendered pixels'));
});
