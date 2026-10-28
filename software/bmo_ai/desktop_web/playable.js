/* Original bundled games use the existing Desktop owner, never a new window. */
(()=>{'use strict';
const $=id=>document.getElementById(id),surface=$('game-surface'),canvas=$('game-canvas');
let api=null,engine=null,launch=null,generation=null,desired=null,loading=null,epoch=0;
let sequence=0,keySequence=0,keyChain=Promise.resolve(),keyPending=0,raf=null,lastFrame=0,frames=0;
let ready=false,focusPaused=false,lastSnapshot=0,lastHeartbeat=0,failed=false;
const held=new Set();
const state=()=>engine?{...engine.snapshot(),launch_id:launch,input_sequence:sequence,
  paused_by_focus:focusPaused,animation_running:raf!==null,frames}:null;
function cancelFrame(){if(raf!==null){cancelAnimationFrame(raf);raf=null;}lastFrame=0;}
function draw(){if(!engine)return;engine.draw(canvas.getContext('2d'),800,480);
 const s=engine.snapshot();$('game-state').textContent=(focusPaused?'입력 해제 · 일시 정지':
  ({playing:'플레이 중',paused:'일시 정지',gameover:'게임 종료',won:'완료'})[s.status]||'준비 중');
 $('game-pause').textContent=s.status==='paused'?'계속하기':'일시 정지';}
function frame(at){raf=null;if(!engine)return;
 if(!ready||focusPaused||document.hidden||performance.now()-lastSnapshot>1400){suspend('connection');return;}
 const dt=lastFrame?Math.max(0,Math.min(.05,(at-lastFrame)/1000)):0;lastFrame=at;
 engine.update(dt);frames++;draw();
 if(engine.snapshot().status==='playing')raf=requestAnimationFrame(frame);}
function animate(){if(engine&&ready&&!focusPaused&&!document.hidden&&raf===null&&engine.snapshot().status==='playing')raf=requestAnimationFrame(frame);}
function stream(key,pressed){
 if(!api||!launch||generation===null||keyPending>=32)return;
 const identity=launch,gen=generation,value={key,pressed,sequence:++keySequence,generation:gen},created=performance.now();keyPending++;
 keyChain=keyChain.catch(()=>{}).then(()=>{
  if(launch!==identity||generation!==gen||performance.now()-created>450)return;
  return api.control('game_key',value);
 }).catch(()=>{suspend('input');}).finally(()=>{keyPending--;});
}
function releaseKeys(){for(const key of held)stream(key,false);held.clear();if(launch)stream('RELEASE',true);}
function suspend(_reason='focus'){
 if(!engine)return;
 if(focusPaused&&engine.snapshot().status==='paused'){cancelFrame();return;}
 releaseKeys();engine.releaseInputs();engine.pause();focusPaused=true;cancelFrame();draw();
}
function close(){epoch++;cancelFrame();releaseKeys();if(engine){engine.close();engine=null;}
 launch=null;generation=null;desired=null;ready=false;focusPaused=false;sequence=0;failed=false;surface.hidden=true;
}
async function load(){if(window.BMOOfflineGames)return;
 if(!loading)loading=new Promise((resolve,reject)=>{
  const script=document.createElement('script');script.src='/games/engines.js';script.async=true;
  script.onload=()=>window.BMOOfflineGames?resolve():reject(Error('game_runtime_missing'));
  script.onerror=()=>reject(Error('game_runtime_missing'));document.head.append(script);
 }).catch(error=>{loading=null;throw error;});
 return loading;
}
function fail(){if(failed)return;failed=true;suspend('runtime');
 $('game-state').textContent='게임 연결을 확인해 주세요.';api?.intent('exit_game').catch(()=>{});}
async function mount(web,modeGeneration){
 const token=++epoch;desired=web.launch_id;
 try{await load();if(token!==epoch||desired!==web.launch_id)return;
  engine=window.BMOOfflineGames.create(web.game_id);launch=web.launch_id;generation=modeGeneration;
  // A new document has no saved engine state. Retained events from an earlier
  // document must never be replayed into its fresh board after a refresh.
  sequence=Number.isSafeInteger(web.input_sequence)?web.input_sequence:0;
  frames=0;ready=false;focusPaused=false;failed=false;lastSnapshot=performance.now();
  surface.hidden=false;draw();canvas.focus({preventScroll:true});
  await api.control('game_ready',{launch_id:launch,generation});
 }catch(_){if(token===epoch)fail();}
}
function sync(next){
 const web=next.games?.web,active=next.mode==='game'&&web?.active===true;
 keySequence=Math.max(keySequence,next.game_keyboard?.sequence_floor||web?.keyboard_sequence_floor||0);
 if(!active){if(engine||desired)close();return;}
 if(web.launch_id!==launch){if(web.launch_id!==desired){close();mount(web,next.mode_generation);}return;}
 if(next.mode_generation!==generation){close();return;}
 lastSnapshot=performance.now();ready=web.ready===true;
 const inputs=web.inputs;
 if(!Array.isArray(inputs)||inputs.length>256){fail();return;}
 for(const item of inputs){
  if(!Number.isSafeInteger(item.sequence)||typeof item.key!=='string'||typeof item.pressed!=='boolean'){fail();return;}
  if(item.sequence<=sequence)continue;
  if(item.sequence!==sequence+1){fail();return;}
  sequence=item.sequence;engine.input(item.key,item.pressed);
 }
 // A real edge after refocusing may resume a paused game; stale held controls
 // were released by both the browser and generation-aware server gate.
 if(engine.snapshot().status==='playing'&&!document.hidden&&document.hasFocus())focusPaused=false;
 if(document.hidden||!document.hasFocus()||document.querySelector('dialog[open]'))suspend('covered');
 if(ready&&performance.now()-lastHeartbeat>=250){stream('HEARTBEAT',true);lastHeartbeat=performance.now();}
 draw();animate();
}
function input(key,pressed){if(!engine||!ready)return false;
 if(pressed===held.has(key))return true;
 pressed?held.add(key):held.delete(key);stream(key,pressed);return true;
}
function restart(){if(!engine||!api)return;
 // A restart is a new admitted launch/generation. Resetting only the canvas
 // would let an older queued hard-drop edge land on the freshly reset board.
 const gameId=engine.snapshot().game_id;suspend('restart');
 api.intent('launch_game',gameId).catch(fail);
}
function togglePause(){if(!engine)return;releaseKeys();engine.releaseInputs();
 if(engine.snapshot().status==='paused'){engine.resume();focusPaused=false;}else engine.pause();
 cancelFrame();draw();animate();canvas.focus({preventScroll:true});}
$('game-pause').onclick=togglePause;$('game-restart').onclick=restart;
$('game-exit').onclick=()=>{suspend('exit');api?.intent('exit_game').catch(fail);};
window.addEventListener('blur',event=>{setTimeout(()=>{
 if(event.isTrusted&&document.hasFocus()&&document.activeElement?.tagName==='IFRAME')return;suspend('blur');},0);});
document.addEventListener('visibilitychange',()=>{if(document.hidden)suspend('hidden');});
window.addEventListener('pagehide',close);
window.BMOPlayableGames=Object.freeze({configure(value){api=value;},sync,input,suspend,close,snapshot:state});
})();
