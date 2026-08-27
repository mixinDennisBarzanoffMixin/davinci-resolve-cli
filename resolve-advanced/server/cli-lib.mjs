import { readFile } from 'node:fs/promises';
import { TOOL_BY_NAME, TOOLS, actionsForTool } from './tools/index.mjs';

const OUTPUT_FORMATS = new Set(['json', 'jsonl', 'raw', 'shell']);

export class CliError extends Error {
  constructor(message, exitCode = 2, details = undefined) {
    super(message);
    this.name = 'CliError';
    this.exitCode = exitCode;
    this.details = details;
  }
}

export const USAGE = `Usage:
  resolve-advanced list
  resolve-advanced describe TOOL
  resolve-advanced actions [TOOL]
  resolve-advanced call TOOL ACTION [PARAMS...]
  resolve-advanced TOOL ACTION [PARAMS...]

PARAMS may be a JSON object, @file.json, -/--stdin, or key=value pairs.
Values in key=value pairs are parsed as JSON when possible. Dotted keys create
nested objects. Multiple parameter sources are merged from left to right.

  -i, --input JSON|@FILE|-  merge a JSON request object
  -s, --set KEY=VALUE       set a dotted key (repeatable)
  --key VALUE               dynamic parameter (--flag / --no-flag are booleans)
  -o, --output FORMAT       json, jsonl, raw, or shell
  --raw DOT.PATH            select a result path and emit it raw
  --pretty | --compact      control JSON whitespace
  --yes                     accepted for root CLI parity (no advanced action currently confirms)

Examples:
  resolve-advanced capabilities get
  resolve-advanced fusion list_templates
  resolve-advanced drx parse drxPath=/tmp/look.drx
  resolve-advanced call media media_inventory @request.json
  jq -n '{path:"/tmp/project.drp"}' | resolve-advanced drp list_nested -`;

function parseValue(raw) {
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function setPath(target, path, value) {
  const normalized = path.trim().replaceAll('-', '_');
  const parts = normalized.split('.');
  if (parts.some((part) => !part)) throw new CliError(`Invalid empty key segment in '${path}'`);
  let cursor = target;
  for (const part of parts.slice(0, -1)) {
    if (cursor[part] === undefined) cursor[part] = {};
    if (!cursor[part] || typeof cursor[part] !== 'object' || Array.isArray(cursor[part])) {
      throw new CliError(`Cannot set '${path}': '${part}' is not an object`);
    }
    cursor = cursor[part];
  }
  cursor[parts.at(-1)] = value;
}

function deepMerge(target, incoming) {
  for (const [key, value] of Object.entries(incoming)) {
    if (
      target[key] &&
      value &&
      typeof target[key] === 'object' &&
      typeof value === 'object' &&
      !Array.isArray(target[key]) &&
      !Array.isArray(value)
    ) {
      deepMerge(target[key], value);
    } else {
      target[key] = value;
    }
  }
}

function mergeParams(target, value, source) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new CliError(`${source} must contain a JSON object`);
  }
  deepMerge(target, value);
}

async function readStdin(stdin) {
  if (stdin !== undefined) return stdin;
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks.map((chunk) => (Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)))).toString('utf8');
}

