import React, {useId} from 'react';

export const C={paper:'#F6F5F3',ink:'#29343B',green:'#176B5B',mint:'#E6F1EB',line:'#DCE0DF',skin:'#DEAB87',skinLight:'#F2C5A1',hair:'#44332F',hairLight:'#655044',clay:'#C08068'};

export const Icon=({name,size=32,color=C.green}: {name:string;size?:number;color?:string})=>{
  const paths:Record<string,React.ReactNode>={
    plus:<path d="M12 5v14M5 12h14"/>,
    hair:<><circle cx="5" cy="6" r="3"/><circle cx="5" cy="18" r="3"/><path d="m8 8 12 12M8 16 20 4"/></>,
    glasses:<><path d="M3 9h6l2 3h2l2-3h6l1 3-1 5h-6l-2-5h-2l-2 5H3l-1-5 1-3ZM3 9l-1-4m19 4 1-4"/></>,
    clothes:<path d="m7 3-5 4 4 6 2-2v10h8V11l2 2 4-6-5-4c-1 4-9 4-10 0Z"/>,
    photo:<><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="8" r="2"/><path d="m3 18 5-5 4 3 4-6 5 8"/></>,
    document:<><path d="M6 2h8l4 4v16H6Z M14 2v5h4"/><path d="M9 12h6m-6 4h6"/></>,
    check:<path d="m5 12 4 4L20 5"/>,
    arrow:<path d="M4 12h15m-5-5 5 5-5 5"/>,
    grid:<><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></>,
    user:<><circle cx="12" cy="7" r="4"/><path d="M3 22c0-11 18-11 18 0"/></>,
    sparkle:<path d="m12 2 3 7 7 3-7 3-3 7-3-7-7-3Z"/>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">{paths[name]||paths.photo}</svg>;
};

export const Glasses=({variant=0,opacity=1}: {variant?:number;opacity?:number})=>{
  if(!variant)return null;
  return <g opacity={opacity} fill="#F2F7F5" fillOpacity=".24" stroke={variant===1?'#796356':variant===2?'#9B7651':'#304E46'} strokeWidth={variant===3?4.7:2.8} strokeLinecap="round" strokeLinejoin="round">
    {variant===2?<><circle cx="120" cy="140" r="23"/><circle cx="180" cy="140" r="23"/></>:
      <><rect x="95" y="120" width="51" height="38" rx={variant===3?12:7}/><rect x="154" y="120" width="51" height="38" rx={variant===3?12:7}/></>}
    <path d="M144 135q6-7 12 0M95 131l-10-4m120 4 10-4" fill="none"/>
  </g>;
};

export const Portrait=({hair=0,glasses=0,glassOpacity=1,document=false,smile=0,headOnly=false}: {hair?:number;glasses?:number;glassOpacity?:number;document?:boolean;smile?:number;headOnly?:boolean})=>{
  const id=useId().replace(/:/g,'');
  const long=[2,3,4,5,6].includes(hair);
  return <svg viewBox={headOnly?'0 0 300 267':'0 0 300 390'} width="100%" height="100%" preserveAspectRatio="xMidYMid meet">
    <defs>
      <linearGradient id={'skin'+id} x1="0" x2="1" y1="0" y2=".8"><stop stopColor={C.skinLight}/><stop offset="1" stopColor={C.skin}/></linearGradient>
      <linearGradient id={'top'+id} x1="0" x2="0" y1="0" y2="1"><stop stopColor={document?'#F9F8F4':'#AFC8B9'}/><stop offset="1" stopColor={document?'#E9EAE3':'#8BAE9A'}/></linearGradient>
    </defs>
    {long&&<path d={hair===5?'M72 69Q45 135 52 329q49 28 96-13 42 43 102 12 8-144-27-257Z':'M81 64Q52 128 60 296q28 30 89-5 53 31 90 4 9-111-22-230Z'} fill={C.hair}/>}
    {hair===6&&<path d="M216 82q50-8 56 49-11 43-34 99 2-62-18-91Z" fill={C.hair}/>}
    {hair===7&&<><circle cx="155" cy="46" r="35" fill={C.hair}/><path d="M129 41q18-11 36-2" stroke={C.hairLight} strokeWidth="4" fill="none"/></>}
    <path d="M117 220v36q-9 13-45 23L36 301Q20 338 14 390h272q-9-58-28-91l-59-33q-16-8-17-15v-33" fill={'url(#skin'+id+')'}/>
    <path d="M115 222q37 26 70-3l-1 24q-37 24-68-6Z" fill="#BA7F66" opacity=".4"/>
    <path d="M100 267q49 36 99-1l59 33q20 29 29 91H14q5-58 24-87Z" fill={'url(#top'+id+')'}/>
    {document?<><path d="m101 268 47 31-21 34-29-47m101-19-50 32 23 34 30-46" fill="#FFFEFB" stroke="#D5DCD4" strokeWidth="1.2"/><path d="M149 298v92" stroke="#D3DAD2" strokeWidth="1.2"/><circle cx="156" cy="352" r="2" fill="#A7B4AB"/></>:
      <><path d="M98 268q47 42 103-2" fill="none" stroke="#C5D8CC" strokeWidth="10"/><path d="m50 318-9 72m208-72 11 72" stroke="#759B84" strokeWidth="2" opacity=".45"/></>}
    <ellipse cx="84" cy="150" rx="15" ry="26" fill={C.skin}/><ellipse cx="216" cy="150" rx="15" ry="26" fill={C.skin}/>
    <path d="M91 80q57-40 118 2l2 81q-6 73-61 87-52-12-61-82Z" fill={'url(#skin'+id+')'}/>
    <path d="M93 174q5 37 27 52-21-7-27-30Z" fill="#C78D71" opacity=".3"/>
    <path d="M94 126q22-12 42-3m28 0q22-8 39 4" stroke={C.hair} strokeWidth="4.2" strokeLinecap="round" fill="none"/>
    <path d="M103 140q17-9 32 0m31 0q17-9 31 0" stroke="#64493D" strokeWidth="2.7" strokeLinecap="round" fill="none"/>
    <ellipse cx="121" cy="142" rx="4.4" ry="5.4" fill="#3E4037"/><ellipse cx="179" cy="142" rx="4.4" ry="5.4" fill="#3E4037"/>
    <circle cx="122" cy="140" r="1.2" fill="white"/><circle cx="180" cy="140" r="1.2" fill="white"/>
    <path d="m151 143-5 33q5 5 12 0" stroke="#BC8369" strokeWidth="2.5" strokeLinecap="round" fill="none"/>
    <ellipse cx="107" cy="173" rx="12" ry="5" fill="#D8927D" opacity=".32"/><ellipse cx="194" cy="173" rx="12" ry="5" fill="#D8927D" opacity=".32"/>
    <path d={`M132 199q18 ${5+smile*12} 36 -1`} fill="none" stroke="#965E52" strokeWidth="2.8" strokeLinecap="round"/>
    <path d="M140 217q11 3 22-1" stroke="#D49C82" strokeWidth="2" fill="none"/>
    {hair===0&&<><path d="M86 147Q63 97 85 66q35-43 105-24 42 20 26 110l-13-49q-41 14-78-9-24 19-31 53Z" fill={C.hair}/><path d="M100 71q40-23 81-9" stroke={C.hairLight} strokeWidth="7" strokeLinecap="round" fill="none"/></>}
    {hair===1&&<><path d="M86 166Q58 91 87 65q47-38 101-21 51 24 29 135l-12-61q-24-8-54-40-29 28-56 34Z" fill={C.hair}/><path d="M87 115q-20 51-8 127l32-12-19-70M213 112q15 51 10 125l-33-8 19-70" fill={C.hair}/><path d="M93 74q22-16 43-17" stroke={C.hairLight} strokeWidth="5" fill="none" strokeLinecap="round"/></>}
    {hair===2&&<><path d="M84 149Q62 82 91 59q65-38 116 11 21 27 9 86l-16-54q-46 13-64-25-34 23-45 76Z" fill={C.hair}/><path d="M76 153v115m148-112-2 112" stroke={C.hairLight} opacity=".65" strokeWidth="4" strokeLinecap="round"/></>}
    {hair===3&&<><path d="M87 164Q64 81 96 58q52-28 102 4 28 35 15 97l-13-58q-39 0-51-24-39 24-57 86Z" fill={C.hair}/><path d="M77 121q-15 43 4 78-24 44-3 71m145-149q18 46-1 80 18 30 0 62" fill="none" stroke={C.hairLight} strokeWidth="10" strokeLinecap="round"/></>}
    {hair===4&&<><path d="M79 127q-20-63 23-83 63-28 107 14 26 32 8 94l-14-38q-66-7-102-8Z" fill={C.hair}/>{[[84,79],[110,53],[140,46],[170,47],[199,65],[219,91],[75,115],[220,129],[66,151],[232,164],[63,195],[234,204],[76,238],[216,246]].map(([x,y],i)=><circle key={i} cx={x} cy={y} r={18+i%3*3} fill={i%3?C.hair:C.hairLight}/>)}<path d="M91 104q10-28 43-24m11 0q25-16 47 6" fill="none" stroke={C.hairLight} strokeWidth="6" strokeLinecap="round"/></>}
    {hair===5&&<><path d="M86 163Q67 89 91 60q65-39 113 4 32 40 12 101l-10-57q-41-11-51-37-40 32-62 84Z" fill={C.hair}/><path d="M70 163q12 49 1 117m159-118q-7 64 2 115" fill="none" stroke={C.hairLight} strokeWidth="4" strokeLinecap="round"/><path d="M114 59q12-6 34-8" fill="none" stroke={C.hairLight} strokeWidth="4" strokeLinecap="round"/></>}
    {hair===6&&<><path d="M87 155Q65 95 93 65q64-42 112 2 28 34 11 94l-15-61q-50 20-65-18-27 28-42 71Z" fill={C.hair}/><path d="M166 58q22 4 31 15" stroke={C.hairLight} strokeWidth="6" fill="none" strokeLinecap="round"/></>}
    {hair===7&&<><path d="M87 151Q64 98 94 64q61-39 109 2 27 34 12 84l-14-55q-57 10-65-15-27 29-43 70Z" fill={C.hair}/><path d="M108 64q21-13 44-13" stroke={C.hairLight} strokeWidth="5" fill="none" strokeLinecap="round"/></>}
    {hair===8&&<><path d="M85 153Q61 94 94 62q57-43 112 4 27 37 10 91l-17-57q-38 28-89 19l-17 36Z" fill={C.hair}/><path d="M97 104q41-5 80-38" stroke={C.hairLight} strokeWidth="6" fill="none" strokeLinecap="round"/><path d="m202 106 13 26" stroke={C.hairLight} strokeWidth="4" fill="none"/></>}
    <Glasses variant={glasses} opacity={glassOpacity}/>
  </svg>;
};

export const StandingPerson=({smile=1,photo=false,tilt=0}: {smile?:number;photo?:boolean;tilt?:number})=>{
  const id=useId().replace(/:/g,'');
  return <svg viewBox="0 0 800 1060" width="100%" height="100%">
    <defs><linearGradient id={'book'+id}><stop stopColor="#F3F1E9"/><stop offset=".45" stopColor="#FFFEF9"/><stop offset=".5" stopColor="#DBDED3"/><stop offset=".55" stopColor="#F7F7ED"/><stop offset="1" stopColor="#FEFEF8"/></linearGradient></defs>
    <ellipse cx="410" cy="1015" rx="207" ry="22" fill="#D8E2D9" opacity=".75"/>
    <path d="m291 610-28 352 104 0 50-272 25 272 108-1-14-354Z" fill="#A7A58C"/>
    <path d="m411 646-37 291m98-286 39 287" fill="none" stroke="#929780" strokeWidth="3"/>
    <path d="M263 961q-42 3-62 32-5 24 52 23h117v-60Zm180-3v56h130q33-2 12-21-18-24-48-31Z" fill="#354F48"/>
    <path d="M256 371q75-58 166-47 73-10 143 48l-11 252q-74 36-138 11-62 20-132-6Z" fill="#AFC5B3"/>
    <path d="M356 336q42 35 83 0" stroke="#DBE3D3" strokeWidth="16" fill="none"/>
    <path d="m264 414 15 188m258-188-5 185" stroke="#8FAC96" strokeWidth="3" fill="none"/>
    <g transform={`rotate(${tilt} 402 220)`}><svg x="285" y="95" width="238" height="244" viewBox="0 0 300 267"><Portrait hair={3} smile={smile} headOnly/></svg></g>
    <path d="M269 390q-38 46-34 109l78 99 55-31-68-76 18-89Z" fill="#AFC5B3"/>
    <path d="M540 389q41 30 48 112l-97 111-51-44 92-82-27-84Z" fill="#AFC5B3"/>
    <g transform="rotate(-4 404 564)">
      <path d="m262 459 147 19 168-24 1 207-168 26-151-25Z" fill="#176B5B"/>
      <path d="m271 466 138 25 154-31 1 190-154 27-139-29Z" fill={'url(#book'+id+')'}/>
      <path d="M409 491v186" stroke="#CCCFC4" strokeWidth="2"/>
      <rect x="301" y="505" width="77" height="99" rx="2" fill="#E1E6DC"/>
      {photo&&<svg x="301" y="505" width="77" height="99" viewBox="0 0 300 390"><rect width="300" height="390" fill="white"/><Portrait hair={3} document/></svg>}
      <path d="M433 506h102m-102 19h86m-86 22h102m-102 20h81m-81 36h99" stroke="#D1D7CB" strokeWidth="5" strokeLinecap="round"/>
      <path d="M301 622h73" stroke="#BDCDBD" strokeWidth="4" strokeLinecap="round"/>
    </g>
    <path transform="translate(-22 70)" d="M320 545q-7-18-21-8-15 16 2 38l40 25q14 9 27-1 7-13-7-23l-29-21q19-7 15-17-11-8-27 7Z" fill={C.skinLight}/>
    <path transform="translate(26 45)" d="M509 546q14-17 25-4 10 18-10 35l-39 23q-17 7-27-7-3-13 14-20l26-16q-17-10-9-19 9-6 20 8Z" fill={C.skinLight}/>
  </svg>;
};
