#!/usr/bin/env node
import { readFileSync } from 'node:fs';

const manifest = JSON.parse(readFileSync(new URL('../pb-lsp.json', import.meta.url), 'utf8'));
process.stdout.write(JSON.stringify(manifest));
