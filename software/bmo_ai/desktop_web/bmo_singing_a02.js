/* BMO Singing A02 two singing moods 0.6.0. R10-derived drawings, audio-event holds.
   Whole-mix animation approximation; no lyric, vowel or vocal recognition. */
(()=>{'use strict';
const NS='http://www.w3.org/2000/svg', mounted=new WeakMap(),clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));
const valid=['closed','bridge','open','full','round','teeth'];
const number=(x,label)=>{if(!Number.isFinite(x))throw TypeError(label+' must be finite');return x;};
function mapPath(d,fx,fy){return d.replace(/([MLCQHVZ])([^MLCQHVZ]*)/g,(_,cmd,body)=>{const a=(body.match(/[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:e[-+]?\d+)?/gi)||[]).map(Number);return cmd+a.map((x,i)=>+(cmd==='H'?fx(x):cmd==='V'?fy(x):i%2?fy(x):fx(x)).toFixed(3)).join(' ');});}
function makeLegacyRoundArt(doc){
 const data=JSON.parse(doc.getElementById('assets').textContent),neutral=data.mouths.neutral;
 const parse=markup=>{const g=doc.createElementNS(NS,'g');g.innerHTML=markup;return g;};
 const round=parse(neutral.round),opened=parse(neutral.open),closed=parse(neutral.closed);
 const outline=round.querySelector('clipPath path').getAttribute('d');
 const teeth=opened.querySelector('[fill="#F8FCF6"]'),tongue=opened.querySelector('[fill="#50D65E"]');
 // Bake affine changes into path coordinates, keeping the original line widths.
 function mapPath(d,fx,fy){return d.replace(/([MLCQHVZ])([^MLCQHVZ]*)/g,(_,cmd,body)=>{const vals=(body.match(/[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:e[-+]?\d+)?/gi)||[]).map(Number);return cmd+vals.map((v,i)=>Number((cmd==='H'?fx(v):cmd==='V'?fy(v):i%2?fy(v):fx(v)).toFixed(3))).join(' ');});}
 const defs=doc.createElementNS(NS,'g');defs.id='bmo-a02-legacy-art';doc.getElementById('art').appendChild(defs);
 const dimensions={closed:[74,9],parted:[62,24],tight:[36,44],small:[52,46],low:[60,56],mid:[76,68],round:[68,74],body:[94,80],open:[108,86],high:[124,92],wide:[142,84],flat:[62,24],rest:[74,9]};
 for(const [key,[w,h]] of Object.entries(dimensions)){
  const group=doc.createElementNS(NS,'g');group.id='a02-legacy-mouth-'+key;
  if(key==='closed'||key==='rest'){const p=closed.querySelector('path').cloneNode(true);p.setAttribute('d',mapPath(p.getAttribute('d'),x=>500+(x-500)*w/168,y=>399+(y-388)*.4));group.appendChild(p);}
  else{
   const d=mapPath(outline,x=>x*w/58,y=>(y+1.16)*h/73.08),id='a02-legacy-clip-'+key;
   group.setAttribute('transform','translate(500 402)');group.innerHTML=`<defs><clipPath id="${id}"><path d="${d}"/></clipPath></defs><path d="${d}" fill="#299638"/><g clip-path="url(#${id})"></g><path d="${d}" fill="none" stroke="#0B1710" stroke-width="3.7" stroke-linecap="round" stroke-linejoin="round"/>`;
   const contents=group.querySelector('g');
   if(!['tight','parted','flat'].includes(key)){
    const t=teeth.cloneNode(true);t.setAttribute('d',mapPath(t.getAttribute('d'),x=>x*w/198,y=>(y+18)*h*.28/30-h*.55));contents.appendChild(t);
    const l=tongue.cloneNode(true);l.setAttribute('d',mapPath(l.getAttribute('d'),x=>(x-4.86)*w*.72/120,y=>(y-35.34)*h*.48/36.66+h*.12));contents.appendChild(l);
   }
  }
  defs.appendChild(group);
 }
 return{defs,dimensions};
}

