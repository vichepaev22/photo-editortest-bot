import {bundle} from '@remotion/bundler';
import {selectComposition,renderStill,renderMedia} from '@remotion/renderer';
import path from 'node:path';
import fs from 'node:fs';

const root=path.resolve(import.meta.dirname,'..');
const browser=path.resolve(root,'../../..','animated-bar-chart/node_modules/.remotion/chrome-headless-shell/win64/chrome-headless-shell-win64/chrome-headless-shell.exe');
if(!fs.existsSync(browser))throw new Error('Local Chromium unavailable; set up the inspected browser runtime first.');
fs.mkdirSync(path.join(root,'out/stills'),{recursive:true});
const serveUrl=await bundle({entryPoint:path.join(root,'src/index.tsx'),publicDir:path.join(root,'public'),outDir:path.join(root,'build')});
const composition=await selectComposition({serveUrl,id:'ObrazWelcome',browserExecutable:browser});
if(process.argv.includes('--stills')){
  for(const frame of [40,110,238,335,468,552,645]){
    await renderStill({serveUrl,composition,browserExecutable:browser,frame,scale:.55,output:path.join(root,`out/stills/scene-${frame}.png`)});
    console.log(`SCENE ${frame} saved`);
  }
}else{
  let previous=-1;
  await renderMedia({serveUrl,composition,browserExecutable:browser,codec:'h264',pixelFormat:'yuv420p',imageFormat:'jpeg',jpegQuality:90,crf:19,concurrency:4,outputLocation:path.join(root,'out/obraz-welcome-master.mp4'),onProgress:({progress})=>{const percent=Math.floor(progress*20)*5;if(percent!==previous){previous=percent;console.log(`RENDER ${percent}%`);}}});
  console.log('VIDEO COMPLETE');
}
