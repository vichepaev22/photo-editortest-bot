'use strict';
(() => {
  const key='obraz.celebrated.jobs', reduced=matchMedia('(prefers-reduced-motion: reduce)');
  let saved=[];
  try {const value=JSON.parse(sessionStorage.getItem(key)||'[]');if(Array.isArray(value))saved=value.filter(id=>typeof id==='string'&&id.length<80).slice(-64);}catch{}
  const seen=new Set(saved);
  let pending=null,canvas=null,frame=0;
  function remember(id){seen.add(id);while(seen.size>64)seen.delete(seen.values().next().value);try{sessionStorage.setItem(key,JSON.stringify([...seen]));}catch{}}
  function stop(){cancelAnimationFrame(frame);frame=0;canvas?.remove();canvas=null;}
  function play(id){
    if(typeof id!=='string'||!id||id.length>79||seen.has(id))return false;
    if(reduced.matches){remember(id);return false;}
    if(document.hidden||window.ObrazWelcome?.isOpen()){pending=id;return false;}
    pending=null;stop();
    canvas=document.createElement('canvas');canvas.id='resultCelebration';canvas.setAttribute('aria-hidden','true');
    canvas.style.cssText='position:fixed;top:max(var(--safe-top,0px),env(safe-area-inset-top,0px));left:0;width:100%;height:160px;pointer-events:none;z-index:40';
    const width=innerWidth,ratio=Math.min(devicePixelRatio||1,2);
    canvas.width=Math.round(width*ratio);canvas.height=160*ratio;
    const context=canvas.getContext('2d');if(!context){canvas=null;remember(id);return false;}
    context.scale(ratio,ratio);document.body.append(canvas);remember(id);
    const colors=['#176b5b','#8bb8a3','#d6eadd','#b79753','#c08068'];
    let seed=[...id].reduce((n,c)=>(n*31+c.charCodeAt(0))>>>0,17);
    const random=()=>{seed=(1664525*seed+1013904223)>>>0;return seed/4294967296;};
    const particles=Array.from({length:48},(_,i)=>({x:width*(i<24?.32:.68),angle:i%24/24*Math.PI*2,distance:32+random()*58,size:2+random()*2.5,spin:random()*Math.PI,color:colors[i%colors.length]}));
    let start;
    const draw=now=>{
      if(!canvas||document.hidden||reduced.matches){stop();return;}
      start??=now;const t=Math.min(1,(now-start)/1400),travel=1-Math.pow(1-t,3);
      context.clearRect(0,0,width,160);context.globalAlpha=Math.min(1,t*12)*Math.min(1,(1-t)*3);
      for(const p of particles){
        const x=p.x+Math.cos(p.angle)*p.distance*travel,y=28+Math.sin(p.angle)*p.distance*travel+80*t*t;
        context.save();context.translate(x,y);context.rotate(p.spin+t*3);context.fillStyle=p.color;
        context.fillRect(-p.size/2,-p.size/2,p.size,p.size*1.65);context.restore();
      }
      if(t<1)frame=requestAnimationFrame(draw);else stop();
    };
    frame=requestAnimationFrame(draw);return true;
  }
  function flush(){if(pending&&!document.hidden&&!window.ObrazWelcome?.isOpen())play(pending);}
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();else flush();});
  window.addEventListener('pagehide',stop);window.addEventListener('pageshow',flush);
  window.addEventListener('obraz:welcome-complete',flush);
  reduced.addEventListener('change',()=>{if(reduced.matches){stop();if(pending)remember(pending);pending=null;}});
  window.ObrazCelebration={play,clear:()=>{pending=null;stop();}};
})();
