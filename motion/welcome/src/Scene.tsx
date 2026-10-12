import React from 'react';
const AbsoluteFill=({children,style={}}:{children?:React.ReactNode;style?:React.CSSProperties})=><div style={{position:'absolute',inset:0,...style}}>{children}</div>;
import {C,Icon,Portrait,StandingPerson} from './art';

const clamp=(x:number)=>Math.max(0,Math.min(1,x));
const ease=(x:number)=>{const p=clamp(x);return p*p*p*(p*(p*6-15)+10);};
const phase=(t:number,a:number,b:number)=>ease((t-a)/(b-a));
const mix=(a:number,b:number,p:number)=>a+(b-a)*p;
const window=(t:number,a:number,b:number,d=.4)=>phase(t,a,a+d)*(1-phase(t,b-d,b));
const shadow='0 24px 68px rgba(39,61,50,.10)';
const hairNames=['Короткая','Боб','До плеч','Мягкие волны','Кудри','Длинные','Хвост','Пучок','Пробор'];
const items=[['hair','Причёска'],['clothes','Одежда'],['glasses','Очки'],['photo','Фон'],['sparkle','Улучшение'],['grid','Объединить'],['photo','По примеру'],['document','Документы']];

function Background({t}:{t:number}){
  const shift=Math.sin(t*.38)*10;
  return <AbsoluteFill style={{background:C.paper,overflow:'hidden'}}>
    <svg width="1080" height="1920" viewBox="0 0 1080 1920" style={{position:'absolute'}}>
      <defs><radialGradient id="wash"><stop stopColor="#DCEAE0" stopOpacity=".9"/><stop offset="1" stopColor="#DCEAE0" stopOpacity="0"/></radialGradient><radialGradient id="peach"><stop stopColor="#EEE4D7" stopOpacity=".7"/><stop offset="1" stopColor="#EEE4D7" stopOpacity="0"/></radialGradient></defs>
      <ellipse cx={110+shift} cy="690" rx="560" ry="610" fill="url(#wash)"/>
      <ellipse cx={1010-shift} cy="1300" rx="560" ry="580" fill="url(#peach)"/>
      <g fill="none" stroke="#CBDAD0" strokeWidth="2" opacity=".55" transform={`translate(0 ${shift})`}>
        <rect x="-45" y="525" width="130" height="170" rx="16" transform="rotate(-13 0 600)"/>
        <path d="M984 356q52-28 104-2M986 360q8 44 41 30 15-6 11-31m9-4q-6 49 34 28"/>
        <rect x="970" y="1460" width="122" height="157" rx="10" transform="rotate(12 990 1520)"/>
        <path d="M-12 1463q25-40 69-3m-48 50 54-31"/>
      </g>
      {[114,951,80,1013].map((x,i)=><circle key={i} cx={x} cy={720+i*250} r={i%2?4:3} fill="#B9CDC0" opacity=".65"/>)}
    </svg>
  </AbsoluteFill>;
}

function Brand({t}:{t:number}){
  return <div style={{position:'absolute',left:76,top:67,width:928,display:'flex',justifyContent:'space-between',alignItems:'center',opacity:phase(t,0,.6)}}>
    <div style={{fontSize:68,fontWeight:650,letterSpacing:-3,display:'flex',alignItems:'baseline',gap:8}}>образ<span style={{display:'inline-block',background:C.green,width:12,height:12,borderRadius:20}}/></div>
    <div style={{fontSize:23,color:'#687972',border:'1px solid #D8E0D8',padding:'13px 22px',borderRadius:28}}>Знакомство со студией</div>
  </div>;
}

