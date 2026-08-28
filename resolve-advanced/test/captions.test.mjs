import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import JSZip from 'jszip';

import { parseCaptionSource, renderCaptions } from '../server/captions.mjs';
import { captionsTool } from '../server/tools/captions.mjs';
import { drt } from '../server/libs.mjs';
import { runCli } from '../server/cli-lib.mjs';

const SRT = `1
00:00:01,000 --> 00:00:02,250
Hello & goodbye

2
00:00:03,000 --> 00:00:05,000
Second line
continues
`;

test('parseCaptionSource normalizes multiline SRT and WebVTT', async () => {
  const srt = await parseCaptionSource({ content: SRT, inputFormat: 'srt' });
  assert.equal(srt.inputFormat, 'srt');
  assert.deepEqual(
    srt.cues.map(({ startSeconds, endSeconds, text }) => ({ startSeconds, endSeconds, text })),
    [
      { startSeconds: 1, endSeconds: 2.25, text: 'Hello & goodbye' },
      { startSeconds: 3, endSeconds: 5, text: 'Second line\ncontinues' },
    ],
  );

  const vtt = await parseCaptionSource({
    content: 'WEBVTT\n\nintro\n00:00.500 --> 00:01.750 position:50%\nHi there\n',
  });
  assert.equal(vtt.inputFormat, 'vtt');
  assert.equal(vtt.cues[0].id, 'intro');
  assert.equal(vtt.cues[0].startSeconds, 0.5);
});

test('advanced CLI registry dispatches captions without a parallel implementation', async () => {
  const result = await runCli(['captions', 'parse', JSON.stringify({ content: SRT, inputFormat: 'srt' })], { pretty: false });
  assert.equal(result.value.cueCount, 2);
  assert.equal(result.value.cues[0].text, 'Hello & goodbye');
});

test('timed JSON words are grouped into readable deterministic cues', async () => {
  const parsed = await parseCaptionSource({
    words: [
      { word: 'Hello', start: 0, end: 0.3 },
      { word: ',', start: 0.3, end: 0.4 },
      { word: 'world', start: 0.45, end: 0.8 },
      { word: 'Again', start: 2, end: 2.3 },
    ],
    maxGapSeconds: 0.8,
  });
  assert.equal(parsed.inputFormat, 'json-words');
  assert.deepEqual(
    parsed.cues.map((cue) => cue.text),
    ['Hello, world', 'Again'],
  );
  assert.equal(parsed.cues[0].words.length, 3);
});

test('renderCaptions emits round-trippable SRT, VTT, and JSON', async () => {
  const { cues } = await parseCaptionSource({ content: SRT, inputFormat: 'srt' });
  for (const format of ['srt', 'vtt', 'json']) {
    const content = renderCaptions(cues, format);
    const roundtrip = await parseCaptionSource({ content, inputFormat: format });
    assert.deepEqual(
      roundtrip.cues.map((cue) => [cue.startSeconds, cue.endSeconds, cue.text]),
      cues.map((cue) => [cue.startSeconds, cue.endSeconds, cue.text]),
    );
  }
});

test('parse rejects overlapping or malformed cues by default', async () => {
  await assert.rejects(
    () =>
      parseCaptionSource({
        cues: [
          { start: 0, end: 2, text: 'one' },
          { start: 1, end: 3, text: 'two' },
        ],
      }),
    /overlap/,
  );
  await assert.rejects(() => parseCaptionSource({ content: 'not captions', inputFormat: 'srt' }), /no caption cues/);
});

test('compose rejects an offset that places captions before frame zero', async () => {
  await assert.rejects(
    () => captionsTool.handler({
      action: 'compose_native',
      args: {
        cues: [{ start: 0, end: 1, text: 'Too early' }],
        outputPath: '/tmp/never-written-caption.drt',
        offsetFrames: -1,
      },
    }),
    /before timeline frame zero/,
  );
});

test('captions.compose_native writes and round-trips a native subtitle-track DRT', async () => {
  const scratch = await mkdtemp(path.join(tmpdir(), 'resolve-captions-'));
  try {
    const outputPath = path.join(scratch, 'captions.drt');
    const result = await captionsTool.handler({
      action: 'compose_native',
      args: {
        content: SRT,
        inputFormat: 'srt',
        outputPath,
        timelineName: 'Native Captions',
        trackName: 'English',
        frameRate: 24,
        offsetFrames: 12,
      },
    });
    assert.equal(result.artifactKind, 'native-subtitle-track-drt');
    assert.equal(result.cueCount, 2);
    assert.equal(result.validation.structural, true);
    assert.equal(result.validation.liveResolveImport, false);
    assert.equal(result.firstStartFrame, 36);

    const parsed = await drt().parseDRT(outputPath);
    const track = parsed.timelines[0].subtitleTracks[0];
    assert.equal(track.name, 'English');
    assert.deepEqual(
      track.clips.map((clip) => [clip.text, clip.startFrame, clip.durationFrames]),
      [
        ['Hello & goodbye', 36, 30],
        ['Second line\ncontinues', 84, 48],
      ],
    );

    const zip = await JSZip.loadAsync(await readFile(outputPath));
    const seqPath = Object.keys(zip.files).find((name) => /SeqContainer\d*\.xml$/.test(name));
    const xml = await zip.file(seqPath).async('string');
    assert.match(xml, /<SubtitleTrackVec>/);
    assert.match(xml, /<Sm2TiSubtitleTrack\b/);
    assert.match(xml, /<PrettyType>Subtitle<\/PrettyType>/);
    assert.match(xml, /Hello &amp; goodbye/);
  } finally {
    await rm(scratch, { recursive: true, force: true });
  }
});

test('captions.write can return stdout-ready content without touching disk', async () => {
  const result = await captionsTool.handler({
    action: 'write',
    args: { cues: [{ start: 0, end: 1, text: 'Bash friendly' }], outputFormat: 'vtt' },
  });
  assert.equal(result.outputFormat, 'vtt');
  assert.match(result.content, /^WEBVTT/);
  assert.equal(result.cueCount, 1);
});
