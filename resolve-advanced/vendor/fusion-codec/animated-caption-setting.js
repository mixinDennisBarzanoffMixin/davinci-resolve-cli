/**
 * Offline Fusion title-template (.setting) generator for animated captions.
 *
 * The artifact is a reusable MacroOperator intended for Resolve's
 * Fusion/Templates/Edit/Titles directory.  This module only authors and
 * structurally validates plaintext Fusion settings; it does not claim that
 * Resolve imported or rendered the result.
 */

'use strict';

const POSITION_PRESETS = Object.freeze({
  'lower-left': { x: 0.16, y: 0.14 },
  'lower-center': { x: 0.5, y: 0.14 },
  'lower-right': { x: 0.84, y: 0.14 },
  center: { x: 0.5, y: 0.5 },
  'upper-left': { x: 0.16, y: 0.86 },
  'upper-center': { x: 0.5, y: 0.86 },
  'upper-right': { x: 0.84, y: 0.86 },
});

const ENTRANCE_PRESETS = Object.freeze({
  none: { animateOpacity: false, animateSize: false, startScale: 1, easing: 'Sine' },
  fade: { animateOpacity: true, animateSize: false, startScale: 1, easing: 'Sine' },
  pop: { animateOpacity: true, animateSize: true, startScale: 0.82, easing: 'Back' },
  punch: { animateOpacity: true, animateSize: true, startScale: 0.68, easing: 'Bounce' },
});

function finite(name, value, { min = -Infinity, max = Infinity } = {}) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < min || n > max) {
    throw new Error(`${name} must be a finite number from ${min} to ${max}`);
  }
  return n;
}

function bool(value, fallback) {
  return value === undefined ? fallback : Boolean(value);
}

function luaString(value) {
  return `"${String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\r/g, '\\r').replace(/\n/g, '\\n')}"`;
}

function identifier(value) {
  let cleaned = String(value || 'AnimatedCaption')
    .normalize('NFKD')
    .replace(/[^A-Za-z0-9_]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/_+$/g, '');
  if (cleaned && !/^[A-Za-z_]/.test(cleaned)) cleaned = `_${cleaned}`;
  return cleaned || 'AnimatedCaption';
}

