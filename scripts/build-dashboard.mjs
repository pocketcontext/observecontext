import { copyFileSync } from 'node:fs';
copyFileSync('node_modules/pocketbase/dist/pocketbase.es.mjs', 'web/pocketbase.es.mjs');
copyFileSync('node_modules/pocketbase/LICENSE.md', 'web/pocketbase.LICENSE.md');
