#!/usr/bin/env node
import { readFileSync } from 'node:fs';

const schema = JSON.parse(readFileSync(new URL('../config.schema.json', import.meta.url), 'utf8'));
process.stdout.write(JSON.stringify(schema));
