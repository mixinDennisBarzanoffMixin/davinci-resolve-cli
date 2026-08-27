/**
 * Transport-neutral registry for every advanced tool.
 *
 * Both MCP and the command-line interface import this module. Keeping the
 * registry here guarantees the two transports dispatch to the same handler
 * objects instead of maintaining parallel implementations.
 */
import { drpTool } from './drp.mjs';
import { drtTool } from './drt.mjs';
import { drxTool } from './drx.mjs';
import { offlineRefTool } from './offline_ref.mjs';
import { fusionTool } from './fusion.mjs';
import { audioPlanTool } from './audio_plan.mjs';
import { fairlightTool } from './fairlight.mjs';
import { audioTool } from './audio.mjs';
import { conformTool } from './conform.mjs';
import { projectDbTool } from './project_db.mjs';
import { projectReadTool } from './project_read.mjs';
import { colorTraceTool } from './color_trace.mjs';
import { capabilitiesTool } from './capabilities.mjs';
import { pipelineTool } from './pipeline.mjs';
import { deliverableTool } from './deliverable.mjs';
import { mediaTool } from './media.mjs';
import { editorialTool } from './editorial.mjs';
import { provenanceTool } from './provenance.mjs';

export const TOOLS = Object.freeze([
  drpTool,
  drtTool,
  drxTool,
  offlineRefTool,
  fusionTool,
  audioPlanTool,
  fairlightTool,
  audioTool,
  conformTool,
  projectDbTool,
  projectReadTool,
  colorTraceTool,
  capabilitiesTool,
  pipelineTool,
  deliverableTool,
  mediaTool,
  editorialTool,
  provenanceTool,
]);

export const TOOL_BY_NAME = new Map(TOOLS.map((tool) => [tool.name, tool]));

// Actions are deliberately derived from the handler that executes them. This
// prevents CLI discovery metadata from drifting as actions are added to an
// if-chain or switch. capabilities.get is transport convention: its handler
// needs no arguments and intentionally ignores the action.
const ACTION_OVERRIDES = Object.freeze({ capabilities: ['get'] });

export function actionsForTool(tool) {
  if (ACTION_OVERRIDES[tool.name]) return [...ACTION_OVERRIDES[tool.name]];
  const source = Function.prototype.toString.call(tool.handler);
  const found = new Set();
  for (const pattern of [/action\s*===\s*['"]([^'"]+)['"]/g, /case\s+['"]([^'"]+)['"]\s*:/g]) {
    for (const match of source.matchAll(pattern)) found.add(match[1]);
  }
  return [...found];
}
