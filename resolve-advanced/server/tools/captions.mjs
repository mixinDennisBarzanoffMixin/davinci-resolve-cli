/**
 * captions tool — shell-friendly caption interchange and native subtitle DRT
 * authoring. All actions are local/offline and do not require Resolve.
 */

import fs from 'node:fs/promises';
import path from 'node:path';
import { z } from 'zod';
import { drt } from '../libs.mjs';
import { cuesToSubtitleClips, parseCaptionSource, renderCaptions, writeCaptionFile } from '../captions.mjs';

const cue = z.object({}).passthrough();
const sourceFields = {
  inputPath: z.string().optional().describe('SRT, VTT, or JSON file path'),
  content: z.string().optional().describe('Inline SRT, VTT, or JSON content'),
  inputFormat: z.enum(['srt', 'vtt', 'json']).optional(),
  cues: z.array(cue).min(1).optional().describe('Timed JSON cues'),
  words: z.array(cue).min(1).optional().describe('Timed word objects; grouped into readable cues'),
  segments: z.array(cue).min(1).optional().describe('Whisper-style timed segments'),
  allowOverlaps: z.boolean().optional().describe('Allow overlapping cues (default false)'),
  maxWords: z.number().int().positive().optional(),
  maxCharacters: z.number().int().positive().optional(),
  maxDurationSeconds: z.number().positive().optional(),
  maxGapSeconds: z.number().positive().optional(),
};
const withOneSource = (shape) =>
  z.object(shape).superRefine((value, ctx) => {
    const present = ['inputPath', 'content', 'cues', 'words', 'segments'].filter((key) => value[key] !== undefined);
    if (present.length !== 1) ctx.addIssue({ code: z.ZodIssueCode.custom, message: 'provide exactly one of inputPath, content, cues, words, or segments' });
  });

const parseSchema = withOneSource(sourceFields);
const writeSchema = withOneSource({
  ...sourceFields,
  outputPath: z.string().optional().describe('Write to this path; omit to return content on stdout'),
  outputFormat: z.enum(['srt', 'vtt', 'json']).optional().describe('Default inferred from outputPath, else srt'),
});
const composeSchema = withOneSource({
  ...sourceFields,
  outputPath: z.string().describe('Standalone .drt path to write'),
  timelineName: z.string().optional(),
  trackName: z.string().optional(),
  frameRate: z.number().positive().max(120).optional(),
  startTimecode: z
    .string()
    .regex(/^\d{2,3}:\d{2}:\d{2}:\d{2}$/)
    .optional(),
  resolution: z
    .string()
    .regex(/^\d+x\d+$/)
    .optional(),
  offsetFrames: z.number().int().optional().describe('Shift every cue by this many timeline frames'),
});

function inferredOutputFormat(outputPath) {
  if (!outputPath) return 'srt';
  const ext = path.extname(outputPath).slice(1).toLowerCase();
  return ['srt', 'vtt', 'json'].includes(ext) ? ext : 'srt';
}

export const captionsTool = {
  name: 'captions',
  description:
    'Caption interchange + native Resolve subtitle artifacts — offline, no Resolve required. Actions: parse (SRT/VTT/JSON cues or timed words → normalized JSON), write (normalized input → SRT/VTT/JSON; omit outputPath for stdout), compose_native (author a standalone .drt containing a native subtitle track). compose_native does not mutate an existing .drp and does not apply Resolve 20 Animated Subtitle templates.',
  async handler({ action, args }) {
    if (action === 'parse') {
      const parsed = await parseCaptionSource(parseSchema.parse(args));
      return { ...parsed, cueCount: parsed.cues.length };
    }
    if (action === 'write') {
      const p = writeSchema.parse(args);
      const parsed = await parseCaptionSource(p);
      const outputFormat = p.outputFormat || inferredOutputFormat(p.outputPath);
      const content = renderCaptions(parsed.cues, outputFormat);
      if (!p.outputPath) return { outputFormat, cueCount: parsed.cues.length, content };
      const bytes = await writeCaptionFile(p.outputPath, content);
      return { outputPath: p.outputPath, outputFormat, cueCount: parsed.cues.length, bytes };
    }
    if (action === 'compose_native') {
      const p = composeSchema.parse(args);
      if (path.extname(p.outputPath).toLowerCase() !== '.drt') {
        throw new Error('captions.compose_native writes a standalone .drt; outputPath must end in .drt');
      }
      const parsed = await parseCaptionSource(p);
      const frameRate = p.frameRate ?? 24;
      const timelineName = p.timelineName || 'Caption Timeline';
      const trackName = p.trackName || 'Subtitle Track 1';
      const startTimecode = p.startTimecode || '01:00:00:00';
      const resolution = p.resolution || '1920x1080';
      const clips = cuesToSubtitleClips(parsed.cues, { frameRate, offsetFrames: p.offsetFrames ?? 0 });
      const buffer = await drt().buildDRT({
        timelines: [
          {
            name: timelineName,
            frameRate,
            startTimecode,
            resolution,
            videoTracks: [],
            audioTracks: [],
            subtitleTracks: [{ name: trackName, clips }],
          },
        ],
        metadata: {
          source: 'davinci-resolve-cli captions.compose_native',
          captionInputFormat: parsed.inputFormat,
          cueCount: clips.length,
        },
      });
      await fs.writeFile(p.outputPath, buffer);
      const structural = await drt().validateDRT(buffer);
      if (!structural.valid) {
        throw new Error(`authored DRT failed structural validation: ${JSON.stringify(structural.errors)}`);
      }
      return {
        outputPath: p.outputPath,
        bytes: buffer.length,
        artifactKind: 'native-subtitle-track-drt',
        inputFormat: parsed.inputFormat,
        cueCount: clips.length,
        timelineName,
        trackName,
        frameRate,
        startTimecode,
        resolution,
        firstStartFrame: clips[0].startFrame,
        lastEndFrame: clips.at(-1).startFrame + clips.at(-1).durationFrames,
        validation: {
          structural: true,
          liveResolveImport: false,
        },
        limitations: [
          'Creates a standalone DRT for import; it does not patch an existing DRP.',
          'The scripting API cannot read or edit native subtitle text/timing after import.',
          'Resolve 20 Animated Subtitle templates are not attached; that UI-only workflow remains separate.',
          'Run an import/readback check in the target Resolve version before production use.',
        ],
      };
    }
    throw new Error(`Unknown captions action: ${action}`);
  },
};
