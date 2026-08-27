#!/usr/bin/env node

// Select the human/shell CLI behavior while retaining the historical
// davinci-resolve-mcp binary's no-arguments-is-stdio contract.
process.env.DAVINCI_RESOLVE_CLI_ENTRY = "1";
await import("./davinci-resolve-mcp.mjs");