const titles=[
  {a:0,b:2,small:'ВАШ НОВЫЙ ВЗГЛЯД НА СЕБЯ',title:'Ваша личная\nфотостудия',sub:'Одно фото. Много возможностей.'},
  {a:2,b:5,small:'НАЧНИТЕ СО СВОЕГО ФОТО',title:'Загрузите\nсвоё фото',sub:'Из галереи — или новое селфи.'},
  {a:5,b:9,small:'ПРИЧЁСКА',title:'Один человек.\nРазные образы.',sub:'Найдите то, что нравится вам.'},
  {a:9,b:12,small:'ОЧКИ',title:'Детали меняют\nвпечатление.',sub:'Примерьте разные оправы.'},
  {a:12,b:16,small:'ФОТО НА ДОКУМЕНТЫ',title:'Всё готово\nна одном листе.',sub:'Четыре одинаковых снимка.'},
  {a:16,b:19,small:'ВАШ РЕЗУЛЬТАТ',title:'Сохраните.\nИ улыбайтесь.',sub:'Новый образ всегда под рукой.'},
  {a:19,b:22.5,small:'ОБРАЗ · ВАША ФОТОСТУДИЯ',title:'Теперь\nваша очередь.',sub:'Выберите, с чего начать.'}
];

function Titles({t}:{t:number}){
  return <>{titles.map((s,i)=>{
    const opacity=(i===0?phase(t,0,.5):phase(t,s.a-.16,s.a+.3))*(i===6?1:1-phase(t,s.b-.28,s.b+.05));
    if(opacity<.002)return null;
    return <div key={i} style={{position:'absolute',left:76,top:202,width:940,opacity,transform:`translateY(${(1-phase(t,s.a-.16,s.a+.4))*14}px)`}}>
      <div style={{fontSize:21,fontWeight:650,letterSpacing:3.2,color:C.green,marginBottom:22}}>{s.small}</div>
      <div style={{fontSize:78,lineHeight:1.07,fontWeight:600,letterSpacing:-2.8,whiteSpace:'pre-line'}}>{s.title}</div>
      <div style={{fontSize:30,color:'#738079',marginTop:21}}>{s.sub}</div>
    </div>;
  })}</>;
}

function PhotoCard({x,y,w,h,hair=3,glasses=0,glassOpacity=1,document=false,opacity=1,rotation=0,label,selected=false}: {x:number;y:number;w:number;h:number;hair?:number;glasses?:number;glassOpacity?:number;document?:boolean;opacity?:number;rotation?:number;label?:string;selected?:boolean}){
  return <div style={{position:'absolute',left:x,top:y,width:w,height:h,opacity,transform:`rotate(${rotation}deg)`,borderRadius:document?10:28,background:'#FFF',boxShadow:shadow,padding:document?10:13,border:selected?'3px solid #176B5B':'1px solid rgba(211,222,213,.8)',overflow:'hidden'}}>
    <div style={{width:'100%',height:label?'calc(100% - 50px)':'100%',borderRadius:document?4:18,overflow:'hidden',background:document?'#FFF':'#E8EDE6'}}><Portrait hair={hair} glasses={glasses} glassOpacity={glassOpacity} document={document} smile={document?0:.2}/></div>
    {label&&<div style={{height:50,display:'flex',alignItems:'center',justifyContent:'center',fontSize:26,fontWeight:500,color:'#47594F'}}>{label}</div>}
  </div>;
}