export async function parseParams(tokens, { stdin, inputs = [], sets = [] } = {}) {
  const args = {};
  let consumedStdin = false;
  const fromJson = (text, source) => {
    let value;
    try {
      value = text.trim() ? JSON.parse(text) : {};
    } catch (error) {
      throw new CliError(`Invalid JSON from ${source}: ${error.message}`);
    }
    mergeParams(args, value, source);
  };

  const fromFile = async (path) => {
    try {
      fromJson(await readFile(path, 'utf8'), path);
    } catch (error) {
      if (error instanceof CliError) throw error;
      throw new CliError(`Cannot read JSON input '${path}': ${error.message}`);
    }
  };

  const fromInput = async (source) => {
    if (source === '-') {
      if (consumedStdin) throw new CliError('stdin may only be used once');
      consumedStdin = true;
      fromJson(await readStdin(stdin), 'stdin');
    } else if (source.startsWith('@')) {
      if (!source.slice(1)) throw new CliError('@ requires a JSON file path');
      await fromFile(source.slice(1));
    } else {
      fromJson(source, '--input');
    }
  };

  for (const source of inputs) await fromInput(source);
  for (const assignment of sets) {
    const equal = assignment.indexOf('=');
    if (equal < 1) throw new CliError(`--set requires key=value, got '${assignment}'`);
    setPath(args, assignment.slice(0, equal), parseValue(assignment.slice(equal + 1)));
  }

  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    if (token === '--') continue;
    if (token === '-' || token === '--stdin') {
      await fromInput('-');
      continue;
    }
    if (token === '--json' || token === '--params') {
      const value = tokens[++i];
      if (value === undefined) throw new CliError(`${token} requires a JSON object`);
      fromJson(value, token);
      continue;
    }
    if (token.startsWith('--json=') || token.startsWith('--params=')) {
      fromJson(token.slice(token.indexOf('=') + 1), token.slice(0, token.indexOf('=')));
      continue;
    }
    if (token === '--file') {
      const path = tokens[++i];
      if (!path) throw new CliError('--file requires a path');
      await fromFile(path);
      continue;
    }
    if (token === '--set') {
      const assignment = tokens[++i];
      if (!assignment) throw new CliError('--set requires key=value');
      const equal = assignment.indexOf('=');
      if (equal < 1) throw new CliError(`Expected key=value, got '${assignment}'`);
      setPath(args, assignment.slice(0, equal), parseValue(assignment.slice(equal + 1)));
      continue;
    }
    if (token.startsWith('@')) {
      await fromInput(token);
      continue;
    }
    if (token.startsWith('{')) {
      fromJson(token, 'inline argument');
      continue;
    }
    const equal = token.indexOf('=');
    if (equal > 0) {
      const key = token.startsWith('--') ? token.slice(2, equal) : token.slice(0, equal);
      setPath(args, key, parseValue(token.slice(equal + 1)));
      continue;
    }
    if (token.startsWith('--no-')) {
      setPath(args, token.slice(5), false);
      continue;
    }
    if (token.startsWith('--')) {
      const key = token.slice(2);
      if (!key) continue;
      const next = tokens[i + 1];
      if (next !== undefined && !next.startsWith('--') && !next.includes('=')) {
        setPath(args, key, parseValue(next));
        i += 1;
      } else {
        setPath(args, key, true);
      }
      continue;
    }
    throw new CliError(`Unrecognized parameter '${token}'; use JSON, @file, stdin, key=value, or --key value`);
  }
  return args;
}

function getTool(name) {
  const tool = TOOL_BY_NAME.get(name);
  if (!tool) throw new CliError(`Unknown tool '${name}'`, 2, { available: TOOLS.map((item) => item.name) });
  return tool;
}

export function toolSummary(tool) {
  return { name: tool.name, description: tool.description, actions: actionsForTool(tool) };
}

function parseGlobalOptions(argv, defaultPretty) {
  const options = {
    pretty: defaultPretty,
    output: 'json',
    rawPath: null,
    inputs: [],
    sets: [],
    yes: false,
  };
  const tokens = [];
  const valued = new Map([
    ['--input', 'inputs'],
    ['-i', 'inputs'],
    ['--set', 'sets'],
    ['-s', 'sets'],
    ['--output', 'output'],
    ['-o', 'output'],
    ['--raw', 'rawPath'],
  ]);
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    const key = valued.get(token);
    if (key) {
      const value = argv[++i];
      if (value === undefined) throw new CliError(`${token} requires a value`);
      if (key === 'inputs' || key === 'sets') options[key].push(value);
      else options[key] = value;
      continue;
    }
    let matched = false;
    for (const [prefix, optionKey] of [
      ['--input=', 'inputs'],
      ['--set=', 'sets'],
      ['--output=', 'output'],
      ['--raw=', 'rawPath'],
    ]) {
      if (token.startsWith(prefix)) {
        const value = token.slice(prefix.length);
        if (!value) throw new CliError(`${prefix.slice(0, -1)} requires a value`);
        if (optionKey === 'inputs' || optionKey === 'sets') options[optionKey].push(value);
        else options[optionKey] = value;
        matched = true;
        break;
      }
    }
    if (matched) continue;
    if (token === '--compact') options.pretty = false;
    else if (token === '--pretty') options.pretty = true;
    else if (token === '--yes') options.yes = true;
    else tokens.push(token);
  }
  if (!OUTPUT_FORMATS.has(options.output)) {
    throw new CliError(`--output must be one of: ${[...OUTPUT_FORMATS].join(', ')}`);
  }
  if (options.rawPath !== null) options.output = 'raw';
  return { tokens, options };
}

/** Invoke the exact handler object registered with the MCP server. */
export async function invokeTool(toolName, action, args = {}) {
  const tool = getTool(toolName);
  const actions = actionsForTool(tool);
  if (!actions.includes(action)) {
    throw new CliError(`Unknown action '${action}' for tool '${toolName}'`, 2, { available: actions });
  }
  try {
    return await tool.handler({ action, args });
  } catch (error) {
    if (error instanceof CliError) throw error;
    const wrapped = new CliError(error?.message || String(error), 1);
    wrapped.cause = error;
    if (Array.isArray(error?.issues)) wrapped.details = { issues: error.issues };
    throw wrapped;
  }
}

