/* Product wrapper only. The pinned R10 and Python audio owner stay unchanged. */
(()=>{'use strict';
const $=id=>document.getElementById(id),frame=$('face'),token=document.querySelector('meta[name="bmo-command-token"]').content;
let state=null,closed=false,timer=null,controller=null,epoch=0,handshakeFrame=null,lastSuccess=performance.now(),currentMode='conversation',lastGaze=null,presentedRun=null;
let enterDown=false,pttEnabled=false,pttPending=false,pttRequest=null,pttEpoch=0;
let featureState=null,musicCatalogKey=null,gameCatalogKey=null,lastFeatureMode=null;
const inputEvidence={enter_presses:0,keyups:0,repeats_ignored:0,blocked_mode:0,request_sent:0,request_accepted:0,request_rejected:0,cancelled_blur:0,physical_identity:'UNVERIFIED'};
function bump(name){inputEvidence[name]=Math.min(999999,inputEvidence[name]+1);}
const messages={credentials_missing:'최초 키 설정이 필요해요. 실행 가이드를 확인해 주세요.',greeting_missing:'시작 인사 캐시가 아직 준비되지 않았어요.',greeting_unapproved:'시작 인사의 청취 승인이 필요해요.',greeting_corrupt:'시작 인사 캐시를 확인해 주세요.',face_ready_timeout:'얼굴 준비 시간이 초과됐어요.',host_startup_timeout:'대화 엔진 준비 시간이 초과됐어요.',greeting_timeout:'시작 인사 재생 정리를 기다리고 있어요.',browser_disconnected:'연결을 다시 확인하고 있어요.'};
async function post(path,payload){const response=await fetch(path,{method:'POST',keepalive:payload.action==='cancel',headers:{'Content-Type':'application/json','X-BMO-Token':token},body:JSON.stringify({version:1,nonce:crypto.randomUUID(),payload})});if(!response.ok)throw Error('요청을 완료하지 못했어요.');return response.json();}
const desktop=(action,value=null)=>post('/api/desktop',{action,value}),control=(action,value=null)=>post('/api/control',{action,value}),intent=(name,target=null)=>post('/api/intent',{name,target});
function error(message){$('notice').hidden=false;$('notice-message').textContent=message;}
function menu(){cancelPTT();window.BMOPlayableGames?.suspend('menu');if(!$('menu').open)$('menu').showModal();}
function refreshAdapter(){if(window.BMOIntegration?.closed){window.BMOIntegration=new window.BMOFaceAdapter(frame);}handshakeFrame=null;}
frame.addEventListener('load',refreshAdapter);
async function ready(){if(!state||handshakeFrame===frame.contentDocument)return;
 let face;try{face=frame.contentWindow.BMOFace;}catch(_){return;}
 if(!face||!['getState','setEmotion','setActivity','setSpeaking','setMouth','stop','look','lookEyes','blink'].every(k=>typeof face[k]==='function'))return;
 const version=face.getState().version;if(typeof version!=='string')return;
 const response=await fetch('/api/screen',{cache:'no-store'});if(!response.ok)throw Error();const screen=await response.json();
 await desktop('face_ready',{run_id:state.run_id,screen_sha256:screen.sha256,frame_version:version});handshakeFrame=frame.contentDocument;}
function render(next){state=next;window.BMODesktop.state=next;
 const loading=next.phase==='loading'&&next.progress_percent!==100;$('splash').hidden=!loading;
 // Acknowledge only after the verified face has had two render opportunities.
 if(next.progress_percent===100&&next.face_ready&&!document.hidden&&presentedRun!==next.run_id){
  const run=next.run_id;presentedRun=run;requestAnimationFrame(()=>requestAnimationFrame(()=>{
   if(!closed&&!document.hidden&&state?.run_id===run&&$('splash').hidden)
    desktop('face_presented',{run_id:run}).catch(()=>{presentedRun=null;});
   else presentedRun=null;
  }));}
 // Values are achieved backend checkpoints, not an elapsed-time progress guess.
 const progress=Number.isInteger(next.progress_percent)?Math.max(0,Math.min(100,next.progress_percent)):0;
 $('progress-percent').textContent=progress+'%';
 $('progress-track').setAttribute('aria-valuenow',String(progress));
 $('progress-fill').style.width=progress+'%';
 const stages={lock_acquired:'BMO 실행을 시작했어요.',server_ready:'얼굴 서버 준비 완료',browser_registered:'화면 창을 준비하고 있어요.',browser_spawned:'화면 창 실행 완료',engine_ready:'대화 엔진 준비 완료',face_handshake_verified:'BMO 얼굴 연결 확인',local_app_ready:'시작 준비 완료'};
 $('loading-stage').textContent=stages[next.progress_stage]||'실행 상태를 확인하고 있어요.';
 const problem=next.phase==='error'||next.phase==='ready'&&next.code!=='greeting_completed';
 $('notice').hidden=!problem;if(problem)$('notice-message').textContent=messages[next.code]||'준비 상태를 확인해 주세요. 메뉴에서 재시도하거나 종료할 수 있어요.';
 $('diagnostic').textContent=next.code;$('talk').disabled=next.phase!=='ready';$('repeat').disabled=next.phase!=='ready';
 $('greeting-retry').disabled=next.phase!=='ready'||next.code==='greeting_completed';}
async function modes(){if(state?.phase!=='ready')return;const r=await fetch('/api/features',{cache:'no-store'});if(!r.ok)return;const next=await r.json();currentMode=next.mode;
 window.BMODesktop.features=next;
 featureState=next;renderFeatures(next);
 syncGamepad(next);
 pttEnabled=next.ptt?.enabled===true&&next.capabilities?.conversation_ptt_microphone!=='denied';
 // Bind completion to THIS admitted command, rather than a stale snapshot's
 // idle phase or a previous capture generation. Queued input stays cancellable.
 const receipt=pttRequest&&(next.requests||[]).find(item=>item.request_id===pttRequest);
 if(receipt&&!['accepted','running'].includes(receipt.status)){pttPending=false;pttRequest=null;}
 const capture=next.ptt?.state;
 const diagnosis=next.turn_diagnostics,failure=diagnosis?.failure;
 const inputFailed=capture==='failed'||next.ptt?.cleanup_fault===true||failure;
 $('ptt-state').hidden=!inputFailed&&!['calibrating','listening','speech','transcribing'].includes(capture);
 const failures={no_speech:'말소리를 감지하지 못했어요. 버튼을 누르고 다시 말해 주세요.',stt_empty:'말을 알아듣지 못했어요. 다시 말해 주세요.'};
 $('ptt-state').textContent=inputFailed?(next.ptt?.cleanup_fault?'녹음 정리를 확인해 주세요. 앱을 종료한 뒤 검수해 주세요.':failures[failure?.code]||(capture==='failed'&&failure?.stage!=='stt'?'녹음을 완료하지 못했어요. 마이크 설정과 연결을 확인해 주세요.':'대화를 완료하지 못했어요. 준비 상태에서 실패 단계를 확인해 주세요.')):
  ({calibrating:'주변 소리를 확인해요',listening:'듣고 있어요',speech:'말소리를 감지했어요',transcribing:'말을 확인하고 있어요'})[capture]||'';
 if(next.mode==='game_menu'&&!document.querySelector('dialog[open]:not(#game-menu)')){if(!$('game-menu').open)$('game-menu').showModal();
  const catalog=JSON.stringify(next.games.catalog);if(catalog!==gameCatalogKey){gameCatalogKey=catalog;$('game-list').replaceChildren();if(!next.games.catalog.length)$('game-list').textContent='게임 자료가 아직 준비되지 않았어요.';for(const item of next.games.catalog){const b=document.createElement('button');b.textContent=item.title;b.dataset.gameId=item.id;b.disabled=item.readiness!=='READY';b.onclick=()=>intent('launch_game',item.id).catch(()=>error('게임을 시작하지 못했어요.'));$('game-list').append(b);}}
  for(const b of $('game-list').querySelectorAll('button'))b.setAttribute('aria-current',String(b.dataset.gameId===next.games.selected_id));
  $('game-pack-status').textContent=next.games.catalog.some(item=>item.capabilities?.includes('packaged_web'))?'자체 제작 오프라인 게임 · 방향키 / Z(A) / B · Enter 일시 정지 · Esc 게임 목록':next.games.catalog.length?'합성 테스트 게임 목록이에요. 실제 게임팩은 별도로 선택해 주세요.':'실행 옵션 --playable-games로 자체 제작 게임팩을 선택할 수 있어요.';}
 else if($('game-menu').open)$('game-menu').close();
 const webGame=next.mode==='game'&&next.games.web?.active===true;
 $('foreground').hidden=webGame||!['game','photo'].includes(next.mode);$('foreground-status').textContent=next.mode==='game'?'게임 실행 중':next.photo_countdown!==null?'사진을 준비하고 있어요.':'사진 처리 결과를 확인해 주세요.';
 window.BMOPlayableGames?.sync(next);
 const face=frame.contentWindow?.BMOFace,gaze=next.gaze,phase=window.BMOIntegration?.state?.phase;
 if(face&&gaze){const native=[phase,face.getState().activity].some(p=>['listening','thinking'].includes(p));if(native&&gaze.owner!=='manual')lastGaze=null;
 else if(gaze.apply){const id=JSON.stringify([gaze.owner,gaze.x,gaze.y,next.mode_generation]);if(id!==lastGaze){face.look(gaze.x,gaze.y);face.lookEyes(gaze.x*.15,gaze.y*.15);if(gaze.owner==='reaction')face.blink();lastGaze=id;}}}
}
function renderFeatures(next){
 if(lastFeatureMode==='music'&&next.mode==='conversation'&&$('music-dialog').open)$('music-dialog').close();
 lastFeatureMode=next.mode;
 const video=next.camera_video,recording=video?.recording===true,unknown=video?.code==='video_close_failed';
 $('video-indicator').hidden=!recording&&!unknown;
 const liveVideo=next.capabilities?.camera_video==='explicit_supervised';
 $('video-explanation').textContent=liveVideo?'음성과 별개이며 소리를 저장하지 않아요. 사용자 동의 후에만 카메라를 열어요.':'음성 녹음과 별개이며 소리를 저장하지 않아요. 현재 실물 카메라는 꺼져 있어요.';
 $('video-indicator').textContent=unknown?'영상 종료를 확인하지 못했어요. 검수가 필요해요.':liveVideo?'● 카메라 녹화 중 · 소리 없음':'● 모의 영상 녹화 중 · 소리 없음';
 const liveBackendAvailable=next.system_diagnostics?.camera?.available===true;
 const videoReady=(next.simulation===true||liveVideo&&liveBackendAvailable)&&video?.readiness==='READY';
 $('video-readiness').textContent=unknown?'영상 정리가 확인되지 않았어요. 새 녹화를 시작하지 마세요.':liveVideo&&!liveBackendAvailable?'현재 Python 환경에 카메라 라이브러리가 없어요. 실행 가이드를 확인해 주세요.':liveVideo?'사용자가 보는 동안 선택한 위치에 실제 카메라 영상을 짧게 저장해요.':!next.simulation?'실물 영상 기능은 사용자 검수 전 사용할 수 없어요.':video?.readiness==='ASSET_PENDING'?'실행 전에 저장 위치를 선택해 주세요.':'선택한 저장 위치에 합성 테스트 영상만 저장해요.';
 $('video-consent').parentElement.lastChild.textContent=liveVideo?'사용자가 보는 동안 실제 카메라 영상을 저장하는 데 동의해요.':'선택한 저장 위치에 모의 영상을 만드는 데 동의해요.';
 $('video-start').textContent=liveVideo?'감독하에 영상 시작':'모의 영상 시작';
 $('video-start').disabled=!videoReady||recording||!$('video-consent').checked||next.conversation_busy||next.mode!=='conversation';
 $('video-stop').disabled=!recording;
 const result=video?.result,saved=result?.completed===true;
 $('video-result').textContent=saved?(liveVideo?'카메라 영상 저장과 검증을 완료했어요.':'모의 영상 저장과 검증을 완료했어요.'):result&&['failed','unknown','rejected'].includes(result.status)?'영상 결과를 확인해 주세요. 자동 재시도하지 않아요.':'';
 $('video-download').hidden=!saved;if(saved)$('video-download').href='/api/video/'+encodeURIComponent(result.video_id);
 $('video-download').textContent=liveVideo?'검증된 카메라 영상 파일':'검증된 모의 영상 파일';
 const photoReady=next.mode==='photo'&&next.photo?.completed===true&&typeof next.photo?.photo_id==='string';
 $('photo-result-image').hidden=!photoReady;$('photo-download').hidden=!photoReady;
 if(photoReady){const url='/api/photo/'+encodeURIComponent(next.photo.photo_id);if($('photo-result-image').getAttribute('src')!==url)$('photo-result-image').src=url;$('photo-download').href=url;}
 const liveCamera=next.capabilities?.camera==='explicit_supervised',cameraAvailable=next.system_diagnostics?.camera?.available===true;
 const cameraReady=next.capabilities?.camera==='mock'||liveCamera&&cameraAvailable;
 $('camera-readiness').textContent=liveCamera?(cameraAvailable?'감독 모드예요. 촬영 버튼을 누를 때 카메라를 열어요.':'현재 Python 환경에 카메라 라이브러리가 없어요. 실행 가이드를 확인해 주세요.'):cameraReady?'합성 사진·미리보기·위치 추적을 검사해요.':'실물 카메라는 사용자 검수 전 사용할 수 없어요.';
 for(const id of ['photo-take','camera-preview','camera-track'])$(id).disabled=!cameraReady||next.conversation_busy;
 if(liveCamera&&!next.system_diagnostics?.camera?.tracking_available)$('camera-track').disabled=true;
 const preview=next.camera?.preview_available&&$('camera-dialog').open;
 $('camera-image').hidden=!preview;if(preview)$('camera-image').src='/api/preview?sequence='+next.sequence;
 const liveMusic=next.capabilities?.music==='explicit_supervised';
 $('music-readiness').textContent=next.media?.catalog?.length?(liveMusic?'검토한 로컬 WAV를 선택한 스피커에서 재생해요. 실제 음량은 사용자 검수가 필요해요.':next.simulation?'현재 목록은 검증된 모의 재생 자료예요. 실제 음원·출력은 별도 검수가 필요해요.':'로컬 음원은 준비됐어요. 실제 재생에는 감독 실행과 음량 검토가 필요해요.'):'사용자 음원과 출처·라이선스 검토가 필요해요.';
 const catalogKey=JSON.stringify(next.media?.catalog||[]);
 if(catalogKey!==musicCatalogKey){musicCatalogKey=catalogKey;$('music-list').replaceChildren();for(const item of next.media?.catalog||[]){const b=document.createElement('button');b.textContent=item.title;b.disabled=item.readiness!=='READY'||!['mock','explicit_supervised'].includes(next.capabilities?.music);b.onclick=()=>intent('play_song',item.id).catch(()=>error('음악을 시작하지 못했어요.'));$('music-list').append(b);}}
 for(const [id,capability] of [['music-pause','pause'],['music-resume','resume']])$(id).disabled=next.mode!=='music'||!next.media?.capabilities?.includes(capability);
 for(const id of ['music-next','music-stop'])$(id).disabled=next.mode!=='music';
 const telemetry=next.system_diagnostics?.telemetry;
 $('system-readiness').textContent=telemetry?.power?.status==='OBSERVED'?('전원 현재 이상: '+(telemetry.power.current_fault?'있음':'없음')+' · 과거 기록: '+(telemetry.power.historical_fault?'있음':'없음')):'온도·전원 실측은 아직 실행하지 않았어요. 자동 설정 변경은 하지 않아요.';
 $('turn-diagnostics').textContent=JSON.stringify(next.turn_diagnostics||{enabled:false},null,2);
 $('input-evidence').textContent=JSON.stringify(inputEvidence,null,2);
 $('gamepad-readiness').textContent=next.usb_gamepad?.enabled?'USB 패드 입력 사용: 표준 매핑의 게임·게임 목록에만 전달해요. 실제 패드 검수는 별도예요.':'USB 패드는 기본으로 꺼져 있어요. GP8 대화 입력은 그대로 사용할 수 있어요.';
}
async function poll(){if(closed||document.hidden)return;const current=epoch;controller=new AbortController();const abort=setTimeout(()=>controller?.abort(),1200);try{
 const r=await fetch('/api/desktop',{cache:'no-store',signal:controller.signal});if(!r.ok)throw Error();const next=await r.json();if(current!==epoch||closed)return;
 lastSuccess=performance.now();render(next);await ready();await modes();
}catch(_){if(performance.now()-lastSuccess>2500){$('splash').hidden=true;error('BMO 연결이 끊겼어요. 창을 닫고 수동으로 다시 실행해 주세요.');}}
finally{clearTimeout(abort);controller=null;if(!closed&&current===epoch&&!document.hidden)timer=setTimeout(poll,100);}}
$('notice-menu').onclick=menu;$('resume').onclick=()=>$('menu').close();$('exit').onclick=()=>desktop('exit').catch(()=>{error('BMO가 종료된 상태예요. 창을 닫아 주세요.');window.close();});
function ptt(){if(!pttEnabled){error('버튼 녹음은 사용자 검수용 마이크 opt-in이 필요해요.');return;}
 if(pttPending){control('ptt').catch(()=>error('버튼 입력을 확인해 주세요.'));return;}
 pttPending=true;pttRequest=null;const current=++pttEpoch;
 bump('request_sent');control('ptt').then(result=>{bump('request_accepted');if(current===pttEpoch&&pttPending)pttRequest=result.request_id;}).catch(()=>{bump('request_rejected');if(current===pttEpoch){pttPending=false;error('버튼 녹음을 시작하지 못했어요.');}});}
function cancelPTT(){enterDown=false;if(pttPending){pttEpoch++;pttPending=false;pttRequest=null;control('cancel').catch(()=>{});}}
$('talk').onclick=()=>{if(pttEnabled)ptt();else control('question','A manually requested question.').catch(()=>error('대화를 시작하지 못했어요.'));$('menu').close();};
$('repeat').onclick=()=>control('repeat').catch(()=>error('다시 들을 답변이 없어요.'));$('stop').onclick=()=>control('stop').catch(()=>error('정지 상태를 확인해 주세요.'));
$('games').onclick=()=>{intent('open_game_menu').catch(()=>error('게임 목록을 열지 못했어요.'));$('menu').close();};$('greeting-retry').onclick=()=>desktop('retry_greeting').catch(()=>error('인사 캐시를 확인해 주세요.'));
function openPanel(id){cancelPTT();$('menu').close();$(id).showModal();if(featureState)renderFeatures(featureState);}
for(const [id,panel] of [['camera-menu','camera-dialog'],['video-menu','video-dialog'],['music-menu','music-dialog'],['system-menu','system-dialog']])$(id).onclick=()=>openPanel(panel);
for(const button of document.querySelectorAll('.dialog-close'))button.onclick=()=>button.closest('dialog').close();
$('video-consent').onchange=()=>{if(featureState)renderFeatures(featureState);};
$('video-start').onclick=()=>{control('video_start',{consent:$('video-consent').checked}).catch(()=>error('영상 준비 상태를 확인해 주세요.'));$('video-consent').checked=false;};
$('video-stop').onclick=()=>control('video_stop').catch(()=>error('영상 저장 상태를 확인해 주세요.'));
$('photo-take').onclick=()=>{intent('take_photo').catch(()=>error('사진 상태를 확인해 주세요.'));$('camera-dialog').close();};
$('camera-preview').onclick=()=>control('preview',true).catch(()=>error('미리보기를 준비하지 못했어요.'));
$('camera-track').onclick=()=>intent('start_face_tracking').catch(()=>error('시선 추적을 준비하지 못했어요.'));
$('camera-off').onclick=()=>intent('stop_face_tracking').catch(()=>error('카메라 종료를 확인해 주세요.'));
for(const [id,action] of [['music-pause','pause'],['music-resume','resume'],['music-next','next']])$(id).onclick=()=>control(action).catch(()=>error('음악 상태를 확인해 주세요.'));
$('music-stop').onclick=()=>intent('stop_media').catch(()=>error('음악 종료를 확인해 주세요.'));
for(const id of ['game-back','foreground-back'])$(id).onclick=()=>intent('return_to_face').catch(()=>error('복귀 상태를 확인해 주세요.'));
const keys={ArrowUp:'UP',ArrowDown:'DOWN',ArrowLeft:'LEFT',ArrowRight:'RIGHT',a:'A',z:'A',b:'B',Enter:'START',Shift:'SELECT',Escape:'ESC'};
function key(event,pressed){if(event.isComposing)return;if(event.key==='Enter'){if(!pressed){enterDown=false;bump('keyups');}else if(event.repeat)bump('repeats_ignored');else bump('enter_presses');}if(event.repeat)return;
 if(pressed&&event.ctrlKey&&event.altKey&&event.code==='KeyD'&&state?.developer_enabled&&state.phase==='ready'){event.preventDefault();location.assign('/developer');return;}
 if(event.key==='Escape'){
  event.preventDefault();event.stopPropagation();
  const covering=document.querySelector('dialog[open]:not(#game-menu)');
  if(covering){if(pressed){cancelPTT();covering.close();}return;}
  if(['game','game_menu'].includes(currentMode)){control('input',{key:'ESC',pressed}).catch(()=>error('게임 종료 상태를 확인해 주세요.'));return;}
  if(pressed){cancelPTT();for(const d of document.querySelectorAll('dialog[open]'))d.close();menu();}return;}
 if(currentMode==='game'&&featureState?.games?.web?.active===true){
  const mapped=event.code==='Space'?'B':event.key==='p'?'START':keys[event.key];
  if(mapped){event.preventDefault();event.stopPropagation();window.BMOPlayableGames?.input(mapped,pressed);return;}}
 if(['BUTTON','INPUT','SELECT','TEXTAREA'].includes(event.target.tagName)&&currentMode!=='game_menu')return;
 if(event.key==='Enter'&&currentMode==='conversation'&&!document.querySelector('dialog[open]')&&state?.phase==='ready'){
  event.preventDefault();event.stopPropagation();if(!pressed){enterDown=false;return;}if(enterDown)return;enterDown=true;ptt();return;}
 if(['game','game_menu'].includes(currentMode)&&keys[event.key]){if(event.key==='Enter'&&pressed)bump('blocked_mode');event.preventDefault();event.stopPropagation();control('input',{key:keys[event.key],pressed}).catch(()=>error('게임 입력 상태를 확인해 주세요.'));return;}}
window.addEventListener('keydown',event=>key(event,true));window.addEventListener('keyup',event=>key(event,false));
function blurPTT(event){setTimeout(()=>{if(event.isTrusted&&document.hasFocus()&&document.activeElement===frame)return;bump('cancelled_blur');cancelPTT();},0);}
window.addEventListener('blur',blurPTT);
let touchStart=null,inputDocument=null;function bindFrameInputs(){try{const doc=frame.contentDocument;if(!doc||doc===inputDocument)return;inputDocument=doc;doc.addEventListener('keydown',event=>key(event,true),true);doc.addEventListener('keyup',event=>key(event,false),true);doc.defaultView.addEventListener('blur',cancelPTT);
 doc.addEventListener('pointerdown',event=>{touchStart={x:event.clientX,y:event.clientY};if(event.clientX<60&&event.clientY<60)touchStart.timer=setTimeout(menu,1200);});for(const name of ['pointerup','pointercancel'])doc.addEventListener(name,()=>{clearTimeout(touchStart?.timer);touchStart=null;});doc.addEventListener('pointermove',event=>{if(touchStart&&Math.hypot(event.clientX-touchStart.x,event.clientY-touchStart.y)>12){clearTimeout(touchStart.timer);touchStart=null;}});}catch(_){}}
frame.addEventListener('load',()=>{cancelPTT();bindFrameInputs();});bindFrameInputs();
// The standard browser mapping is opt-in and game-only. Never map a pad button
// to GP8/TALK, GP7, a servo command, camera capture or a provider API.
let padTimer=null,padIndex=null,padGeneration=null,padHeld=new Set(),padSuppressed=new Set(),padSequence=0,padHeartbeat=0,padChain=Promise.resolve(),padPending=0;
const padEvidence={polls:0,edges:0,disconnects:0,mapping:'standard_game_only',physical_verified:false};
function sendPad(key,pressed,generation){
 if(generation===null||closed||padPending>=32)return;
 const value={key,pressed,sequence:++padSequence,generation},created=performance.now();padPending++;
 padChain=padChain.catch(()=>{}).then(()=>{if(closed||performance.now()-created>450)return;return control('gamepad',value);}).catch(()=>{}).finally(()=>{padPending--;});
}
function releasePad(disconnected=true){
 clearTimeout(padTimer);padTimer=null;
 for(const key of padHeld)sendPad(key,false,padGeneration);
 if(padIndex!==null){if(disconnected)padEvidence.disconnects++;sendPad(disconnected?'PAD_LOST':'RELEASE',true,padGeneration);}
 padIndex=null;padGeneration=null;padHeld=new Set();padSuppressed=new Set();padHeartbeat=0;
}
function normalizedPad(pad){
 const keys=new Set(),mapping=[[0,'A'],[1,'B'],[8,'SELECT'],[9,'START'],[12,'UP'],[13,'DOWN'],[14,'LEFT'],[15,'RIGHT']];
 for(const [index,key] of mapping)if(pad.buttons?.[index]?.pressed===true)keys.add(key);
 const x=pad.axes?.[0],y=pad.axes?.[1];if(Number.isFinite(x)){if(x<-.6)keys.add('LEFT');if(x>.6)keys.add('RIGHT');}if(Number.isFinite(y)){if(y<-.6)keys.add('UP');if(y>.6)keys.add('DOWN');}
 return keys;
}
function gamepadAllowed(next){return next?.usb_gamepad?.enabled===true&&state?.phase==='ready'&&!document.hidden&&document.hasFocus()&&['game','game_menu'].includes(next.mode)&&!document.querySelector('dialog[open]:not(#game-menu)');}
function pollPad(){
 padTimer=null;if(!gamepadAllowed(featureState)){releasePad();return;}
 let pad;try{padEvidence.polls++;pad=Array.from(navigator.getGamepads?.()||[]).find(p=>p&&p.connected&&p.mapping==='standard');}catch(_){releasePad();return;}
 if(!pad){releasePad();return;}
 const keys=normalizedPad(pad),generation=featureState.mode_generation;
 if(pad.index!==padIndex||padGeneration!==generation){releasePad(false);padIndex=pad.index;padGeneration=generation;padSuppressed=new Set(keys);}
 for(const key of [...padSuppressed])if(!keys.has(key))padSuppressed.delete(key);
 for(const key of [...padHeld])if(!keys.has(key)){sendPad(key,false,generation);padHeld.delete(key);padEvidence.edges++;}
 for(const key of keys)if(!padHeld.has(key)&&!padSuppressed.has(key)){sendPad(key,true,generation);padHeld.add(key);padEvidence.edges++;}
 if(performance.now()-padHeartbeat>=250){sendPad('HEARTBEAT',true,generation);padHeartbeat=performance.now();}
 padTimer=setTimeout(pollPad,50);
}
function syncGamepad(next){padSequence=Math.max(padSequence,next?.usb_gamepad?.sequence_floor||0);if(!gamepadAllowed(next)){releasePad(false);return;}if(padTimer===null)padTimer=setTimeout(pollPad,0);}
window.addEventListener('blur',event=>{setTimeout(()=>{if(event.isTrusted&&document.hasFocus()&&document.activeElement===frame)return;releasePad();},0);});
window.addEventListener('gamepaddisconnected',()=>releasePad());
window.addEventListener('pagehide',()=>{window.BMOPlayableGames?.close();releasePad();cancelPTT();closed=true;epoch++;clearTimeout(timer);controller?.abort();window.BMOIntegration?.close();});
document.addEventListener('visibilitychange',()=>{epoch++;clearTimeout(timer);controller?.abort();if(document.hidden){releasePad();cancelPTT();}else{refreshAdapter();poll();}});
window.BMODesktop={state:null,desktop,control,intent,menu,inputEvidence,gamepadEvidence:padEvidence};window.BMOPlayableGames?.configure({control,intent});poll();
})();
