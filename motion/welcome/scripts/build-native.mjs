import path from 'node:path';
import fs from 'node:fs';
import {build} from 'esbuild';
const root=path.resolve(import.meta.dirname,'..');
const output=path.resolve(root,'../../src/image_studio/web/welcome.js');
await build({entryPoints:[path.join(root,'src/Native.tsx')],outfile:output,bundle:true,minify:true,
  platform:'browser',format:'iife',target:['es2020'],jsx:'automatic',legalComments:'inline',
  define:{'process.env.NODE_ENV':'"production"'}});
console.log(JSON.stringify({nativeBundle:fs.statSync(output).size}));
