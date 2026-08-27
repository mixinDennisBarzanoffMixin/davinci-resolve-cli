import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { parseParams, runCli } from '../server/cli-lib.mjs';
import { TOOLS, TOOL_BY_NAME, actionsForTool } from '../server/tools/index.mjs';

const advancedRoot = path.resolve(import.meta.dirname, '..');
const cliPath = path.join(advancedRoot, 'cli.mjs');

async function cli(args, options = {}) {
  return new Promise((resolve, reject) => {
    const { input = '', ...spawnOptions } = options;
    const child = spawn(process.execPath, [cliPath, ...args], { cwd: advancedRoot, ...spawnOptions });
    const stdout = [];
    const stderr = [];
    child.stdout.on('data', (chunk) => stdout.push(chunk));
    child.stderr.on('data', (chunk) => stderr.push(chunk));
    child.on('error', reject);
    child.on('close', (code) => {
      const result = { stdout: Buffer.concat(stdout).toString(), stderr: Buffer.concat(stderr).toString() };
      if (code === 0) resolve(result);
      else reject(Object.assign(new Error(`CLI exited ${code}`), result, { code }));
    });
    child.stdin.end(input);
  });
}

test('transport-neutral registry exposes all 18 MCP handler objects and their actions', () => {
  assert.equal(TOOLS.length, 18);
  assert.equal(TOOL_BY_NAME.size, TOOLS.length);
  assert.equal(new Set(TOOLS.map((tool) => tool.name)).size, TOOLS.length);
  for (const tool of TOOLS) {
    assert.equal(TOOL_BY_NAME.get(tool.name), tool);
    assert.equal(typeof tool.handler, 'function');
    assert.ok(actionsForTool(tool).length > 0, `${tool.name} has discoverable actions`);
  }
});

test('list, describe, and actions emit composable JSON discovery data', async () => {
  const listed = await cli(['list', '--compact']);
  assert.equal(listed.stderr, '');
  assert.equal(listed.stdout.split('\n').length, 2, 'compact output is one JSON line plus newline');
  const list = JSON.parse(listed.stdout);
  assert.equal(list.count, 18);
  assert.deepEqual(
    list.tools.map(({ name }) => name),
    TOOLS.map(({ name }) => name),
  );

  const described = JSON.parse((await cli(['describe', 'drx', '--compact'])).stdout);
  assert.equal(described.name, 'drx');
  assert.ok(described.actions.includes('parse'));
  assert.ok(described.actions.includes('generate'));

  const allActions = JSON.parse((await cli(['actions', '--compact'])).stdout);
  assert.deepEqual(allActions.tools.drp, actionsForTool(TOOL_BY_NAME.get('drp')));
  assert.deepEqual(JSON.parse((await cli(['actions', 'capabilities', '--compact'])).stdout), {
    tool: 'capabilities',
    actions: ['get'],
  });
});

test('call and ergonomic TOOL ACTION syntax invoke the same handler directly', async () => {
  const ergonomic = JSON.parse((await cli(['audio_plan', 'select_template', 'contentType=podcast', '--compact'])).stdout);
  const explicit = JSON.parse((await cli(['call', 'audio_plan', 'select_template', '{"contentType":"podcast"}', '--compact'])).stdout);
  assert.deepEqual(ergonomic, explicit);
  assert.ok(ergonomic.template);

  const direct = await TOOL_BY_NAME.get('capabilities').handler({ action: 'get', args: {} });
  const throughCli = await runCli(['capabilities', 'get'], { pretty: false });
  assert.deepEqual(throughCli.value, direct);
});

