#!/usr/bin/env node

// Tool implementations occasionally use console for diagnostics. stdout is a
// machine-readable result channel in this CLI, so route all diagnostics to
// stderr before invoking a handler.
for (const method of ['log', 'info', 'debug', 'warn', 'error']) {
  console[method] = (...values) => process.stderr.write(`${values.map(String).join(' ')}\n`);
}

const { resultIsError, runCli, serializeError, serializeResult } = await import('./server/cli-lib.mjs');

try {
  const result = await runCli(process.argv.slice(2));
  process.stdout.write(serializeResult(result));
  if (resultIsError(result.value)) process.exitCode = 1;
} catch (error) {
  process.stderr.write(serializeError(error));
  process.exitCode = error?.exitCode || 3;
}
