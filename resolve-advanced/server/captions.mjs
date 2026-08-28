/**
 * Transport-neutral caption parsing/rendering helpers.
 *
 * The normalized cue model is deliberately small so shell pipelines can move
 * captions between SRT, WebVTT, JSON, and Resolve artifact authoring without
 * carrying a second timeline model:
 *   { id?, startSeconds, endSeconds, text, words? }
 */

import fs from 'node:fs/promises';
import path from 'node:path';

const TIMING = /^((?:\d{1,3}:)?\d{2}:\d{2}[,.]\d{3})\s*-->\s*((?:\d{1,3}:)?\d{2}:\d{2}[,.]\d{3})(?:\s+.*)?$/;

function timestampSeconds(value) {
  const parts = value.replace(',', '.').split(':');
  const seconds = Number(parts.pop());
  const minutes = Number(parts.pop());
  const hours = parts.length ? Number(parts.pop()) : 0;
  return hours * 3600 + minutes * 60 + seconds;
}

function parseTiming(line, source, cueIndex) {
  const match = String(line).trim().match(TIMING);
  if (!match) throw new Error(`${source}: cue ${cueIndex + 1} has invalid timing line: ${line}`);
  return {
    startSeconds: timestampSeconds(match[1]),
    endSeconds: timestampSeconds(match[2]),
  };
}

function normalizeText(value) {
  if (typeof value !== 'string') throw new TypeError('caption text must be a string');
  const text = value.replace(/\r\n?/g, '\n').trim();
  if (!text) throw new Error('caption text must not be empty');
  return text;
}

function finiteNumber(value, label) {
  const number = Number(value);
  if (!Number.isFinite(number)) throw new TypeError(`${label} must be a finite number`);
  return number;
}

function timeValue(item, secondsKey, shortKey, millisecondsKey) {
  if (item[secondsKey] !== undefined) return finiteNumber(item[secondsKey], secondsKey);
  if (item[shortKey] !== undefined) return finiteNumber(item[shortKey], shortKey);
  if (item[millisecondsKey] !== undefined) return finiteNumber(item[millisecondsKey], millisecondsKey) / 1000;
  return undefined;
}

function normalizeWord(raw, index) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new TypeError(`words[${index}] must be an object`);
  }
  const text = normalizeText(raw.text ?? raw.word ?? raw.token ?? '');
  const startSeconds = timeValue(raw, 'startSeconds', 'start', 'startMs');
  const endSeconds = timeValue(raw, 'endSeconds', 'end', 'endMs');
  if (startSeconds === undefined || endSeconds === undefined) {
    throw new Error(`words[${index}] requires start/end seconds (or startMs/endMs)`);
  }
  if (startSeconds < 0 || endSeconds <= startSeconds) {
    throw new Error(`words[${index}] must satisfy 0 <= start < end`);
  }
  return { text, startSeconds, endSeconds };
}

function joinWordText(words) {
  let out = '';
  for (const { text } of words) {
    if (!out || /^[,.;:!?%\]\)}'’…]/u.test(text) || /[\[({'‘“]$/u.test(out)) out += text;
    else out += ` ${text}`;
  }
  return out;
}

export function groupWords(rawWords, options = {}) {
  if (!Array.isArray(rawWords) || rawWords.length === 0) {
    throw new Error('words must contain at least one timed word');
  }
  const maxWords = options.maxWords ?? 8;
  const maxCharacters = options.maxCharacters ?? 42;
  const maxDurationSeconds = options.maxDurationSeconds ?? 3.5;
  const maxGapSeconds = options.maxGapSeconds ?? 0.8;
  for (const [label, value] of Object.entries({ maxWords, maxCharacters, maxDurationSeconds, maxGapSeconds })) {
    if (!Number.isFinite(value) || value <= 0) throw new Error(`${label} must be greater than zero`);
  }

  const words = rawWords.map(normalizeWord);
  for (let i = 1; i < words.length; i += 1) {
    if (words[i].startSeconds < words[i - 1].startSeconds) {
      throw new Error(`words must be ordered by start time (index ${i} is earlier than index ${i - 1})`);
    }
  }

  const groups = [];
  let current = [];
  const flush = () => {
    if (!current.length) return;
    groups.push({
      startSeconds: current[0].startSeconds,
      endSeconds: current.at(-1).endSeconds,
      text: joinWordText(current),
      words: current,
    });
    current = [];
  };
  for (const word of words) {
    const candidate = [...current, word];
    const gap = current.length ? word.startSeconds - current.at(-1).endSeconds : 0;
    const duration = candidate.at(-1).endSeconds - candidate[0].startSeconds;
    const shouldBreak =
      current.length > 0 &&
      (gap > maxGapSeconds || candidate.length > maxWords || joinWordText(candidate).length > maxCharacters || duration > maxDurationSeconds);
    if (shouldBreak) flush();
    current.push(word);
  }
  flush();
  return groups;
}