function createArt(doc){
 const data=JSON.parse(doc.getElementById('assets').textContent),defs=doc.createElementNS(NS,'g');defs.id='bmo-a02-art';doc.getElementById('art').appendChild(defs);
 const el=(tag)=>doc.createElementNS(NS,tag),dims={},prov={};
 function original(key,emotion,src,sx=1,sy=1){const g=el('g');g.innerHTML=data.mouths[emotion][src];g.id='a02-mouth-'+key;
  for(const p of g.querySelectorAll('path'))p.setAttribute('d',mapPath(p.getAttribute('d'),x=>x*sx,y=>-9+(y+9)*sy));
  for(const n of g.querySelectorAll('[id]')){const old=n.id,id='a02-'+key+'-'+old;n.id=id;for(const c of g.querySelectorAll('[clip-path]'))if(c.getAttribute('clip-path')==='url(#'+old+')')c.setAttribute('clip-path','url(#'+id+')');}
  defs.appendChild(g);prov[key]=emotion+'.'+src;return g;
 }
 // Closed line is byte-for-byte R10 geometry, not a scaled or re-centered copy.
 const closed=el('g');closed.id='a02-mouth-closed';closed.innerHTML=data.mouths.neutral.closed;defs.appendChild(closed);dims.closed=[168,16.5];prov.closed='neutral.closed unchanged';
 const raw=el('g');raw.innerHTML=data.mouths.neutral.open;
 const tongueSource=raw.querySelector('[fill="#50D65E"]').getAttribute('d');
 const teethSource=raw.querySelector('[fill="#F8FCF6"]').getAttribute('d');
 const roundRaw=el('g');roundRaw.innerHTML=data.mouths.neutral.round;
 const roundPath=roundRaw.querySelector('clipPath path').getAttribute('d');
 const frontRaw=el('g');frontRaw.innerHTML=data.mouths.happy.open;
 const frontPath=frontRaw.querySelector('clipPath path').getAttribute('d');
 const smileTeeth=frontRaw.querySelector('[fill="#F8FCF6"]').getAttribute('d');
 function cavity(key,w,h,isRound=false){
  // Use the full approved happy.open outline. Its upper corners and continuous
  // lower U remove the downturned expression without inventing perspective walls.
  const g=el('g'),clip='artfix-clip-'+key;
  const d=isRound?mapPath(roundPath,x=>x*w/58,y=>(y+37.7)*h/73.08):mapPath(frontPath,x=>x*w/238,y=>(y+7)*h/81);
  const teeth=isRound
   ?mapPath(teethSource,x=>x*w/216,y=>(y+18)*h*.26/30)
   :mapPath(smileTeeth,x=>x*w/238,y=>key==='teeth'?(y+12)*h*.55/29:(y+7)*Math.min(h/81,1.25));
  // Keep the approved tongue curve; center its crest and show a shallow lower lobe.
  const tongue=mapPath(tongueSource,x=>(x+.256)*w*(isRound?.78:.60)/119.88,y=>(y-41.106)*h*.48/30+h*.74);
  g.id='a02-mouth-'+key;g.setAttribute('transform','translate(500 385)');
  g.innerHTML=`<defs><clipPath id="${clip}"><path d="${d}"/></clipPath></defs><path d="${d}" fill="#299638"/><g clip-path="url(#${clip})"><path d="${teeth}" fill="#F8FCF6" stroke="#0B1710" stroke-width="2.2" stroke-linejoin="round"/><path d="${tongue}" fill="#50D65E"/></g><path d="${d}" fill="none" stroke="#0B1710" stroke-width="3.7" stroke-linejoin="round" stroke-linecap="round"/>`;
  defs.appendChild(g);dims[key]=[w,h];prov[key]=(isRound?'neutral.round with neutral.open teeth':'happy.open complete contour and teeth')+'; centered neutral.open tongue; R10 colors and stroke widths';
 }
 cavity('bridge',216,66);cavity('open',236,100);cavity('full',252,114);cavity('round',108,116,true);
 // Brief narrowed opening retains green space; never a solid white clenched mouth.
 cavity('teeth',230,54);
 const legacy=makeLegacyRoundArt(doc);defs.appendChild(legacy.defs);
 const eyes=doc.getElementById('eye-happy-open');
 for(const [key,sy,turn] of [['closed',-.57,0],['press',-.33,3],['joyful-closed',1,0],['joyful-press',.82,0]]){const g=el('g');g.id='a02-eye-'+key;defs.appendChild(g);[...eyes.children].forEach((n,i)=>{const q=n.cloneNode(true);q.removeAttribute('id');g.appendChild(q);for(const p of q.querySelectorAll('path')){const b=p.getBBox(),cx=b.x+b.width/2,cy=b.y+b.height/2;p.setAttribute('d',mapPath(p.getAttribute('d'),x=>x,y=>cy+(y-cy)*sy));if(turn)p.setAttribute('transform',`rotate(${i===0?turn:-turn} ${cx} ${cy})`);}});}
 return{defs,dimensions:dims,provenance:prov,legacyDimensions:legacy.dimensions};
}
function attach(doc,opts={}){
 if(mounted.has(doc))return mounted.get(doc);const face=doc.defaultView?.BMOFace;
 if(!face||face.getState().artworkRevision!=='review-r10')throw Error('Approved R10 required');
 const art=createArt(doc),eye=doc.createElementNS(NS,'use'),mouth=doc.createElementNS(NS,'use'),notes=doc.createElementNS(NS,'g'),origEye=doc.getElementById('eyes'),origMouth=doc.getElementById('mouth');
 eye.id='bmo-a02-eyes';mouth.id='bmo-a02-mouth';notes.id='bmo-a02-notes';doc.getElementById('eyePairAdjust').appendChild(eye);doc.getElementById('mouthAdjust').appendChild(mouth);doc.getElementById('lookRoot').appendChild(notes);
 let noteFloor=-Infinity,fallbackNotes=[],priorLevel=0,lastNoteBirth=-10;
 let active=false,saved,visibility,config={bpm:104,notes:true,acting:true,mood:'joyful'},state={active:false},lastT=-1,held='closed',since=-10,lastNote=-10,pressMix=0;
 function configure(o={}){if(o.mood!==undefined){if(!['joyful','gentle'].includes(o.mood))throw Error('Invalid mood');if(config.mood!==o.mood){noteFloor=lastT;fallbackNotes=[];}config.mood=o.mood;}if(o.bpm!==undefined)config.bpm=clamp(number(o.bpm,'bpm'),55,180);if(o.notes!==undefined)config.notes=!!o.notes;if(o.acting!==undefined)config.acting=!!o.acting;return{...config};}configure(opts);eye.style.display=mouth.style.display='none';
 function start(){if(active)return;saved=face.getState();visibility=[origEye.style.visibility,origMouth.style.visibility];origEye.style.visibility=origMouth.style.visibility='hidden';face.setActivity('idle');face.setEmotion('neutral');face.setSpeaking(false);face.look(0,0);face.lookEyes(0,0);active=true;lastT=-1;held='closed';since=-10;lastNote=-10;noteFloor=-Infinity;fallbackNotes=[];priorLevel=0;lastNoteBirth=-10;}
 function stop(){if(!active)return;active=false;origEye.style.visibility=visibility[0];origMouth.style.visibility=visibility[1];eye.style.display=mouth.style.display='none';notes.replaceChildren();face.setSpeaking(false);face.setEmotion(saved.selectedEmotion);face.setActivity(saved.activity);face.look(saved.gaze.x,saved.gaze.y);face.lookEyes(saved.eyeOnly.x,saved.eyeOnly.y);state={...state,active:false,mouth:'original',eyes:'original',noteCount:0};}
 const gentleMap={closed:null,bridge:'low',open:'open',full:'high',round:'round',teeth:'parted'};
 const mouthId=pose=>config.mood==='gentle'&&gentleMap[pose]?'#a02-legacy-mouth-'+gentleMap[pose]:'#a02-mouth-'+pose;
 const probe=doc.createElementNS(NS,'use');probe.setAttribute('visibility','hidden');doc.getElementById('mouthAdjust').appendChild(probe);
 const origins={};
 for(const mood of ['joyful','gentle'])for(const pose of valid){
  const id=mood==='gentle'&&gentleMap[pose]?'#a02-legacy-mouth-'+gentleMap[pose]:'#a02-mouth-'+pose;
  const group=doc.querySelector(id),outline=[...group.querySelectorAll('path[fill="none"]')].at(-1),outlineId=id.slice(1)+'-outline';outline.id=outlineId;
  // Measure the visible outer path, not the unclipped tongue/teeth bounding box.
  probe.setAttribute('href','#'+outlineId);probe.setAttribute('transform',group.getAttribute('transform')||'');
  const b=probe.getBBox(),m=doc.getElementById('lookRoot').getCTM().inverse().multiply(probe.getCTM());
  const p=probe.ownerSVGElement.createSVGPoint();p.x=b.x+b.width;p.y=b.y+b.height*.55;const q=p.matrixTransform(m);
  origins[mood+':'+pose]={x:q.x+27,y:q.y,width:b.width,height:b.height};
 }probe.remove();
 const single='<ellipse cx="0" cy="0" rx="7.8" ry="5.2" transform="rotate(-22)"/><path d="M6 -1V-34" stroke="#0B1710" stroke-width="3.7" stroke-linecap="round" fill="none"/><path d="M6 -34 C10 -29 22 -29 22 -17 C17 -22 12 -21 6 -25Z"/>';
 const pair='<ellipse cx="0" cy="0" rx="7.8" ry="5.2" transform="rotate(-22)"/><ellipse cx="23" cy="-5" rx="7.8" ry="5.2" transform="rotate(-22 23 -5)"/><path d="M6 -1V-33 L29 -38 V-6 M6 -28 L29 -33" fill="none" stroke="#0B1710" stroke-width="3.7" stroke-linecap="round" stroke-linejoin="round"/>';
 function noteMarkup(events,t){return events.filter(e=>e.eventTime>noteFloor&&t>=e.birth&&t-e.birth<e.life).slice(-2).map(e=>{
  const age=Math.floor((t-e.birth)*24+1e-7)/24,u=clamp(age/e.life),p=origins[config.mood+':'+e.mouth]||origins[config.mood+':open'];
  const x=p.x+(e.follow?-2:0)+(config.mood==='joyful'?78:60)*u,y=p.y+(e.follow?28:0)-(config.mood==='joyful'?48:38)*u;
  const alpha=Math.min(1,u/.09,(1-u)/.23),scale=e.follow?.80:1;
  return `<g data-birth="${e.birth}" data-origin-x="${p.x}" data-origin-y="${p.y}" transform="translate(${x.toFixed(2)} ${y.toFixed(2)}) rotate(-8) scale(${scale})" opacity="${alpha.toFixed(2)}" fill="#0B1710">${e.kind==='pair'?pair:single}</g>`;
 }).join('');}
 function update(a={}){
  if(!active)start();const t=Math.max(0,number(a.time??0,'time')),paused=!!a.paused,seek=t<lastT||t-lastT>.35,level=clamp(number(a.level??.6,'level'));
  if(seek){noteFloor=-Infinity;fallbackNotes=[];priorLevel=level;}if(paused)noteFloor=t;
  if(a.visible===false){eye.style.display=mouth.style.display='none';notes.replaceChildren();lastT=t;state={...state,active:true,visible:false,time:t,noteCount:0};return{...state};}
  eye.style.display=mouth.style.display='';let pose=a.mouth,act=a.acting||'closed';
  if(pose!==undefined&&!valid.includes(pose))throw Error('Invalid mouth');if(!['closed','press'].includes(act))throw Error('Invalid acting');
  if(!pose){const threshold=held==='closed'?.075:.035;pose=level<threshold?'closed':level<.20?'bridge':level>.70?'full':'open';if(!seek&&t-since<.10)pose=held;}
  if(paused||(!a.mouth&&level<.025)){pose='closed';act='closed';}if(!config.acting)act='closed';
  if(pose!==held||seek){held=pose;since=t;}mouth.setAttribute('href',mouthId(pose));const eyeKey=config.mood==='joyful'?'joyful-'+act:act;eye.setAttribute('href','#a02-eye-'+eyeKey);
  const onset=clamp(number(a.onset??0,'onset'));
  let events=Array.isArray(a.noteEvents)?a.noteEvents:null;
  if(!events){
   const gap=config.mood==='joyful'?2.2:3.6;
   if(!paused&&!seek&&pose!=='closed'&&level>.22&&t-lastNoteBirth>=gap&&(onset>.45||level-priorLevel>.16)){
    fallbackNotes.push({birth:t,eventTime:t,life:config.mood==='joyful'?1.55:1.8,mouth:pose,kind:'single',follow:false});lastNoteBirth=t;
   }
   fallbackNotes=fallbackNotes.filter(e=>t-e.birth<e.life);events=fallbackNotes;
  }
  notes.innerHTML=config.notes&&!paused?noteMarkup(events,t):'';
  priorLevel=level;
  lastT=t;state={active:true,visible:true,time:t,mood:config.mood,mouth:pose,mouthAsset:mouthId(pose),eyes:eyeKey,paused,noteCount:notes.childElementCount,source:a.mouth?'audio-event-plan':'level-fallback',bpm:a.bpm??config.bpm};return{...state};
 }
 const api={start,stop,update,configure,getState:()=>({...state}),getArtInfo:()=>({dimensions:{...art.dimensions},provenance:{...art.provenance},legacyDimensions:{...art.legacyDimensions},noteOrigins:{...origins}}),dispose(){stop();eye.remove();mouth.remove();notes.remove();art.defs.remove();mounted.delete(doc);}};mounted.set(doc,api);return api;
}
const api=Object.freeze({version:'0.6.0',attach});if(typeof window!=='undefined')window.BMOSingingR10A02=api;if(typeof module!=='undefined')module.exports=api;
})();
