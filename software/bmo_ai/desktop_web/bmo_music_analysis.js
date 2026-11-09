/* BMO Music Analysis 1.0.0. Local whole-mix features, not vocal/lyric analysis.
   Independent implementation; no runtime dependencies or network. */
(()=>{'use strict';
const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));
const quantile=(a,p)=>{const s=Array.from(a).filter(Number.isFinite).sort((a,b)=>a-b);return s[Math.min(s.length-1,Math.floor(s.length*p))]||0;};
function mean(a,start,end){let s=0;for(let i=start;i<end;i++)s+=a[i]||0;return s/Math.max(1,end-start);}
function estimate(onsets,start,end,dt=.02){
 const n=end-start;if(n<200)return{bpm:null,confidence:0};
 const vals=Array.from(onsets.slice(start,end)),p90=quantile(vals,.90),p98=quantile(vals,.98),threshold=Math.max(.025,p90*.6,p98*.28);
 const peaks=[];for(let i=start+1;i<end-1;i++)if(onsets[i]>=threshold&&onsets[i]>=onsets[i-1]&&onsets[i]>onsets[i+1]){if(!peaks.length||i-peaks.at(-1)>5)peaks.push(i);else if(onsets[i]>onsets[peaks.at(-1)])peaks[peaks.length-1]=i;}
 if(peaks.length<5)return{bpm:null,confidence:0};
 const ac=[];for(let lag=17;lag<=55;lag++){let ab=0,aa=0,bb=0;for(let i=start+lag;i<end;i++){const a=onsets[i],b=onsets[i-lag];ab+=a*b;aa+=a*a;bb+=b*b;}ac[lag]=ab/Math.max(1e-9,Math.sqrt(aa*bb));}
 let best=null;
 for(let lag=17;lag<=55;lag++){
  // Tolerate one 20ms bin of onset jitter. Compare integer multiples too.
  const corr=.6*ac[lag]+.2*(ac[lag-1]||ac[lag])+.2*(ac[lag+1]||ac[lag]);
  let interval=0,count=0;for(let i=1;i<peaks.length;i++){const gap=peaks[i]-peaks[i-1];if(gap>100)continue;const d=Math.abs(gap-lag);interval+=Math.exp(-d*d/8);count++;}interval/=Math.max(1,count);
  const score=.65*corr+.35*interval;
  if(!best||score>best.score)best={lag,score,corr,interval};
 }
 const confidence=clamp((best.corr*.65+best.interval*.35-.10)/.5);
 // A periodic estimate is deliberately withheld for weak/nonperiodic material.
 if(confidence<.28||best.corr<.12||(best.corr-quantile(ac.filter(Number.isFinite),.5)<.035&&best.interval<.3))return{bpm:null,confidence};
 const lag=best.lag,left=ac[lag-1]??ac[lag],right=ac[lag+1]??ac[lag],center=ac[lag];
 const delta=clamp(.5*(left-right)/(left-2*center+right||1),-.5,.5);
 return{bpm:clamp(60/((lag+delta)*dt),55,180),confidence};
}
function fromEnvelopes(rms,low,high,dt=.02){
 const n=rms.length,p95=quantile(rms.filter(x=>x>.00005),.95)||.1;
 const floor=Math.max(.0001,p95*.025),levels=Float32Array.from(rms,x=>clamp((x-floor)/Math.max(.0001,p95-floor)));
 const onsetRaw=new Float32Array(n);for(let i=1;i<n;i++){
  // Positive log-energy changes in full, low, and high bands.
  const f=(a)=>Math.max(0,Math.log1p((a[i]||0)*4/p95)-Math.log1p((a[i-1]||0)*4/p95));
  onsetRaw[i]=.25*f(rms)+.45*f(low)+.30*f(high);
 }
 const op=Math.max(.03,quantile(onsetRaw,.96)),onsets=Float32Array.from(onsetRaw,x=>clamp((x-.02)/op));
 const peakFlags=Float32Array.from(onsets,(v,i)=>v>.45&&v>(onsets[i-1]||0)&&v>=(onsets[i+1]||0)?1:0);
 const level=Float32Array.from(levels,(v,i)=>.65*v+.25*(levels[i-1]??v)+.1*(levels[i-2]??v));
 const global=estimate(onsets,0,n,dt),windows=[];
 for(let t=0;t<n*dt;t+=4){const a=Math.max(0,Math.floor((t-6)/dt)),b=Math.min(n,Math.ceil((t+6)/dt));windows.push({time:t,...estimate(onsets,a,b,dt)});}
 const energy=new Float32Array(n),tempo=new Float32Array(n),confidence=new Float32Array(n),beats=new Float32Array(n);
 let smoothEnergy=.25,smoothTempo=global.bpm||84,beat=0;
 for(let i=0;i<n;i++){
  const a=Math.max(0,i-50),b=Math.min(n,i+50),activity=mean(onsets,a,b),loud=mean(level,a,b);
  // Calm sustained sound can be loud: transient activity contributes separately.
  const density=mean(peakFlags,a,b)/dt;
  const target=clamp(.12+.22*Math.sqrt(activity)+.6*clamp(density/3.2)+.15*(loud-.4));smoothEnergy+=(target-smoothEnergy)*.018;energy[i]=smoothEnergy;
  const w=windows[Math.min(windows.length-1,Math.floor(i*dt/4))];
  const desired=w.bpm??(global.confidence>.40?global.bpm:null);
  const conf=w.bpm?w.confidence:global.confidence>.40?global.confidence*.65:0;
  smoothTempo+=((desired||84)-smoothTempo)*.012;tempo[i]=smoothTempo;confidence[i]=conf;beat+=smoothTempo/60*dt;beats[i]=beat;
 }
 return{schema:'bmo.music-profile.v1',step:dt,duration:n*dt,level,onset:onsets,energy,tempo,confidence,beats,summary:{bpm:global.bpm?Math.round(global.bpm):null,confidence:global.confidence,energy:mean(energy,0,n),rawOnsetP96:op,method:'whole-mix three-band energy-onset autocorrelation; no vocals or lyrics'},windows};
}
function analysePCM(channels,sampleRate){
 if(!Array.isArray(channels)||!channels.length||!Number.isFinite(sampleRate)||sampleRate<=0)throw Error('Invalid PCM');
 const length=channels[0].length;if(channels.some(c=>c.length!==length))throw Error('Channel lengths differ');
 const step=Math.round(sampleRate*.02),n=Math.ceil(length/step),rms=new Float32Array(n),low=new Float32Array(n),high=new Float32Array(n),stride=4,alpha=1-Math.exp(-2*Math.PI*220/(sampleRate/stride));
 const lp=channels.map(()=>0);
 for(let k=0;k<n;k++){let a=0,b=0,c=0,count=0;for(let ch=0;ch<channels.length;ch++){const data=channels[ch];for(let i=k*step;i<Math.min(length,(k+1)*step);i+=stride){const x=data[i];lp[ch]+=alpha*(x-lp[ch]);a+=x*x;b+=lp[ch]*lp[ch];c+=(x-lp[ch])**2;count++;}}rms[k]=Math.sqrt(a/Math.max(1,count));low[k]=Math.sqrt(b/Math.max(1,count));high[k]=Math.sqrt(c/Math.max(1,count));}
 return fromEnvelopes(rms,low,high);
}
function at(profile,time){if(!profile||!profile.level.length)return null;const i=clamp(Math.floor(time/profile.step),0,profile.level.length-1);return{level:profile.level[i],onset:profile.onset[i],energy:profile.energy[i],bpm:profile.tempo[i],confidence:profile.confidence[i],beat:profile.beats[i]};}
const api={version:'1.0.0',analysePCM,fromEnvelopes,estimate,at};if(typeof window!=='undefined')window.BMOMusicAnalysis=api;else if(typeof self!=='undefined')self.BMOMusicAnalysis=api;if(typeof module!=='undefined')module.exports=api;
})();