export function normalizeCues(rawCues, options = {}) {
  if (!Array.isArray(rawCues) || rawCues.length === 0) throw new Error('cues must contain at least one caption');
  const cues = rawCues.map((raw, index) => {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      throw new TypeError(`cues[${index}] must be an object`);
    }
    const startSeconds = timeValue(raw, 'startSeconds', 'start', 'startMs');
    const endSeconds = timeValue(raw, 'endSeconds', 'end', 'endMs');
    if (startSeconds === undefined || endSeconds === undefined) {
      throw new Error(`cues[${index}] requires start/end seconds (or startMs/endMs)`);
    }
    if (startSeconds < 0 || endSeconds <= startSeconds) {
      throw new Error(`cues[${index}] must satisfy 0 <= start < end`);
    }
    const cue = {
      startSeconds,
      endSeconds,
      text: normalizeText(raw.text ?? raw.caption ?? ''),
    };
    if (raw.id !== undefined) cue.id = String(raw.id);
    if (raw.words !== undefined) cue.words = raw.words.map(normalizeWord);
    return cue;
  });
  cues.sort((a, b) => a.startSeconds - b.startSeconds || a.endSeconds - b.endSeconds);
  if (!options.allowOverlaps) {
    for (let i = 1; i < cues.length; i += 1) {
      if (cues[i].startSeconds < cues[i - 1].endSeconds) {
        throw new Error(`captions overlap at normalized cue ${i + 1}; pass allowOverlaps=true only if the target track supports it`);
      }
    }
  }
  return cues;
}

function parseTimedText(content, format) {
  const normalized = String(content)
    .replace(/^\uFEFF/, '')
    .replace(/\r\n?/g, '\n');
  const body = format === 'vtt' ? normalized.replace(/^WEBVTT[^\n]*\n?/, '') : normalized;
  const blocks = body
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean);
  const cues = [];
  for (const block of blocks) {
    if (format === 'vtt' && /^(NOTE|STYLE|REGION)(?:\s|$)/.test(block)) continue;
    const lines = block.split('\n');
    let timingIndex = lines.findIndex((line) => line.includes('-->'));
    if (timingIndex < 0) continue;
    const timing = parseTiming(lines[timingIndex], format.toUpperCase(), cues.length);
    const text = lines
      .slice(timingIndex + 1)
      .join('\n')
      .trim();
    if (!text) throw new Error(`${format.toUpperCase()}: cue ${cues.length + 1} has no text`);
    const cue = { ...timing, text };
    if (timingIndex > 0) cue.id = lines.slice(0, timingIndex).join(' ').trim();
    cues.push(cue);
  }
  if (!cues.length) throw new Error(`${format.toUpperCase()}: no caption cues found`);
  return cues;
}

function inferFormat({ inputPath, content, inputFormat }) {
  if (inputFormat) return inputFormat.toLowerCase();
  if (inputPath) {
    const ext = path.extname(inputPath).slice(1).toLowerCase();
    if (['srt', 'vtt', 'json'].includes(ext)) return ext;
  }
  const trimmed = String(content ?? '').trimStart();
  if (trimmed.startsWith('WEBVTT')) return 'vtt';
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) return 'json';
  return 'srt';
}

function sourceCount(input) {
  return ['inputPath', 'content', 'cues', 'words', 'segments'].filter((key) => input[key] !== undefined).length;
}

