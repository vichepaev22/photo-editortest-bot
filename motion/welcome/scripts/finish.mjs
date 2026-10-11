import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
const root=path.resolve(import.meta.dirname,'..'),out=path.join(root,'out');
const ffmpeg=process.argv[2] || 'ffmpeg',ffprobe=process.argv[3] || 'ffprobe';
const master=path.join(out,'obraz-welcome-master.mp4');
if(!fs.existsSync(master))throw new Error('Render must complete successfully before finishing.');
const run=(args)=>execFileSync(ffmpeg,['-hide_banner','-loglevel','error','-y',...args],{stdio:'inherit'});
for(const [name,crf,scale] of [['obraz-welcome-22s.mp4','24',null],['obraz-welcome-miniapp.mp4','26','720:1280']]){
  const output=path.join(out,name);
  const filter=`scale=${scale || 'iw:ih'}:flags=lanczos:in_range=auto:out_range=tv,format=yuv420p`;
  run(['-i',master,'-vf',filter,'-c:v','libx264','-preset','medium','-crf',crf,'-pix_fmt','yuv420p','-color_range','tv','-an','-movflags','+faststart',output]);
  const meta=JSON.parse(execFileSync(ffprobe,['-v','error','-show_streams','-show_format','-of','json',output],{encoding:'utf8'}));
  const stream=meta.streams.find(s=>s.codec_type==='video');
  if(Number(meta.format.duration)!==22 || stream.r_frame_rate!=='30/1' || stream.codec_name!=='h264' || stream.pix_fmt!=='yuv420p')throw new Error(`Invalid export: ${name}`);
  const record={file:name,width:stream.width,height:stream.height,duration:Number(meta.format.duration),fps:stream.r_frame_rate,codec:stream.codec_name,pixelFormat:stream.pix_fmt,bytes:fs.statSync(output).size,audio:meta.streams.some(s=>s.codec_type==='audio')};
  fs.writeFileSync(path.join(out,name+'.json'),JSON.stringify(record,null,2));
  console.log(JSON.stringify(record));
}
run(['-ss','1.3','-i',path.join(out,'obraz-welcome-miniapp.mp4'),'-frames:v','1','-c:v','libwebp','-quality','85',path.join(out,'obraz-welcome-poster.webp')]);
run(['-ss','18.4','-i',path.join(out,'obraz-welcome-22s.mp4'),'-frames:v','1',path.join(out,'passport-final.png')]);
run(['-i',path.join(out,'obraz-welcome-22s.mp4'),'-f','null','-']);
console.log('FULL VIDEO DECODE OK');
