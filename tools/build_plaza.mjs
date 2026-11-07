import fs from 'node:fs';
import { build } from 'esbuild';
const base = process.env.PLAZA_API_BASE || 'https://api.i02zane.site/wuwa-tappo-plus/v1';
const address = new URL(base);
if (address.protocol !== 'https:' || address.username || address.password || address.search || address.hash || address.port || address.pathname !== '/wuwa-tappo-plus/v1') throw new Error('Invalid PLAZA_API_BASE');
await build({ entryPoints: ['web/plaza/native.mjs'], outfile: 'src/wuwa_tappo_plus/scripts/plaza-client.bundle.js',
  bundle: true, minify: true, format: 'iife', globalName: '__WUT_PLAZA_CLIENT__', platform: 'browser', target: ['chrome120'],
  define: { PLAZA_CONFIG: JSON.stringify({ base }) }, legalComments: 'eof' });
fs.writeFileSync('src/wuwa_tappo_plus/scripts/plaza-config.json', JSON.stringify({ base }, null, 2));
console.log(`Built plaza client for ${address.hostname}`);
