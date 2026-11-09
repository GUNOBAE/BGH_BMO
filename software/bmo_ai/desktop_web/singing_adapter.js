/* TWO MOODS 06 product boundary. Python/ALSA owns audio and its sample clock.
   Legacy {time,mouth} cues remain the explicit fallback; they are NEVER passed
   off as whole-mix analysis or translated into the new planner's mouth enum. */
(()=>{'use strict';
const HASH=/^[a-f0-9]{64}$/,MOODS=new Set(['joyful','gentle']),MAX_BYTES=4*1024*1024;
function validateProfile(value,descriptor,duration){
 if(!value||value.schema!=='bmo.music-profile.v1'||value.analyzer_version!=='1.0.0'
  ||value.audio_sha256!==descriptor.audio_sha256||!HASH.test(value.audio_sha256)
  ||value.step!==.02||!Number.isFinite(value.duration)||value.duration<=0||value.duration>600.02)throw Error('profile_contract');
 const n=value.level?.length;
 if(!Number.isInteger(n)||n<1||n>30000||Math.abs(value.duration-n*value.step)>.021
  ||Number.isFinite(duration)&&Math.abs(value.duration-duration)>.041)throw Error('profile_duration');
 for(const name of ['level','onset','energy','tempo','confidence','beats']){
  const rows=value[name];if(!Array.isArray(rows)||rows.length!==n)throw Error('profile_arrays');
  const max=name==='tempo'?180:name==='beats'?1801:1;
  if(rows.some(x=>typeof x!=='number'||!Number.isFinite(x)||x<0||x>max))throw Error('profile_values');
 }
 return value;
}
class ProductSinging {
 constructor(frame){
  this.frame=frame;this.context=null;this.latest=null;this.serial=0;this.pending=null;this.renderer=null;
  this.document=null;this.faceAdapter=null;this.owner=null;this.profile=null;this.plan=null;this.mood=null;
  this.cache=new Map();this.retired=new Set();this.closed=false;this.evidence={active:false,status:'legacy_cues',approximation:'whole_mix_energy_not_lyrics',clock:'python_pcm_position_seconds'};
  frame.addEventListener('load',()=>this.retire('frame_reloaded'));
 }
 identity(next){const m=next?.media;return m?JSON.stringify([next.mode_generation,m.request_id,m.playback_generation]):null;}
 retire(reason='retired'){
  if(this.context){this.retired.add(this.context.key);if(this.retired.size>128)this.retired.delete(this.retired.values().next().value);}
  this.serial++;this.pending?.abort();this.pending=null;this.context=null;this.latest=null;this.profile=null;this.plan=null;
  this.release();this.evidence={...this.evidence,active:false,status:reason};
 }
 release(){
  // stop() restores the renderer's own layers first. releaseVisual then applies
  // the latest speech snapshot, so a saved old song emotion cannot win.
  try{this.renderer?.stop();}catch(_){}
  if(this.faceAdapter&&this.owner)this.faceAdapter.releaseVisual(this.owner);
  this.faceAdapter=null;this.owner=null;
 }
 compatible(next){
  const m=next?.media,d=m?.singing_profile;
  return !this.closed&&next?.mode==='music'&&next.audio_owner==='music'
   &&(m.display_mode||m.variant)==='face'&&['playing','paused'].includes(m.state)&&(m.state==='paused'||m.output_started===true)
   &&Number.isFinite(m.position_seconds)&&m.position_seconds>=0
   &&typeof m.request_id==='string'&&Number.isInteger(m.playback_generation)&&Number.isInteger(next.mode_generation)
   &&d?.status==='ready'&&HASH.test(d.audio_sha256||'')&&HASH.test(d.profile_sha256||'')
   &&typeof d.url==='string'&&d.url.startsWith('/api/music-singing/');
 }
 sync(next,{stopping=false}={}){
  if(stopping){this.retire('stopping');return;}
  if(!this.compatible(next)){
   // Temporary missing analysis/output is a fallback, not a fake .6 signal.
   this.serial++;this.pending?.abort();this.pending=null;this.context=null;this.latest=null;
   this.profile=null;this.plan=null;this.release();
   this.evidence={...this.evidence,active:false,status:next?.media?.singing_profile?.status||'legacy_cues'};return;
  }
  const key=this.identity(next);if(this.retired.has(key))return;
  const descriptor=next.media.singing_profile,profileKey=descriptor.audio_sha256+':'+descriptor.profile_sha256;
  const changed=this.context?.key!==key||this.context.profileKey!==profileKey;
  this.latest=next;
  if(changed){
   this.serial++;this.pending?.abort();this.pending=null;this.release();this.profile=null;this.plan=null;
   this.context={key,profileKey,serial:this.serial};
   this.evidence={...this.evidence,active:false,status:'analysis_loading',request_id:next.media.request_id,
    mode_generation:next.mode_generation,playback_generation:next.media.playback_generation,audio_sha256:descriptor.audio_sha256};
   const cached=this.cache.get(profileKey);
   if(cached){this.profile=cached;this.draw();}else this.load(descriptor,this.context);
  }else this.draw();
 }
 async load(descriptor,context){
  const controller=new AbortController();this.pending=controller;
  const timeout=setTimeout(()=>controller.abort(),5000);
  try{
   const response=await fetch(descriptor.url,{cache:'no-store',signal:controller.signal});
   if(!response.ok)throw Error('profile_unavailable');
   const length=Number(response.headers.get('Content-Length'));
   if(length>MAX_BYTES)throw Error('profile_bound');
   const buffer=await response.arrayBuffer();if(buffer.byteLength>MAX_BYTES)throw Error('profile_bound');
   const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',buffer))].map(x=>x.toString(16).padStart(2,'0')).join('');
   if(hash!==descriptor.profile_sha256)throw Error('profile_sha');
   const value=validateProfile(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(buffer)),descriptor,this.latest?.media?.duration_seconds);
   if(this.closed||this.context!==context||this.serial!==context.serial||!this.compatible(this.latest)
      ||this.identity(this.latest)!==context.key||this.retired.has(context.key))return;
   this.cache.set(context.profileKey,value);while(this.cache.size>2)this.cache.delete(this.cache.keys().next().value);
   this.profile=value;this.draw();
  }catch(_){if(this.context===context){this.release();this.evidence={...this.evidence,active:false,status:'analysis_invalid_legacy_cues'};}}
  finally{clearTimeout(timeout);if(this.pending===controller)this.pending=null;}
 }
 draw(){
  const next=this.latest,context=this.context;
  if(!this.profile||!context||!this.compatible(next)||this.retired.has(context.key)||this.identity(next)!==context.key)return;
  const adapter=window.BMOIntegration,doc=this.frame.contentDocument;
  if(!adapter||adapter.closed||!doc||!doc.defaultView?.BMOFace)return;
  const mood=MOODS.has(next.media.singing_mood)?next.media.singing_mood:'joyful';
  try{
   if(this.document!==doc){this.release();this.renderer?.dispose();this.renderer=null;this.document=doc;}
   if(!this.renderer)this.renderer=window.BMOSingingR10A02.attach(doc,{mood,notes:true});
   if(this.faceAdapter!==adapter){
    this.release();this.owner='singing:'+context.key;this.faceAdapter=adapter;
    if(!adapter.claimVisual(this.owner,()=>this.retire('conversation_priority'))){
     if(this.context===context){this.release();this.evidence={...this.evidence,active:false,status:'visual_owner_unavailable'};}
     return;
    }
   }
   if(!this.plan||this.mood!==mood){this.plan=window.BMOSingingPlan.compile(this.profile,{mood});this.mood=mood;this.renderer.configure({mood});}
   // No player.currentTime, autonomous demo timer or independent beat clock.
   // Every rendered sample uses the current, consumed ALSA/PCM position.
   const time=next.media.position_seconds,features=window.BMOMusicAnalysis.at(this.profile,time),plan=window.BMOSingingPlan.at(this.plan,time);
   if(!features||!Number.isFinite(features.level))throw Error('analysis_sample');
   const rendered=this.renderer.update({time,...features,...plan,paused:next.media.state!=='playing',visible:!document.hidden});
   this.evidence={...this.evidence,...rendered,status:'two_moods_06',audio_sha256:this.profile.audio_sha256,
    profile_sha256:next.media.singing_profile.profile_sha256,approximation:'whole_mix_energy_not_lyrics',clock:'python_pcm_position_seconds'};
  }catch(_){this.release();this.evidence={...this.evidence,active:false,status:'renderer_unavailable_legacy_cues'};}
 }
 close(){this.retire('closed');this.closed=true;this.renderer?.dispose();this.renderer=null;this.cache.clear();}
 snapshot(){return {...this.evidence,
  renderer_version:this.evidence.active?window.BMOSingingR10A02?.version:null,
  planner_version:this.evidence.active?window.BMOSingingPlan?.version:null,
  analyzer_version:this.evidence.active?window.BMOMusicAnalysis?.version:null};}
}
window.BMOProductSinging=Object.freeze({attach:frame=>new ProductSinging(frame)});
})();
