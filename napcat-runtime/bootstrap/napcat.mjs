import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const currentDir = dirname(fileURLToPath(import.meta.url));
await import(pathToFileURL(join(currentDir, '..', 'napcat.mjs')).href);
