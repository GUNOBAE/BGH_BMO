/* BMO Singing Plan 1.2.0: deterministic, whole-mix animation cues.
   Energy/onset approximation only: no lyrics, phonemes, vocals or emotion recognition.
   No randomness, beat clock, periodic acting loop, audio ownership or runtime timer. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;if(root)root.BMOSingingPlan=api;})(typeof window!=='undefined'?window:typeof self!=='undefined'?self:globalThis,function(){'use strict';
const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));
const number=(x,fallback=0)=>typeof x==='number'&&Number.isFinite(x)?x:fallback;
const THRESHOLDS=Object.freeze({gateOn:.11,gateOff:.055,gateAttack:.06,gateRelease:.14,drawHold:.10,teethOnset:.50,teethRise:.045,teethHold:.10,teethGap:.38,roundBuild:.34,roundDrop:.20,roundHold:.22,roundMinimum:.18,pressRise:.18,pressLevel:.65,pressBuild:.46,pressHold:.72,pressGap:4,noteGap:2.8,noteLife:1.25});
function compile(profile,options={}){
 const mood=options.mood??'joyful';if(!['joyful','gentle'].includes(mood))throw new TypeError('Invalid singing mood');
 if(!profile||!profile.level||!Number.isFinite(profile.step)||profile.step<=0)throw new TypeError('A music profile with level[] and positive step is required');
 const dt=profile.step,n=profile.level.length,duration=n*dt;
 const levels=new Float64Array(n),onsets=new Float64Array(n),prefix=new Float64Array(n+1),onPrefix=new Float64Array(n+1);
 let sm=n?clamp(number(profile.level[0])):0;
 for(let i=0;i<n;i++){const raw=clamp(number(profile.level[i])),tau=raw>sm?.025:.065;sm+=(raw-sm)*(1-Math.exp(-dt/tau));levels[i]=sm;onsets[i]=clamp(number(profile.onset&&profile.onset[i]));prefix[i+1]=prefix[i]+sm;onPrefix[i+1]=onPrefix[i]+onsets[i];}
 const avg=(arr,a,b)=>{a=Math.max(0,Math.min(n,a));b=Math.max(a,Math.min(n,b));return b>a?(arr[b]-arr[a])/(b-a):0;};
 const before=(t,s)=>Math.max(0,Math.floor((t-s)/dt+1e-8));
 const events=[{time:0,mouth:'closed',acting:'closed'}],noteTimes=[];
 let active=false,highFor=0,lowFor=0,phraseStart=0,roundArmed=false,roundUntil=-1,roundStart=-10,broadFor=0;
 let mouth='closed',acting='closed',lastDraw=-10,lastTeeth=-10,teethUntil=-1,lastNote=-10;
 let pressCandidate=null,pressStart=-10,pressPeak=0,lastPressEnd=-10,aperture='open';
 function record(t,m,a){const last=events[events.length-1];if(last.mouth===m&&last.acting===a)return;if(Math.abs(last.time-t)<dt*.2){last.mouth=m;last.acting=a;}else events.push({time:Number(t.toFixed(6)),mouth:m,acting:a});}
 function note(t){if(t-lastNote>=THRESHOLDS.noteGap){noteTimes.push(Number(t.toFixed(6)));lastNote=t;}}
 for(let i=0;i<n;i++){
  const t=i*dt,l=levels[i],on=onsets[i],prior=levels[before(t,.10)]??l,rise=l-prior;
  highFor=l>=THRESHOLDS.gateOn?highFor+dt:0;lowFor=l<THRESHOLDS.gateOff?lowFor+dt:0;
  if(!active&&highFor>=THRESHOLDS.gateAttack-dt*.1){active=true;phraseStart=t;roundArmed=false;roundUntil=-1;roundStart=-10;broadFor=0;aperture=l>.70?'full':l<.22?'bridge':'open';}
  if(active&&lowFor>=THRESHOLDS.gateRelease-dt*.1){active=false;pressCandidate=null;roundUntil=teethUntil=-1;if(acting==='press')lastPressEnd=t;acting='closed';}
  if(!active){mouth='closed';acting='closed';record(t,mouth,acting);continue;}
  const age=t-phraseStart;
  // The lip opening follows a hysteretic amplitude envelope. No scheduled closures.
  if(aperture==='full'){if(l<.59)aperture=l<.20?'bridge':'open';}
  else if(aperture==='bridge'){if(l>.29)aperture=l>.72?'full':'open';}
  else if(l>.72)aperture='full';else if(l<.20)aperture='bridge';
  // Short contacts follow actual rising attacks; the onset is still whole-mix.
  const attack=on>THRESHOLDS.teethOnset&&rise>THRESHOLDS.teethRise&&l>.27;
  if(attack){roundArmed=true;if(t-roundStart>=THRESHOLDS.roundMinimum)roundUntil=-1;if(t-lastTeeth>=THRESHOLDS.teethGap){lastTeeth=t;teethUntil=t+THRESHOLDS.teethHold;}if(on>.85&&rise>.16)note(t);}
  // A rounded release follows a previously held opening and a real energy drop.
  // Re-arm only after a fresh rising attack; constant sounds cannot cycle vowels.
  const prevBroad=broadFor;broadFor=mouth==='open'||mouth==='full'?broadFor+dt:0;
  let peak=l;for(let j=before(t,.60);j<=i;j++)peak=Math.max(peak,levels[j]);
  const falling=peak-l>Math.max(.10,peak*THRESHOLDS.roundDrop)&&l<prior-.025;
  const quietAttack=avg(onPrefix,before(t,.08),i+1)<.30;
  if(roundArmed&&Math.max(broadFor,prevBroad)>=THRESHOLDS.roundBuild&&l>.18&&falling&&quietAttack&&t>=teethUntil){
   roundArmed=false;roundStart=t;roundUntil=t+THRESHOLDS.roundHold;
  }
  // Eye effort requires a sustained rise over a previous baseline, not a clock.
  // Startup at a constant high level is deliberately not treated as effort.
  const baseline=avg(prefix,before(t,.80),before(t,.22));
  if(acting==='closed'&&age>.7&&t-lastPressEnd>=THRESHOLDS.pressGap){
   if(!pressCandidate&&l>THRESHOLDS.pressLevel&&l-baseline>THRESHOLDS.pressRise)pressCandidate={start:t,base:baseline};
   if(pressCandidate){
    if(l<THRESHOLDS.pressLevel-.045||l<pressCandidate.base+.12)pressCandidate=null;
    else if(t-pressCandidate.start>=THRESHOLDS.pressBuild){acting='press';pressStart=t;pressPeak=l;pressCandidate=null;roundUntil=-1;note(t);}
   }
  }
  if(acting==='press'){pressPeak=Math.max(pressPeak,l);if(t-pressStart>=THRESHOLDS.pressHold&&(l<.58||l<pressPeak*.80)){acting='closed';lastPressEnd=t;}}
  let desired=t<teethUntil?'teeth':t<roundUntil?'round':aperture;
  // If silence closure is already pending, do not flash an intermediate drawing.
  if(lowFor>0&&l<THRESHOLDS.gateOff)desired=mouth;
  // Contacts hold 100ms. Rounded releases hold at least180ms before a new attack.
  const mustReleaseTeeth=mouth==='teeth'&&t>=teethUntil;
  if(mouth==='round'&&t-lastDraw<THRESHOLDS.roundMinimum-dt*.1)desired='round';
  if(desired!==mouth&&(t-lastDraw>=THRESHOLDS.drawHold-dt*.1||mustReleaseTeeth)){if(desired==='teeth')teethUntil=Math.max(teethUntil,t+THRESHOLDS.teethHold);if(desired==='round')roundUntil=Math.max(roundUntil,t+THRESHOLDS.roundMinimum);mouth=desired;lastDraw=t;}
  record(t,mouth,acting);
 }
 if(n&&(events[events.length-1].mouth!=='closed'||events[events.length-1].acting!=='closed'))events.push({time:Number(duration.toFixed(6)),mouth:'closed',acting:'closed'});
 // Notes are separate, deterministic sound-event cues. The selected mood changes
 // density/appearance, never a claimed inference about the song's emotion.
 const noteEvents=[];let priorEvent=-10,ei=0,ordinal=0,priorEffort='closed';noteTimes.length=0;
 for(let i=0;i<n;i++){
  const t=i*dt;while(ei+1<events.length&&events[ei+1].time<=t+1e-8)ei++;
  const e=events[ei],l=levels[i],on=onsets[i],rise=l-levels[before(t,.10)],effort=e.acting==='press'&&priorEffort!=='press';priorEffort=e.acting;
  const gap=mood==='joyful'?2.2:3.6;
  if(e.mouth==='closed'||l<.22||t-priorEvent<gap||!((on>.25&&rise>.04)||rise>.12||effort))continue;
  const birth=Number(t.toFixed(6)),strong=on>.60&&l>.32,paired=mood==='joyful'&&strong&&ordinal%3===2;
  noteTimes.push(birth);noteEvents.push({birth,eventTime:birth,mouth:e.mouth,life:mood==='joyful'?1.55:1.8,kind:paired?'pair':'single',follow:false});
  if(mood==='joyful'&&strong&&!paired)noteEvents.push({birth:Number((birth+.22).toFixed(6)),eventTime:birth,mouth:e.mouth,life:1.55,kind:'single',follow:true});
  priorEvent=t;ordinal++;
 }
 const counts={};for(const e of events)counts[e.mouth]=(counts[e.mouth]||0)+1;
 return{schema:'bmo.singing-plan.v1',version:'1.2.0',mood,duration,step:dt,events,noteTimes,noteEvents,summary:{events:events.length,notes:noteTimes.length,mouthChanges:counts,method:'whole-mix amplitude hysteresis and sustained-rise events; no phoneme, vocal or emotion detection'}};
}
function lastAt(a,time,key){let lo=0,hi=a.length-1,best=-1;while(lo<=hi){const m=(lo+hi)>>1,v=key?a[m][key]:a[m];if(v<=time+1e-8){best=m;lo=m+1;}else hi=m-1;}return best;}
function at(plan,time){
 if(!plan||!Array.isArray(plan.events))throw new TypeError('A compiled singing plan is required');
 const t=Math.max(0,number(time)),i=lastAt(plan.events,t,'time'),e=i>=0?plan.events[i]:{mouth:'closed',acting:'closed'},ni=lastAt(plan.noteTimes||[],t),noteTime=ni>=0?plan.noteTimes[ni]:-Infinity;
 const noteEvents=(plan.noteEvents||[]).filter(x=>t>=x.birth&&t-x.birth<x.life);
 return{mouth:e.mouth,acting:e.acting,noteEvents,noteTime,noteAge:Number.isFinite(noteTime)?t-noteTime:Infinity};
}
return Object.freeze({version:'1.2.0',compile,at,thresholds:THRESHOLDS});
});
