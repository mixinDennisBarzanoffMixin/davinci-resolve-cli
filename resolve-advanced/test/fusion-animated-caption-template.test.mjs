import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';

import { fusionTool } from '../server/tools/fusion.mjs';

const require = createRequire(import.meta.url);
const fusion = require('../vendor/fusion-codec/composition-generator.js');

test('animated caption setting is a reusable title macro with Duration AnimCurves', () => {
  const out = fusion.generateAnimatedCaptionSetting({
    assetName: 'Social Caption 01',
    text: 'Hello "world"\nnext line',
    font: 'Montserrat',
    fontStyle: 'SemiBold',
    size: 0.09,
    textColor: '#FFCC00',
    strokeColor: { r: 0.1, g: 0.2, b: 0.3, a: 0.9 },
    strokeWidth: 0.06,
    shadowColor: '#11223399',
    shadowSoftness: 0.08,
    position: { x: 0.01, y: 0.99 },
    safeMargin: 0.1,
    entrance: 'pop',
    entranceFraction: 0.25,
  });

  assert.equal(out.validation.valid, true);
  assert.equal(out.manifest.artifactKind, 'fusion-title-template-setting');
  assert.equal(out.manifest.position.x, 0.1, 'custom x is clamped to title-safe margin');
  assert.equal(out.manifest.position.y, 0.9, 'custom y is clamped to title-safe margin');
  assert.equal(out.manifest.animation.durationAdaptive, true);
  assert.equal(out.manifest.animation.modifier, 'AnimCurves');
  assert.equal(out.manifest.nativeSubtitleTrack, false);
  assert.equal(out.manifest.resolveRenderValidated, false);
  assert.match(out.settingContent, /Social_Caption_01 = MacroOperator/);
  assert.match(out.settingContent, /CaptionText = TextPlus/);
  assert.match(out.settingContent, /SourceOp = "EntranceSize", Source = "Value"/);
  assert.match(out.settingContent, /SourceOp = "EntranceOpacity", Source = "Value"/);
  assert.match(out.settingContent, /Value = FuID \{ "Duration" \}/);
  assert.match(out.settingContent, /TimeScale = Input \{ Value = 4, \}/);
  assert.match(out.settingContent, /Value = "Hello \\"world\\"\\nnext line"/);
  assert.match(out.settingContent, /FillRed = InstanceInput/);
  assert.match(out.settingContent, /StrokeWidth = InstanceInput/);
  assert.match(out.settingContent, /ShadowOffset = InstanceInput/);
});

test('none entrance emits a static, still reusable title template', () => {
  const out = fusion.generateAnimatedCaptionSetting({ entrance: 'none', position: 'upper-right' });
  assert.equal(out.validation.valid, true);
  assert.equal(out.validation.durationAdaptiveAnimation, false);
  assert.equal(out.manifest.animation.durationAdaptive, false);
  assert.doesNotMatch(out.settingContent, /= AnimCurves \{/);
  assert.match(out.settingContent, /Center = Input \{ Value = \{ 0\.84, 0\.86 \}, \}/);
  assert.match(out.settingContent, /Blend = Input \{ Value = 1, \}/);
});

test('macro names are normalized to valid Fusion identifiers', () => {
  const out = fusion.generateAnimatedCaptionSetting({ assetName: '123 social / caption!' });
  assert.equal(out.manifest.macroName, '_123_social_caption');
  assert.match(out.settingContent, /_123_social_caption = MacroOperator/);
});

test('title setting validator is conservative and declares its verification limits', () => {
  const generated = fusion.generateAnimatedCaptionSetting({ entrance: 'fade' });
  const valid = fusion.validateAnimatedCaptionSetting(generated.settingContent);
  assert.equal(valid.valid, true);
  assert.equal(valid.resolveImportValidated, false);
  assert.equal(valid.resolveRenderValidated, false);
  assert.match(valid.warnings.join(' '), /does not prove Resolve import/i);

  const invalid = fusion.validateAnimatedCaptionSetting('{ Tools = ordered() { Bad = TextPlus { } }');
  assert.equal(invalid.valid, false);
  assert.ok(invalid.errors.some((e) => /unclosed brace/.test(e)));
  assert.ok(invalid.errors.some((e) => /MacroOperator/.test(e)));
  assert.ok(invalid.errors.some((e) => /MediaOut/.test(e)));
});

test('fusion tool generates, writes, validates, and describes animated caption templates', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fusion-caption-setting-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const outputPath = path.join(dir, 'Social Caption.setting');

  const written = await fusionTool.handler({
    action: 'generate_animated_caption_template',
    args: {
      outputPath,
      text: 'CLI CAPTION',
      entrance: 'punch',
      textColor: '#00FFAA',
      shadowEnabled: false,
    },
  });
  assert.equal(written.outputPath, outputPath);
  assert.ok(written.bytes > 1000);
  assert.equal(written.validation.valid, true);
  assert.equal(written.manifest.nativeAnimatedSubtitlesEffect, false);
  assert.equal(fs.existsSync(outputPath), true);

  const checked = await fusionTool.handler({
    action: 'validate_title_template',
    args: { settingPath: outputPath },
  });
  assert.equal(checked.valid, true);

  const presets = await fusionTool.handler({ action: 'list_animated_caption_presets', args: {} });
  assert.ok(presets.positions.some((p) => p.name === 'lower-center'));
  assert.ok(presets.entrances.some((p) => p.name === 'pop'));
});

test('animated caption template rejects unsafe or ambiguous inputs', async () => {
  assert.throws(() => fusion.generateAnimatedCaptionSetting({ safeMargin: 0.6 }), /safeMargin must be/);
  assert.throws(() => fusion.generateAnimatedCaptionSetting({ textColor: '#not-a-color' }), /textColor must be/);
  await assert.rejects(
    () =>
      fusionTool.handler({
        action: 'generate_animated_caption_template',
        args: { outputPath: '/tmp/not-a-setting.comp' },
      }),
    /must end in \.setting/,
  );
  await assert.rejects(
    () =>
      fusionTool.handler({
        action: 'validate_title_template',
        args: { content: '{}', settingPath: '/tmp/x.setting' },
      }),
    /provide exactly one/,
  );
});