function color(value, fallback, name) {
  if (typeof value === 'string') {
    const m = value.trim().match(/^#([0-9a-f]{6}|[0-9a-f]{8})$/i);
    if (!m) throw new Error(`${name} must be #RRGGBB, #RRGGBBAA, or an RGB(A) object`);
    const hex = m[1];
    return {
      r: parseInt(hex.slice(0, 2), 16) / 255,
      g: parseInt(hex.slice(2, 4), 16) / 255,
      b: parseInt(hex.slice(4, 6), 16) / 255,
      a: hex.length === 8 ? parseInt(hex.slice(6, 8), 16) / 255 : 1,
    };
  }
  const source = value === undefined ? fallback : value;
  if (!source || typeof source !== 'object' || Array.isArray(source)) {
    throw new Error(`${name} must be #RRGGBB, #RRGGBBAA, or an RGB(A) object`);
  }
  return {
    r: finite(`${name}.r`, source.r, { min: 0, max: 1 }),
    g: finite(`${name}.g`, source.g, { min: 0, max: 1 }),
    b: finite(`${name}.b`, source.b, { min: 0, max: 1 }),
    a: finite(`${name}.a`, source.a ?? fallback.a ?? 1, { min: 0, max: 1 }),
  };
}

function resolvePosition(position, safeMargin) {
  let point;
  let preset = null;
  if (typeof position === 'string' || position === undefined) {
    preset = position || 'lower-center';
    point = POSITION_PRESETS[preset];
    if (!point) {
      throw new Error(`position must be one of ${Object.keys(POSITION_PRESETS).join(', ')}, or {x,y}`);
    }
  } else if (position && typeof position === 'object' && !Array.isArray(position)) {
    point = {
      x: finite('position.x', position.x, { min: 0, max: 1 }),
      y: finite('position.y', position.y, { min: 0, max: 1 }),
    };
  } else {
    throw new Error('position must be a preset name or {x,y}');
  }
  return {
    x: Math.min(1 - safeMargin, Math.max(safeMargin, point.x)),
    y: Math.min(1 - safeMargin, Math.max(safeMargin, point.y)),
    preset,
  };
}

function input(value) {
  return `Input { Value = ${value}, }`;
}

function instanceInput(name, sourceOp, source, page = 'Caption') {
  return `${name} = InstanceInput { SourceOp = ${luaString(sourceOp)}, Source = ${luaString(source)}, Page = ${luaString(page)}, },`;
}

function animCurves(name, { startValue = 0, endValue = 1, easing, timeScale }) {
  const scale = Number((endValue - startValue).toFixed(12));
  const normalizedTimeScale = Number(timeScale.toFixed(12));
  return `\t\t\t${name} = AnimCurves {
\t\t\t\tCtrlWZoom = false,
\t\t\t\tInputs = {
\t\t\t\t\tSource = ${input('FuID { "Duration" }')},
\t\t\t\t\tCurve = ${input('FuID { "Easing" }')},
\t\t\t\t\tEaseIn = ${input(`FuID { ${luaString(easing)} }`)},
\t\t\t\t\tEaseOut = ${input(`FuID { ${luaString(easing)} }`)},
\t\t\t\t\tScale = ${input(scale)},
\t\t\t\t\tOffset = ${input(startValue)},
\t\t\t\t\tTimeScale = ${input(normalizedTimeScale)},
\t\t\t\t},
\t\t\t},`;
}

/** Generate a reusable animated-caption Fusion title macro. */
function generateAnimatedCaptionSetting(params = {}) {
  const assetName = String(params.assetName || 'CLI Animated Caption');
  const macroName = identifier(params.macroName || assetName);
  const text = String(params.text ?? 'Animated caption');
  const font = String(params.font || 'Open Sans');
  const fontStyle = String(params.fontStyle || 'Bold');
  const size = finite('size', params.size ?? 0.075, { min: 0.005, max: 1 });
  const safeMargin = finite('safeMargin', params.safeMargin ?? 0.08, { min: 0, max: 0.45 });
  const pos = resolvePosition(params.position, safeMargin);
  const fill = color(params.textColor, { r: 1, g: 1, b: 1, a: 1 }, 'textColor');
  const stroke = color(params.strokeColor, { r: 0, g: 0, b: 0, a: 1 }, 'strokeColor');
  const shadow = color(params.shadowColor, { r: 0, g: 0, b: 0, a: 0.72 }, 'shadowColor');
  const strokeEnabled = bool(params.strokeEnabled, true);
  const strokeWidth = finite('strokeWidth', params.strokeWidth ?? 0.045, { min: 0, max: 0.5 });
  const shadowEnabled = bool(params.shadowEnabled, true);
  const shadowSoftness = finite('shadowSoftness', params.shadowSoftness ?? 0.035, { min: 0, max: 1 });
  const shadowOffset = params.shadowOffset ?? { x: 0.012, y: -0.012 };
  const shadowX = finite('shadowOffset.x', shadowOffset.x, { min: -1, max: 1 });
  const shadowY = finite('shadowOffset.y', shadowOffset.y, { min: -1, max: 1 });
  const entrance = String(params.entrance || 'pop');
  const entrancePreset = ENTRANCE_PRESETS[entrance];
  if (!entrancePreset) throw new Error(`entrance must be one of ${Object.keys(ENTRANCE_PRESETS).join(', ')}`);
  const entranceFraction = finite('entranceFraction', params.entranceFraction ?? 0.2, { min: 0.02, max: 1 });
  const timeScale = 1 / entranceFraction;
  const easing = String(params.easing || entrancePreset.easing);
  if (!/^[A-Za-z][A-Za-z0-9 _-]*$/.test(easing)) throw new Error('easing contains unsupported characters');
  const startScale = finite('startScale', params.startScale ?? entrancePreset.startScale, { min: 0, max: 2 });

  const transformSize = entrancePreset.animateSize ? 'Input { SourceOp = "EntranceSize", Source = "Value", }' : input('1');
  const mergeBlend = entrancePreset.animateOpacity ? 'Input { SourceOp = "EntranceOpacity", Source = "Value", }' : input('1');
  const modifiers = [];
  if (entrancePreset.animateSize) {
    modifiers.push(animCurves('EntranceSize', { startValue: startScale, endValue: 1, easing, timeScale }));
  }
  if (entrancePreset.animateOpacity) {
    modifiers.push(animCurves('EntranceOpacity', { startValue: 0, endValue: 1, easing: 'Sine', timeScale }));
  }

  const settingContent = `-- Generated offline by davinci-resolve-cli. Structural validation only; Resolve import/render not verified.
{
\tTools = ordered() {
\t\t${macroName} = MacroOperator {
\t\t\tInputs = ordered() {
\t\t\t\t${instanceInput('CaptionText', 'CaptionText', 'StyledText')}
\t\t\t\t${instanceInput('Font', 'CaptionText', 'Font')}
\t\t\t\t${instanceInput('FontStyle', 'CaptionText', 'Style')}
\t\t\t\t${instanceInput('Size', 'CaptionText', 'Size')}
\t\t\t\t${instanceInput('SafePosition', 'CaptionTransform', 'Center', 'Layout')}
\t\t\t\t${instanceInput('FillRed', 'CaptionText', 'Red1', 'Fill')}
\t\t\t\t${instanceInput('FillGreen', 'CaptionText', 'Green1', 'Fill')}
\t\t\t\t${instanceInput('FillBlue', 'CaptionText', 'Blue1', 'Fill')}
\t\t\t\t${instanceInput('FillAlpha', 'CaptionText', 'Alpha1', 'Fill')}
\t\t\t\t${instanceInput('StrokeEnabled', 'CaptionText', 'Enabled2', 'Stroke')}
\t\t\t\t${instanceInput('StrokeWidth', 'CaptionText', 'Thickness2', 'Stroke')}
\t\t\t\t${instanceInput('StrokeRed', 'CaptionText', 'Red2', 'Stroke')}
\t\t\t\t${instanceInput('StrokeGreen', 'CaptionText', 'Green2', 'Stroke')}
\t\t\t\t${instanceInput('StrokeBlue', 'CaptionText', 'Blue2', 'Stroke')}
\t\t\t\t${instanceInput('StrokeAlpha', 'CaptionText', 'Alpha2', 'Stroke')}
\t\t\t\t${instanceInput('ShadowEnabled', 'CaptionText', 'Enabled3', 'Shadow')}
\t\t\t\t${instanceInput('ShadowRed', 'CaptionText', 'Red3', 'Shadow')}
\t\t\t\t${instanceInput('ShadowGreen', 'CaptionText', 'Green3', 'Shadow')}
\t\t\t\t${instanceInput('ShadowBlue', 'CaptionText', 'Blue3', 'Shadow')}
\t\t\t\t${instanceInput('ShadowOpacity', 'CaptionText', 'Alpha3', 'Shadow')}
\t\t\t\t${instanceInput('ShadowOffset', 'CaptionText', 'Offset3', 'Shadow')}
\t\t\t\t${instanceInput('ShadowSoftness', 'CaptionText', 'SoftnessX3', 'Shadow')}
\t\t\t},
\t\t\tOutputs = ordered() {
\t\t\t\tMainOutput1 = InstanceOutput { SourceOp = "MediaOut1", Source = "Output", },
\t\t\t},
\t\t\tViewInfo = GroupInfo { Pos = { 0, 0 }, },
\t\t\tTools = ordered() {
\t\t\tTransparentBG = Background {
\t\t\t\tInputs = {
\t\t\t\t\tTopLeftAlpha = ${input('0')},
\t\t\t\t\tUseFrameFormatSettings = ${input('1')},
\t\t\t\t},
\t\t\t},
\t\t\tCaptionText = TextPlus {
\t\t\t\tInputs = {
\t\t\t\t\tStyledText = ${input(luaString(text))},
\t\t\t\t\tFont = ${input(luaString(font))},
\t\t\t\t\tStyle = ${input(luaString(fontStyle))},
\t\t\t\t\tSize = ${input(size)},
\t\t\t\t\tHorizontalJustificationNew = ${input('1')},
\t\t\t\t\tVerticalJustificationNew = ${input('1')},
\t\t\t\t\tRed1 = ${input(fill.r)}, Green1 = ${input(fill.g)}, Blue1 = ${input(fill.b)}, Alpha1 = ${input(fill.a)},
\t\t\t\t\tEnabled2 = ${input(strokeEnabled ? '1' : '0')},
\t\t\t\t\tRed2 = ${input(stroke.r)}, Green2 = ${input(stroke.g)}, Blue2 = ${input(stroke.b)}, Alpha2 = ${input(stroke.a)},
\t\t\t\t\tThickness2 = ${input(strokeWidth)}, OutsideOnly2 = ${input('1')},
\t\t\t\t\tEnabled3 = ${input(shadowEnabled ? '1' : '0')},
\t\t\t\t\tRed3 = ${input(shadow.r)}, Green3 = ${input(shadow.g)}, Blue3 = ${input(shadow.b)}, Alpha3 = ${input(shadow.a)},
\t\t\t\t\tOffset3 = ${input(`{ ${shadowX}, ${shadowY} }`)},
\t\t\t\t\tSoftnessX3 = ${input(shadowSoftness)}, SoftnessY3 = ${input(shadowSoftness)},
\t\t\t\t},
\t\t\t},
\t\t\tCaptionTransform = Transform {
\t\t\t\tInputs = {
\t\t\t\t\tInput = Input { SourceOp = "CaptionText", Source = "Output", },
\t\t\t\t\tCenter = ${input(`{ ${pos.x}, ${pos.y} }`)},
\t\t\t\t\tSize = ${transformSize},
\t\t\t\t},
\t\t\t},
\t\t\tCaptionMerge = Merge {
\t\t\t\tInputs = {
\t\t\t\t\tBackground = Input { SourceOp = "TransparentBG", Source = "Output", },
\t\t\t\t\tForeground = Input { SourceOp = "CaptionTransform", Source = "Output", },
\t\t\t\t\tBlend = ${mergeBlend},
\t\t\t\t},
\t\t\t},
${modifiers.join('\n')}${modifiers.length ? '\n' : ''}\t\t\tMediaOut1 = MediaOut {
\t\t\t\tInputs = { Input = Input { SourceOp = "CaptionMerge", Source = "Output", }, },
\t\t\t},
\t\t\t},
\t\t},
\t},
\tActiveTool = ${luaString(macroName)},
}`;

  const validation = validateAnimatedCaptionSetting(settingContent);
  if (!validation.valid) {
    throw new Error(`generated Fusion setting failed structural validation: ${validation.errors.join('; ')}`);
  }
  return {
    settingContent,
    manifest: {
      artifactKind: 'fusion-title-template-setting',
      assetName,
      macroName,
      position: { x: pos.x, y: pos.y, preset: pos.preset, safeMargin },
      style: { font, fontStyle, size, fill, strokeEnabled, strokeWidth, shadowEnabled },
      animation: {
        entrance,
        durationAdaptive: entrance !== 'none',
        modifier: entrance === 'none' ? null : 'AnimCurves',
        source: entrance === 'none' ? null : 'Duration',
        entranceFraction,
        easing,
      },
      nativeSubtitleTrack: false,
      nativeAnimatedSubtitlesEffect: false,
      resolveImportValidated: false,
      resolveRenderValidated: false,
    },
    validation,
  };
}

function bracesReport(content) {
  let depth = 0;
  let quote = false;
  let escape = false;
  let lineComment = false;
  for (let i = 0; i < content.length; i += 1) {
    const ch = content[i];
    const next = content[i + 1];
    if (lineComment) {
      if (ch === '\n') lineComment = false;
      continue;
    }
    if (quote) {
      if (escape) escape = false;
      else if (ch === '\\') escape = true;
      else if (ch === '"') quote = false;
      continue;
    }
    if (ch === '-' && next === '-') {
      lineComment = true;
      i += 1;
    } else if (ch === '"') quote = true;
    else if (ch === '{') depth += 1;
    else if (ch === '}') {
      depth -= 1;
      if (depth < 0) return { balanced: false, reason: 'closing brace without opening brace' };
    }
  }
  if (quote) return { balanced: false, reason: 'unterminated string' };
  return { balanced: depth === 0, reason: depth === 0 ? null : `${depth} unclosed brace(s)` };
}

/** Conservative structural validator for an offline generated title setting. */
function validateAnimatedCaptionSetting(content) {
  const text = typeof content === 'string' ? content : '';
  const errors = [];
  const warnings = [];
  if (!text.trim()) errors.push('setting is empty');
  const braces = bracesReport(text);
  if (!braces.balanced) errors.push(braces.reason);
  const required = [
    ['Tools = ordered()', /Tools\s*=\s*ordered\(\)\s*\{/],
    ['MacroOperator', /=\s*MacroOperator\s*\{/],
    ['TextPlus', /=\s*TextPlus\s*\{/],
    ['MediaOut', /=\s*MediaOut\s*\{/],
    ['macro output', /InstanceOutput\s*\{[^}]*SourceOp\s*=\s*"MediaOut1"/s],
    ['caption text control', /SourceOp\s*=\s*"CaptionText"\s*,\s*Source\s*=\s*"StyledText"/s],
  ];
  for (const [label, pattern] of required) if (!pattern.test(text)) errors.push(`missing ${label}`);
  const adaptive = /=\s*AnimCurves\s*\{/.test(text);
  if (adaptive && !/Source\s*=\s*Input\s*\{\s*Value\s*=\s*FuID\s*\{\s*"Duration"/s.test(text)) {
    errors.push('AnimCurves modifier is not driven by Duration');
  }
  if (/=\s*MediaIn\s*\{/.test(text)) warnings.push('template contains MediaIn; it is not a title-only generator');
  warnings.push('structural validation does not prove Resolve import, Inspector behavior, or rendered output');
  return {
    valid: errors.length === 0,
    errors,
    warnings,
    artifactKind: 'fusion-title-template-setting',
    durationAdaptiveAnimation: adaptive,
    nativeSubtitleTrack: false,
    nativeAnimatedSubtitlesEffect: false,
    resolveImportValidated: false,
    resolveRenderValidated: false,
  };
}

function listAnimatedCaptionSettingPresets() {
  return {
    positions: Object.entries(POSITION_PRESETS).map(([name, point]) => ({ name, ...point })),
    entrances: Object.entries(ENTRANCE_PRESETS).map(([name, preset]) => ({ name, ...preset })),
    artifactKind: 'fusion-title-template-setting',
  };
}

module.exports = {
  generateAnimatedCaptionSetting,
  validateAnimatedCaptionSetting,
  listAnimatedCaptionSettingPresets,
  POSITION_PRESETS,
  ENTRANCE_PRESETS,
};