test('parameters accept inline JSON, @file, stdin, and typed/dotted key=value', async () => {
  const scratch = await mkdtemp(path.join(tmpdir(), 'resolve-advanced-cli-'));
  try {
    const paramsPath = path.join(scratch, 'params.json');
    await writeFile(paramsPath, JSON.stringify({ contentType: 'social' }));
    const fileResult = JSON.parse((await cli(['audio_plan', 'select_template', `@${paramsPath}`, '--compact'])).stdout);
    const stdinResult = JSON.parse(
      (await cli(['audio_plan', 'select_template', '-', '--compact'], { input: '{"contentType":"social"}' })).stdout,
    );
    assert.deepEqual(fileResult, stdinResult);

    const parsed = await runCli(
      ['audio_plan', 'track_plan', '{"contentType":"documentary"}', 'opts.trackCount=4', 'opts.enabled=true'],
      { pretty: false },
    );
    assert.equal(parsed.format, 'json');
    assert.ok(parsed.value.plan);
  } finally {
    await rm(scratch, { recursive: true, force: true });
  }
});

test('root-compatible input, set, and dynamic flag grammar preserves JSON types', async () => {
  const params = await parseParams(
    [
      '--track-type',
      'video',
      '--index=2',
      '--enabled',
      '--no-follow',
      '--nested.mode',
      'fast',
    ],
    {
      inputs: ['{"index":1,"nested":{"keep":true}}'],
      sets: ['nested.depth=3'],
    },
  );
  assert.deepEqual(params, {
    index: 2,
    nested: { keep: true, depth: 3, mode: 'fast' },
    track_type: 'video',
    enabled: true,
    follow: false,
  });

  const selected = JSON.parse(
    (
      await cli([
        'audio_plan',
        'select_template',
        '-i',
        '{"contentType":"social"}',
        '-s',
        'contentType=podcast',
        '--yes',
        '--compact',
      ])
    ).stdout,
  );
  assert.equal(selected.template.name, 'Podcast');
});

test('jsonl, raw path, and shell output match the root Bash contract', async () => {
  const jsonl = await cli(['capabilities', 'get', '-o', 'jsonl']);
  assert.equal(jsonl.stderr, '');
  assert.equal(jsonl.stdout.trimStart()[0], '{');
  assert.equal(jsonl.stdout.split('\n').length, 2);
  assert.ok(JSON.parse(jsonl.stdout).core);

  const raw = await cli(['audio_plan', 'select_template', '--contentType', 'podcast', '--raw', 'template.name']);
  assert.equal(raw.stdout, 'Podcast\n');

  const action = await cli(['actions', 'drp', '--raw', 'actions.0']);
  assert.equal(action.stdout, 'create_empty_project\n');

  const shell = await cli(['audio_plan', 'select_template', 'contentType=podcast', '-o', 'shell']);
  assert.match(shell.stdout, /^TEMPLATE_NAME=Podcast$/m);
  assert.match(shell.stdout, /^TEMPLATE_TRACKS='\[/m);
  assert.equal(shell.stderr, '');
});

test('invalid output formats and raw paths are usage errors', async () => {
  for (const args of [
    ['capabilities', 'get', '-o', 'yaml'],
    ['capabilities', 'get', '--raw', 'missing.path'],
  ]) {
    await assert.rejects(cli(args), (error) => {
      assert.equal(error.code, 2);
      assert.equal(error.stdout, '');
      assert.equal(JSON.parse(error.stderr).exitCode, 2);
      return true;
    });
  }
});

test('usage errors and handler errors use stderr, clean stdout, and meaningful exit codes', async () => {
  await assert.rejects(
    cli(['no_such_tool', 'get']),
    (error) => {
      assert.equal(error.code, 2);
      assert.equal(error.stdout, '');
      assert.match(error.stderr, /"error":"Unknown tool/);
      assert.equal(JSON.parse(error.stderr).exitCode, 2);
      return true;
    },
  );

  await assert.rejects(
    cli(['audio_plan', 'select_template', '{}']),
    (error) => {
      assert.equal(error.code, 1);
      assert.equal(error.stdout, '');
      assert.equal(JSON.parse(error.stderr).exitCode, 1);
      return true;
    },
  );
});
