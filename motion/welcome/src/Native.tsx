import React, {useEffect, useState} from 'react';
import {createRoot, type Root} from 'react-dom/client';
import {WelcomeScene} from './Scene';

const dialog=document.getElementById('welcomeDialog') as HTMLDialogElement;
const mount=document.getElementById('welcomeStage')!;
// The studio's generic `svg` rules must not resize or recolour illustrated portraits.
const shadow=mount.attachShadow({mode:'open'});
const isolatedStyles=document.createElement('style');
isolatedStyles.textContent=`*{box-sizing:border-box}.welcome-canvas{position:absolute;width:1080px;height:1680px;transform-origin:top left;text-align:left}.welcome-canvas svg{display:block}.welcome-progress{position:absolute;bottom:0;left:50%;transform:translateX(-50%);display:flex;gap:5px;width:min(82%,380px)}.welcome-progress span{flex:1;background:#d7e0d5;height:3px;border-radius:6px;overflow:hidden}.welcome-progress i{display:block;height:100%;background:#176b5b}`;
const isolatedMount=document.createElement('div');isolatedMount.style.cssText='height:100%;width:100%;position:relative';
shadow.append(isolatedStyles,isolatedMount);
const key='obraz.welcome.svg.v1';
let root:Root|null=null, closedInSession=false;
const media=window.matchMedia('(prefers-reduced-motion: reduce)');

function Animation(){
  const [reduced,setReduced]=useState(media.matches);
  const [t,setTime]=useState(media.matches?21.9:0);
  const [size,setSize]=useState({width:320,height:500});
  useEffect(()=>{
    const resize=()=>setSize({width:mount.clientWidth,height:mount.clientHeight});
    const observer=new ResizeObserver(resize);observer.observe(mount);resize();
    return()=>observer.disconnect();
  },[]);
  useEffect(()=>{
    const change=()=>setReduced(media.matches);
    media.addEventListener('change',change);
    return()=>media.removeEventListener('change',change);
  },[]);
  useEffect(()=>{
    if(reduced){setTime(21.9);return;}
    let frame=0,elapsed=0,last=performance.now(),paint=last;
    const resume=()=>{last=performance.now();};
    const tick=(now:number)=>{
      if(!document.hidden){
        elapsed+=Math.min(now-last,100); // Background time never skips the story.
        if(now-paint>=1000/30){setTime(Math.min(elapsed/1000,21.9));paint=now;}
      }
      last=now;if(elapsed<22000)frame=requestAnimationFrame(tick);
    };
    document.addEventListener('visibilitychange',resume);
    window.addEventListener('pageshow',resume);
    frame=requestAnimationFrame(tick);
    return()=>{cancelAnimationFrame(frame);document.removeEventListener('visibilitychange',resume);window.removeEventListener('pageshow',resume);};
  },[reduced]);
  const scale=Math.max(.01,Math.min(size.width/1080,size.height/1680));
  const beats=[0,2,5,9,12,16,19];
  return <>
    <div className="welcome-canvas" aria-hidden="true" style={{left:(size.width-1080*scale)/2,top:(size.height-1680*scale)/2,transform:`scale(${scale})`}}><WelcomeScene t={t} showFooter={false}/></div>
    <div className="welcome-progress" aria-hidden="true">{beats.map((start,i)=><span key={start}><i style={{width:`${Math.max(0,Math.min(1,(t-start)/((beats[i+1]??22)-start)))*100}%`}}/></span>)}</div>
  </>;
}

function close(mark=true){
  if(mark){closedInSession=true;try{localStorage.setItem(key,'seen');}catch{}}
  root?.unmount();root=null;
  if(dialog.open)dialog.close();
  window.dispatchEvent(new Event('obraz:welcome-complete'));
}
function open(force=false){
  if(!dialog || !mount || dialog.open)return;
  let seen=closedInSession;try{seen=seen||localStorage.getItem(key)==='seen';}catch{}
  if(seen&&!force)return;
  try{
    dialog.showModal();
    root=createRoot(isolatedMount,{identifierPrefix:'welcome-',onUncaughtError:()=>queueMicrotask(()=>close(false))});
    root.render(<Animation/>);
    (document.getElementById('welcomeStart') as HTMLButtonElement).focus();
    window.dispatchEvent(new Event('obraz:welcome-open'));
  }catch{close(false);}
}
document.getElementById('welcomeStart')?.addEventListener('click',()=>close());
document.getElementById('welcomeSkip')?.addEventListener('click',()=>close());
dialog?.addEventListener('cancel',event=>{event.preventDefault();close();});
declare global{interface Window{ObrazWelcome:{open:(force?:boolean)=>void;isOpen:()=>boolean};}}
window.ObrazWelcome={open,isOpen:()=>Boolean(dialog?.open)};