export async function runCli(argv, options = {}) {
  const parsed = parseGlobalOptions(argv, options.pretty ?? Boolean(process.stdout.isTTY));
  const tokens = parsed.tokens;
  const globals = parsed.options;
  const result = (format, value) => ({ format, value, pretty: globals.pretty, output: globals.output, rawPath: globals.rawPath });

  if (tokens.length === 0 || tokens[0] === '-h' || tokens[0] === '--help' || tokens[0] === 'help') {
    return result('text', USAGE);
  }

  const command = tokens.shift();
  if (command === 'list') {
    if (tokens.length) throw new CliError('list takes no arguments');
    return result('json', { count: TOOLS.length, tools: TOOLS.map(toolSummary) });
  }
  if (command === 'describe') {
    if (tokens.length !== 1) throw new CliError('describe requires exactly one TOOL');
    return result('json', toolSummary(getTool(tokens[0])));
  }
  if (command === 'actions') {
    if (tokens.length > 1) throw new CliError('actions accepts at most one TOOL');
    if (tokens.length === 1) {
      const tool = getTool(tokens[0]);
      return result('json', { tool: tool.name, actions: actionsForTool(tool) });
    }
    return result('json', { tools: Object.fromEntries(TOOLS.map((tool) => [tool.name, actionsForTool(tool)])) });
  }

  let toolName = command;
  if (command === 'call') {
    toolName = tokens.shift();
    if (!toolName) throw new CliError('call requires TOOL and ACTION');
  }
  const action = tokens.shift();
  if (!action) throw new CliError(`${toolName} requires an ACTION`);
  const args = await parseParams(tokens, { ...options, inputs: globals.inputs, sets: globals.sets });
  return result('json', await invokeTool(toolName, action, args));
}

function extractPath(value, path) {
  if (path === null || path === '' || path === '.') return value;
  let cursor = value;
  for (const part of path.replace(/^\.|\.$/g, '').split('.')) {
    try {
      if (Array.isArray(cursor)) {
        if (!/^\d+$/.test(part)) throw new Error();
        cursor = cursor[Number(part)];
      } else {
        cursor = cursor[part];
      }
    } catch {
      throw new CliError(`Output path not found: ${path}`);
    }
    if (cursor === undefined) throw new CliError(`Output path not found: ${path}`);
  }
  return cursor;
}

function shellQuote(value) {
  if (value === '') return "''";
  if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(value)) return value;
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function shellKey(parts) {
  const raw = parts.length ? parts.join('_') : 'RESULT';
  let key = raw.replace(/[^A-Za-z0-9_]/g, '_').toUpperCase().replace(/^_+|_+$/g, '') || 'RESULT';
  if (/^\d/.test(key)) key = `_${key}`;
  return key;
}

function rawValue(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function shellLines(value, parts = []) {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const lines = [];
    for (const [key, item] of Object.entries(value)) {
      if (item && typeof item === 'object' && !Array.isArray(item)) lines.push(...shellLines(item, [...parts, key]));
      else lines.push(`${shellKey([...parts, key])}=${shellQuote(rawValue(item))}`);
    }
    return lines;
  }
  return [`${shellKey(parts)}=${shellQuote(rawValue(value))}`];
}

export function serializeResult(result) {
  if (result.format === 'text') return `${result.value}\n`;
  const value = extractPath(result.value, result.rawPath);
  if (result.output === 'raw') return value === null || value === undefined ? '' : `${rawValue(value)}\n`;
  if (result.output === 'shell') {
    const lines = shellLines(value);
    return lines.length ? `${lines.join('\n')}\n` : '';
  }
  if (result.output === 'jsonl') return `${JSON.stringify(value ?? null)}\n`;
  return `${JSON.stringify(value ?? null, null, result.pretty ? 2 : 0)}\n`;
}

export function serializeError(error) {
  const payload = { error: error?.message || String(error), exitCode: error?.exitCode || 3 };
  if (error?.details !== undefined) payload.details = error.details;
  return `${JSON.stringify(payload)}\n`;
}

export function resultIsError(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const error = value.error;
  const hasError =
    error !== undefined &&
    error !== null &&
    error !== false &&
    error !== 0 &&
    error !== '' &&
    !(typeof error === 'object' && !Array.isArray(error) && Object.keys(error).length === 0);
  return (
    value.success === false ||
    value.isError === true ||
    value.status === 'confirmation_required' ||
    hasError
  );
}
