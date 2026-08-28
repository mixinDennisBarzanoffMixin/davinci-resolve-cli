/**
 * fusion tool — declarative Fusion composition (.comp) authoring (offline).
 *
 * generate — spec →.comp text (optionally written to outputPath)
 * generate_from_template — named template + params →.comp
 * list_templates — available built-in templates
 * to_api_calls — spec → equivalent davinci-resolve-mcp Fusion API calls
 * generate_animated_caption_template — reusable offline Edit title .setting
 * validate_title_template — conservative structural check for a .setting
 * list_animated_caption_presets — supported safe positions and entrances
 */

import fs from 'node:fs/promises';
import { z } from 'zod';
import { fusion } from '../libs.mjs';

const generateSchema = z.object({
  spec: z.object({}).passthrough().describe('Composition spec: { nodes, connections,... }'),
  outputPath: z.string().optional().describe('If set, write the.comp here'),
});
const fromTemplateSchema = z.object({
  templateName: z.string(),
  params: z.object({}).passthrough().optional(),
  outputPath: z.string().optional(),
});
const toApiSchema = z.object({ spec: z.object({}).passthrough() });
const animatedCaptionTemplateSchema = z.object({
  outputPath: z.string().optional().describe('Destination .setting path'),
  assetName: z.string().optional(),
  macroName: z.string().optional(),
  text: z.string().optional(),
  font: z.string().optional(),
  fontStyle: z.string().optional(),
  size: z.number().optional(),
  textColor: z.union([z.string(), z.object({}).passthrough()]).optional(),
  strokeEnabled: z.boolean().optional(),
  strokeColor: z.union([z.string(), z.object({}).passthrough()]).optional(),
  strokeWidth: z.number().optional(),
  shadowEnabled: z.boolean().optional(),
  shadowColor: z.union([z.string(), z.object({}).passthrough()]).optional(),
  shadowSoftness: z.number().optional(),
  shadowOffset: z.object({ x: z.number(), y: z.number() }).optional(),
  position: z.union([z.string(), z.object({ x: z.number(), y: z.number() })]).optional(),
  safeMargin: z.number().optional(),
  entrance: z.enum(['none', 'fade', 'pop', 'punch']).optional(),
  entranceFraction: z.number().optional(),
  easing: z.string().optional(),
  startScale: z.number().optional(),
});
const validateTitleTemplateSchema = z
  .object({
    content: z.string().optional(),
    settingPath: z.string().optional(),
  })
  .refine((p) => Boolean(p.content) !== Boolean(p.settingPath), {
    message: 'provide exactly one of content or settingPath',
  });

function asText(out) {
  return typeof out === 'string' ? out : out?.comp || out?.content || out?.text || JSON.stringify(out);
}

export const fusionTool = {
  name: 'fusion',
  description:
    'Declarative Fusion composition and reusable title-template authoring — offline, no Resolve required. Actions: generate, generate_from_template, list_templates, to_api_calls, generate_animated_caption_template, validate_title_template, list_animated_caption_presets.',
  async handler({ action, args }) {
    const fx = fusion();
    if (action === 'generate') {
      const p = generateSchema.parse(args);
      const out = fx.generateComp(p.spec);
      const text = asText(out);
      if (p.outputPath) {
        await fs.writeFile(p.outputPath, text);
        return { outputPath: p.outputPath, bytes: Buffer.byteLength(text) };
      }
      return { comp: text };
    }
    if (action === 'generate_from_template') {
      const p = fromTemplateSchema.parse(args);
      const out = fx.generateFromTemplate(p.templateName, p.params || {});
      const text = asText(out);
      if (p.outputPath) {
        await fs.writeFile(p.outputPath, text);
        return { outputPath: p.outputPath, bytes: Buffer.byteLength(text) };
      }
      return { comp: text };
    }
    if (action === 'list_templates') {
      return { templates: fx.listTemplates() };
    }
    if (action === 'to_api_calls') {
      const p = toApiSchema.parse(args);
      return { apiCalls: fx.specToApiCalls(p.spec) };
    }
    if (action === 'generate_animated_caption_template') {
      const p = animatedCaptionTemplateSchema.parse(args);
      if (p.outputPath && !p.outputPath.toLowerCase().endsWith('.setting')) {
        throw new Error('outputPath must end in .setting');
      }
      const out = fx.generateAnimatedCaptionSetting(p);
      if (p.outputPath) {
        await fs.writeFile(p.outputPath, out.settingContent, 'utf8');
        return {
          outputPath: p.outputPath,
          bytes: Buffer.byteLength(out.settingContent),
          manifest: out.manifest,
          validation: out.validation,
          installation: {
            kind: 'Edit title template',
            folderSuffix: 'Fusion/Templates/Edit/Titles',
            restartResolveAfterInstall: true,
          },
        };
      }
      return out;
    }
    if (action === 'validate_title_template') {
      const p = validateTitleTemplateSchema.parse(args);
      const content = p.settingPath ? await fs.readFile(p.settingPath, 'utf8') : p.content;
      return fx.validateAnimatedCaptionSetting(content);
    }
    if (action === 'list_animated_caption_presets') {
      return fx.listAnimatedCaptionSettingPresets();
    }
    throw new Error(`Unknown fusion action: ${action}`);
  },
};