function Studio({t,ending=false}:{t:number;ending?:boolean}){
  const active=ending?'':t>3.8?'hair':'';
  const cardW=882,cardH=1128;
  return <div style={{position:'absolute',left:99,top:515,width:cardW,height:cardH,border:'1px solid #DBE1D9',borderRadius:34,background:'#FFF',boxShadow:shadow,padding:38,boxSizing:'border-box',overflow:'hidden'}}>
    <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:24}}><div style={{fontSize:31,fontWeight:600}}>Студия</div><div style={{fontSize:23,padding:'8px 17px',borderRadius:30,background:C.mint,color:C.green}}>Ваше фото</div></div>
    <div style={{height:425,borderRadius:22,background:ending?'#E8EDE6':'#F2F4F0',border:ending?'none':'2px dashed #C5D7C9',display:'flex',alignItems:'center',justifyContent:'center',overflow:'hidden',position:'relative'}}>
      {ending?<div style={{height:418,width:322,transform:`translateY(${(1-phase(t,19,19.7))*18}px)`}}><Portrait hair={3} glasses={1} smile={.6}/></div>:
        <div style={{textAlign:'center'}}><div style={{width:84,height:84,margin:'0 auto 24px',borderRadius:26,background:C.mint,display:'flex',alignItems:'center',justifyContent:'center'}}><Icon name="plus" size={48}/></div><div style={{fontSize:33,fontWeight:550}}>Добавьте своё фото</div><div style={{fontSize:24,color:'#819086',marginTop:13}}>Здесь начинается новый образ</div></div>}
      {t>=2.85&&t<4.55&&!ending&&<div style={{position:'absolute',left:256,top:mix(115,0,phase(t,2.85,3.7)),width:285,height:385,opacity:phase(t,2.85,3.25),background:'#E5EBE2',borderRadius:16,boxShadow:'0 14px 30px #53695824',padding:8,transform:`rotate(${mix(-9,0,phase(t,2.85,3.7))}deg)`}}><Portrait hair={0}/></div>}
      {t>3.0&&t<4.7&&!ending&&<div style={{position:'absolute',left:32,right:32,bottom:22,height:8,borderRadius:12,background:'#DDE6DC'}}><div style={{height:8,width:`${phase(t,3.0,4.05)*100}%`,borderRadius:12,background:C.green}}/></div>}
    </div>
    <div style={{fontSize:25,color:'#52675A',marginTop:24,marginBottom:19,display:'flex',gap:10,alignItems:'center'}}><Icon name="check" size={25}/>{ending?'Готово. Сохраните свой образ.':t>4.1?'Фото загружено':'Фото из галереи или камеры'}</div>
    <div style={{fontSize:30,fontWeight:550,marginBottom:20}}>Что изменим?</div>
    <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:13}}>{items.map(([icon,name])=><div key={name} style={{height:85,border:`1px solid ${active===icon?'#87AE98':'#DDE4DB'}`,borderRadius:16,background:active===icon?C.mint:'#FBFCF9',display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',gap:6,fontSize:21,color:active===icon?C.green:C.ink}}><Icon name={icon} size={27}/>{name}</div>)}</div>
    <div style={{padding:'20px 23px',marginTop:24,border:'1px solid #DCE3D9',borderRadius:16,fontSize:25,color:'#849186',height:60}}>Добавьте свою идею…</div>
    <div style={{marginTop:29,display:'flex',justifyContent:'space-around',borderTop:'1px solid #E4E8E0',paddingTop:26}}>{[['sparkle','Студия'],['grid','Образы'],['photo','Результаты'],['user','Профиль']].map(([i,n])=><div key={n} style={{fontSize:18,color:n==='Студия'?C.green:'#889287',textAlign:'center'}}><Icon name={i} size={25} color={n==='Студия'?C.green:'#889287'}/><div style={{marginTop:8}}>{n}</div></div>)}</div>
  </div>;
}

function Upload({t}:{t:number}){
  const tap=window(t,2.15,2.8,.15);
  const scale=mix(1,.96,window(t,2.27,2.55,.1));
  return <div style={{opacity:1-phase(t,4.25,5),transform:`scale(${scale})`,transformOrigin:'540px 1000px'}}>
    <Studio t={t}/>
    {tap>.005&&<div style={{position:'absolute',left:525,top:786,width:74,height:74,borderRadius:100,background:'#176B5B18',border:'2px solid #176B5B55',opacity:tap,transform:`translate(-50%,-50%) scale(${mix(.5,1.4,phase(t,2.15,2.8))})`}}/>}
  </div>;
}

function HairScene({t}:{t:number}){
  const grid=phase(t,6.35,7.3);
  const exit=1-phase(t,8.65,9.12);
  const hair=t<5.6?0:t<6.3?1:0;
  const hero=phase(t,4.15,5.0);
  return <div style={{opacity:exit}}>
    {grid>.002&&Array.from({length:8},(_,j)=>j+1).map(i=>{
      const p=phase(t,6.35+i*.05,6.93+i*.05);
      return <PhotoCard key={i} x={94+(i%3)*301} y={534+Math.floor(i/3)*365+(1-p)*20} w={280} h={344} hair={i} label={hairNames[i]} opacity={p} selected={i===3&&t>8.2}/>;
    })}
    <PhotoCard x={mix(mix(367,236,hero),94,grid)} y={mix(mix(525,544,hero),534,grid)} w={mix(mix(285,608,hero),280,grid)} h={mix(mix(385,813,hero),344,grid)} hair={hair} label={grid>.5?hairNames[0]:undefined} opacity={hero}/>
    {grid<.8&&t>5.05&&<div style={{position:'absolute',left:405,top:1415,display:'flex',gap:14,opacity:window(t,5.05,6.75,.25)}}>{[0,1,2].map(i=><div key={i} style={{height:10,width:i===hair?55:12,borderRadius:20,background:i===hair?C.green:'#CAD8CC'}}/>)}</div>}
  </div>;
}

function GlassesScene({t}:{t:number}){
  const enter=phase(t,8.7,9.35);
  const index=t<9.8?1:t<10.85?2:3;
  const starts=[9.15,9.8,10.85];
  const opacity=phase(t,8.7,9.1)*(1-phase(t,11.85,12.35));
  const glassOpacity=phase(t,starts[index-1],starts[index-1]+.4);
  return <div style={{opacity}}>
    <PhotoCard x={mix(94,236,enter)} y={mix(899,534,enter)} w={mix(280,608,enter)} h={mix(344,813,enter)} hair={3} glasses={index} glassOpacity={glassOpacity}/>
    <div style={{position:'absolute',top:1421,left:232,display:'flex',gap:25,opacity:enter}}>{[1,2,3].map(i=><div key={i} style={{width:180,height:104,borderRadius:22,border:`2px solid ${i===index?'#7EA98D':'#D9E2D7'}`,background:i===index?C.mint:'#FFFFFF',display:'flex',alignItems:'center',justifyContent:'center',boxShadow:i===index?'0 8px 25px #304D3911':'none',transform:`translateY(${i===index?-5:0}px)`}}><svg width="125" height="75" viewBox="80 104 140 75"><g><PortraitGlasses variant={i}/></g></svg></div>)}</div>
  </div>;
}

// The same frame-aligned glasses artwork used on the face and in option cards.
function PortraitGlasses({variant}:{variant:number}){
  return <g fill="none" stroke={variant===1?'#796356':variant===2?'#9B7651':'#304E46'} strokeWidth={variant===3?5:3} strokeLinecap="round">
    {variant===2?<><circle cx="120" cy="140" r="23"/><circle cx="180" cy="140" r="23"/></>:<><rect x="95" y="120" width="51" height="38" rx={variant===3?12:7}/><rect x="154" y="120" width="51" height="38" rx={variant===3?12:7}/></>}
    <path d="M144 135q6-7 12 0M95 131l-10-4m120 4 10-4"/>
  </g>;
}

function DocumentScene({t}:{t:number}){
  const enter=phase(t,11.9,12.5);
  const sheet=phase(t,13.45,14.35);
  const opacity=1-phase(t,16.0,16.7);
  return <div style={{opacity}}>
    <div style={{position:'absolute',left:mix(225,160,sheet),top:mix(522,520,sheet),width:mix(630,760,sheet),height:mix(836,1003,sheet),background:'#FFF',border:'1px solid #DDE2D8',borderRadius:mix(28,13,sheet),boxShadow:shadow,opacity:enter}}/>
    <PhotoCard x={mix(236,215,sheet)} y={mix(534,588,sheet)} w={mix(608,299,sheet)} h={mix(813,385,sheet)} hair={3} document opacity={enter}/>
    {[1,2,3].map(i=>{const p=phase(t,13.65+i*.08,14.38+i*.08);return <PhotoCard key={i} x={mix(236,215+(i%2)*350,p)} y={mix(534,588+Math.floor(i/2)*426,p)} w={mix(608,299,p)} h={mix(813,385,p)} hair={3} document opacity={p}/>;})}
    {t<13.75&&<div style={{position:'absolute',left:240,top:mix(590,1270,phase(t,12.4,13.4)),width:600,height:2,background:'#79A78D',boxShadow:'0 0 22px 5px #91BEA455',opacity:window(t,12.3,13.75,.2)}}/>}
    <div style={{position:'absolute',top:1552,left:310,width:460,textAlign:'center',opacity:sheet,fontSize:27,color:'#5C7363'}}><span style={{display:'inline-flex',alignItems:'center',gap:12,background:C.mint,borderRadius:30,padding:'12px 25px'}}><Icon name="check" size={26}/>4 фото · 35 × 45 мм</span></div>
  </div>;
}

function PassportScene({t}:{t:number}){
  const enter=phase(t,16,16.75);
  const exit=1-phase(t,18.9,19.6);
  const travel=phase(t,16.1,17.1);
  return <div style={{opacity:enter*exit}}>
    <div style={{position:'absolute',left:126,top:mix(569,481,enter),width:828,height:1097,transform:`rotate(${mix(-2,0,enter)}deg)`}}><StandingPerson smile={phase(t,17.0,17.8)} tilt={mix(0,7,phase(t,16.75,17.7))} photo={travel>.96}/></div>
    {travel<.99&&<PhotoCard x={mix(215,437,travel)} y={mix(588,1002,travel)} w={mix(299,79,travel)} h={mix(385,102,travel)} hair={3} document rotation={mix(0,-4,travel)} opacity={enter}/>}
    <div style={{position:'absolute',top:1495,left:354,opacity:phase(t,17.35,18),transform:`translateY(${mix(12,0,phase(t,17.35,18))}px)`,background:C.mint,borderRadius:34,padding:'16px 31px',display:'flex',gap:12,alignItems:'center',color:C.green,fontSize:30}}><Icon name="check" size={30}/>Сохранено</div>
  </div>;
}

function Footer({t}:{t:number}){
  const end=phase(t,19.2,20.0);
  const beats=[0,2,5,9,12,16,19];
  return <>
    <div style={{position:'absolute',left:110,top:1720,width:860,display:'flex',gap:14,opacity:1-end}}>{beats.map((a,i)=><div key={i} style={{height:7,flex:1,borderRadius:12,background:'#D7E0D5',overflow:'hidden'}}><div style={{height:'100%',width:`${clamp((t-a)/((beats[i+1]??22)-a))*100}%`,background:C.green,borderRadius:12}}/></div>)}</div>
    <div style={{position:'absolute',left:128,top:1756,width:824,textAlign:'center',fontSize:24,color:'#7D8980',opacity:1-end}}>Фото. Идея. Новый образ.</div>
    <div style={{position:'absolute',left:100,top:1702,width:880,height:114,opacity:end,transform:`translateY(${(1-end)*18}px)`,borderRadius:28,background:C.green,color:'#FFF',display:'flex',alignItems:'center',justifyContent:'center',gap:52,boxShadow:'0 16px 38px #176B5B28',fontSize:37,fontWeight:550}}>Начать пользоваться<Icon name="arrow" size={39} color="white"/></div>
    <div style={{position:'absolute',top:1852,width:'100%',textAlign:'center',fontSize:20,color:'#8B958C'}}>Показано на иллюстрациях</div>
  </>;
}

export const WelcomeScene=({t,showFooter=true}:{t:number;showFooter?:boolean})=>{
  return <AbsoluteFill style={{fontFamily:'"Segoe UI",Arial,sans-serif',color:C.ink}}>
    <Background t={t}/><Brand t={t}/><Titles t={t}/>
    {t<5&&<Upload t={t}/>}
    {t>=4.15&&t<9.12&&<HairScene t={t}/>}
    {t>=8.7&&t<12.35&&<GlassesScene t={t}/>}
    {t>=11.9&&t<16.7&&<DocumentScene t={t}/>}
    {t>=16&&t<19.6&&<PassportScene t={t}/>}
    {t>=19&&<div style={{opacity:phase(t,19,19.7),transform:`translateY(${mix(20,0,phase(t,19,19.7))}px)`}}><Studio t={t} ending/></div>}
    {showFooter&&<Footer t={t}/>}
  </AbsoluteFill>;
};
