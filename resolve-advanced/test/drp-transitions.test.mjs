import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs/promises';
import { createRequire } from 'node:module';

import { drpTool } from '../server/tools/drp.mjs';

const require = createRequire(import.meta.url);
const JSZip = require('jszip');

async function synth3(file) {
  const id = (n) => `${n}0000000-0000-4000-8000-000000000000`;
  const clip = (i) => `<Element><Sm2TiVideoClip DbId="${id(i + 1)}"><FieldsBlob/><Name>c${i}</Name><Start>${i * 100}</Start><Duration>100</Duration><In/></Sm2TiVideoClip></Element>`;
  const track = `<Element><Sm2TiTrack DbId="${id(4)}"><FieldsBlob/><Type>0</Type><Items>${clip(0)}${clip(1)}${clip(2)}</Items><FusionCompHolderItems/><LayersVec/></Sm2TiTrack></Element>`;
  const seq = `<Sm2SequenceContainer DbId="${id(5)}"><FieldsBlob/><VideoTrackVec>${track}</VideoTrackVec><AudioTrackVec/></Sm2SequenceContainer>`;
  const zip = new JSZip();
  zip.file('SeqContainer/s1.xml', seq);
  await fs.writeFile(file, await zip.generateAsync({ type: 'nodebuffer' }));
}

test('drp tool exposes fixture-grounded transition placement, inventory, validation, and cloning', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'drp-transition-tool-'));
  const source = path.join(dir, 'source.drp');
  const placed = path.join(dir, 'placed.drp');
  const cloned = path.join(dir, 'cloned.drp');
  const retimed = path.join(dir, 'retimed.drp');
  const deleted = path.join(dir, 'deleted.drp');
  await synth3(source);

  const caps = await drpTool.handler({ action: 'transition_capabilities', args: {} });
  assert.equal(caps.synthesized.length, 1);

  const write = await drpTool.handler({
    action: 'place_transition',
    args: { drpPath: source, outputPath: placed, track: 1, atFrame: 100, durationPreset: 'standard', frameRate: 24 },
  });
  assert.equal(write.durationFrames, 12);
  assert.equal(write.durationSource, 'preset:standard');

  const listed = await drpTool.handler({ action: 'list_transitions', args: { drpPath: placed } });
  assert.equal(listed.transitionCount, 1);
  assert.equal(listed.transitions[0].atFrame, 100);

  const copy = await drpTool.handler({
    action: 'clone_transition',
    args: { drpPath: placed, outputPath: cloned, sourceTrack: 1, targetTrack: 1, atFrame: 200 },
  });
  assert.equal(copy.durationSource, 'source-transition');
  assert.equal(copy.opaquePayloadPreserved, true);

  const validated = await drpTool.handler({ action: 'validate_transitions', args: { drpPath: cloned } });
  assert.equal(validated.valid, true);
  assert.equal(validated.transitionCount, 2);

  const resize = await drpTool.handler({
    action: 'set_transition_duration',
    args: { drpPath: cloned, outputPath: retimed, track: 1, transitionIndex: 1, durationSeconds: 1, frameRate: 24 },
  });
  assert.equal(resize.durationFrames, 24);
  assert.equal(resize.atFrame, 200);

  const remove = await drpTool.handler({
    action: 'delete_transition',
    args: { drpPath: retimed, outputPath: deleted, track: 1, transitionDbId: resize.transitionDbId },
  });
  assert.equal(remove.ripple, false);
  assert.equal((await drpTool.handler({ action: 'list_transitions', args: { drpPath: deleted } })).transitionCount, 1);
});

test('drp place_transition schema rejects unverified transition names/alignment', async () => {
  await assert.rejects(
    () => drpTool.handler({ action: 'place_transition', args: { drpPath: '/x', outputPath: '/y', track: 1, atFrame: 10, transitionType: 'wipe' } }),
    /cross_dissolve/,
  );
  await assert.rejects(
    () => drpTool.handler({ action: 'place_transition', args: { drpPath: '/x', outputPath: '/y', track: 1, atFrame: 10, alignment: 'start' } }),
    /center/,
  );
});