export async function parseCaptionSource(input = {}) {
  if (sourceCount(input) !== 1) {
    throw new Error('provide exactly one caption source: inputPath, content, cues, words, or segments');
  }
  const grouping = {
    maxWords: input.maxWords,
    maxCharacters: input.maxCharacters,
    maxDurationSeconds: input.maxDurationSeconds,
    maxGapSeconds: input.maxGapSeconds,
  };
  let cues;
  let inputFormat = input.inputFormat;
  if (input.cues) {
    cues = input.cues;
    inputFormat = 'json';
  } else if (input.words) {
    cues = groupWords(input.words, grouping);
    inputFormat = 'json-words';
  } else if (input.segments) {
    cues = input.segments.map((segment) => ({
      ...segment,
      text: segment.text ?? (Array.isArray(segment.words) ? joinWordText(segment.words.map(normalizeWord)) : undefined),
    }));
    inputFormat = 'json-segments';
  } else {
    const content = input.content ?? (await fs.readFile(input.inputPath, 'utf8'));
    const format = inferFormat({ inputPath: input.inputPath, content, inputFormat: input.inputFormat });
    inputFormat = format;
    if (format === 'srt' || format === 'vtt') {
      cues = parseTimedText(content, format);
    } else if (format === 'json') {
      let parsed;
      try {
        parsed = JSON.parse(content);
      } catch (error) {
        throw new Error(`JSON captions: ${error.message}`);
      }
      if (Array.isArray(parsed)) cues = parsed;
      else if (Array.isArray(parsed.cues)) cues = parsed.cues;
      else if (Array.isArray(parsed.words)) cues = groupWords(parsed.words, grouping);
      else if (Array.isArray(parsed.segments))
        cues = parsed.segments.map((segment) => ({
          ...segment,
          text: segment.text ?? (Array.isArray(segment.words) ? joinWordText(segment.words.map(normalizeWord)) : undefined),
        }));
      else throw new Error('JSON captions must be an array or contain cues, words, or segments');
    } else {
      throw new Error(`unsupported caption input format: ${format}`);
    }
  }
  return {
    inputFormat,
    cues: normalizeCues(cues, { allowOverlaps: input.allowOverlaps }),
  };
}

function formatTimestamp(seconds, separator) {
  const totalMs = Math.round(seconds * 1000);
  const ms = totalMs % 1000;
  const totalSeconds = Math.floor(totalMs / 1000);
  const s = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const m = totalMinutes % 60;
  const h = Math.floor(totalMinutes / 60);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}${separator}${String(ms).padStart(3, '0')}`;
}

export function renderCaptions(cues, format = 'srt') {
  const normalized = normalizeCues(cues);
  if (format === 'json') return `${JSON.stringify({ cues: normalized }, null, 2)}\n`;
  if (format === 'srt') {
    return `${normalized.map((cue, index) => `${cue.id || index + 1}\n${formatTimestamp(cue.startSeconds, ',')} --> ${formatTimestamp(cue.endSeconds, ',')}\n${cue.text}`).join('\n\n')}\n`;
  }
  if (format === 'vtt') {
    return `WEBVTT\n\n${normalized.map((cue) => `${cue.id ? `${cue.id}\n` : ''}${formatTimestamp(cue.startSeconds, '.')} --> ${formatTimestamp(cue.endSeconds, '.')}\n${cue.text}`).join('\n\n')}\n`;
  }
  throw new Error(`unsupported caption output format: ${format}`);
}

export function cuesToSubtitleClips(cues, { frameRate, offsetFrames = 0 } = {}) {
  if (!Number.isFinite(frameRate) || frameRate <= 0) throw new Error('frameRate must be greater than zero');
  if (!Number.isInteger(offsetFrames)) throw new Error('offsetFrames must be an integer');
  return normalizeCues(cues).map((cue) => {
    const startFrame = offsetFrames + Math.round(cue.startSeconds * frameRate);
    const endFrame = offsetFrames + Math.round(cue.endSeconds * frameRate);
    if (startFrame < 0) throw new Error('offsetFrames places a caption before timeline frame zero');
    return {
      text: cue.text,
      startFrame,
      durationFrames: Math.max(1, endFrame - startFrame),
    };
  });
}

export async function writeCaptionFile(outputPath, content) {
  await fs.writeFile(outputPath, content, 'utf8');
  return Buffer.byteLength(content);
}
