/* Product wrapper only. The pinned R10 and Python audio owner stay unchanged. */
(()=>{'use strict';
const $=id=>document.getElementById(id),frame=$('face'),token=document.querySelector('meta[name="bmo-command-token"]').content;
let state=null,closed=false,timer=null,controller=null,epoch=0,handshakeFrame=null,lastSuccess=performance.now(),currentMode='conversation',lastGaze=null,presentedRun=null;
let enterDown=false,pttEnabled=false,pttPending=false,pttRequest=null,pttEpoch=0;
let featureState=null,musicCatalogKey=null,lastFeatureMode=null;
const messages={credentials_missing:'최초 키 설정이 필요해요. 실행 가이드를 확인해 주세요.',greeting_missing:'시작 인사 캐시가 아직 준비되지 않았어요.',greeting_unapproved:'시작 인사의 청취 승인이 필요해요.',greeting_corrupt:'시작 인사 캐시를 확인해 주세요.',face_ready_timeout:'얼굴 준비 시간이 초과됐어요.',host_startup_timeout:'대화 엔진 준비 시간이 초과됐어요.',greeting_timeout:'시작 인사 재생 정리를 기다리고 있어요.',browser_disconnected:'연결을 다시 확인하고 있어요.'};
async function post(path,payload){const response=await fetch(path,{method:'POST',keepalive:payload.action==='cancel',headers:{'Content-Type':'application/json','X-BMO-Token':token},body:JSON.stringify({version:1,nonce:crypto.randomUUID(),payload})});if(!response.ok)throw Error('요청을 완료하지 못했어요.');return response.json();}
const desktop=(action,value=null)=>post('/api/desktop',{action,value}),control=(action,value=null)=>post('/api/control',{action,value}),intent=(name,target=null)=>post('/api/intent',{name,target});
function error(message){$('notice').hidden=false;$('notice-message').textContent=message;}
function menu(){cancelPTT();if(!$('menu').open)$('menu').showModal();}
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
 pttEnabled=next.ptt?.enabled===true&&next.capabilities?.conversation_ptt_microphone!=='denied';
 // Bind completion to THIS admitted command, rather than a stale snapshot's
 // idle phase or a previous capture generation. Queued input stays cancellable.
 const receipt=pttRequest&&(next.requests||[]).find(item=>item.request_id===pttRequest);
 if(receipt&&!['accepted','running'].includes(receipt.status)){pttPending=false;pttRequest=null;}
 const capture=next.ptt?.state,reason=next.ptt?.reason_code;
 const inputFailed=capture==='failed'||next.ptt?.cleanup_fault===true;
 // Keep the last safe VAD outcome visible after returning to idle. The old
 // UI hid no_speech and made a completed-but-empty capture look like a hang.
 const pttReasons={
  no_speech:'말소리를 감지하지 못했어요. 초록 버튼 뒤 잠깐 기다렸다가 말해 주세요.',
  calibration_noisy:'팬·주변 소음이 커서 보정하지 못했어요. 마이크 감도를 확인해 주세요.',
  microphone_stream_closed:'마이크 입력이 중단됐어요. USB 연결을 확인해 주세요.',
  microphone_start_failed:'마이크를 열지 못했어요. USB 입력 장치를 확인해 주세요.',
  pcm_cleanup_failed:'녹음 정리 실패: 새 대화를 시작하지 말고 앱을 종료해 주세요.',
  transcription_failed:'음성을 받았지만 문장 변환에 실패했어요. 네트워크/STT를 확인해 주세요.',
  invalid_pcm_frame:'마이크 PCM 프레임이 올바르지 않아 녹음을 중단했어요.',
  user_cancelled:'녹음이 취소됐어요.'
 };
 const active=['calibrating','listening','speech','transcribing'].includes(capture);
 const terminal=Object.prototype.hasOwnProperty.call(pttReasons,reason);
 $('ptt-state').hidden=!inputFailed&&!active&&!terminal;
 $('ptt-state').textContent=next.ptt?.cleanup_fault?'녹음 정리를 확인해 주세요. 앱을 종료한 뒤 검수해 주세요.':
  terminal&&!active?pttReasons[reason]:
  inputFailed?(pttReasons[reason]||'녹음을 완료하지 못했어요. 마이크 설정과 연결을 확인해 주세요.'):
  ({calibrating:'주변 소리를 확인해요',listening:'듣고 있어요',speech:'말소리를 감지했어요',transcribing:'말을 확인하고 있어요'})[capture]||'';
 if(next.mode==='game_menu'&&!document.querySelector('dialog[open]:not(#game-menu)')){if(!$('game-menu').open)$('game-menu').showModal();$('game-list').replaceChildren();if(!next.games.catalog.length)$('game-list').textContent='게임 자료가 아직 준비되지 않았어요.';for(const item of next.games.catalog){const b=document.createElement('button');b.textContent=item.title;b.disabled=item.readiness!=='READY';b.onclick=()=>intent('launch_game',item.id).catch(()=>error('게임을 시작하지 못했어요.'));$('game-list').append(b);}}
 else if($('game-menu').open)$('game-menu').close();
 $('foreground').hidden=!['game','photo'].includes(next.mode);$('foreground-status').textContent=next.mode==='game'?'게임 실행 중':next.photo_countdown!==null?'사진을 준비하고 있어요.':'사진 처리 결과를 확인해 주세요.';
 const face=frame.contentWindow?.BMOFace,gaze=next.gaze,phase=window.BMOIntegration?.state?.phase;
 if(face&&gaze){const native=[phase,face.getState().activity].some(p=>['listening','thinking'].includes(p));if(native&&gaze.owner!=='manual')lastGaze=null;
 else if(gaze.apply){const id=JSON.stringify([gaze.owner,gaze.x,gaze.y,next.mode_generation]);if(id!==lastGaze){face.look(gaze.x,gaze.y);face.lookEyes(gaze.x*.15,gaze.y*.15);if(gaze.owner==='reaction')face.blink();lastGaze=id;}}}
}
function renderFeatures(next){
 if(lastFeatureMode==='music'&&next.mode==='conversation'&&$('music-dialog').open)$('music-dialog').close();
 lastFeatureMode=next.mode;
 const video=next.camera_video,recording=video?.recording===true,unknown=video?.code==='video_close_failed';
 $('video-indicator').hidden=!recording&&!unknown;
 $('video-indicator').textContent=unknown?'영상 종료를 확인하지 못했어요. 검수가 필요해요.':'● 모의 영상 녹화 중 · 소리 없음';
 const videoReady=next.simulation===true&&video?.readiness==='READY';
 $('video-readiness').textContent=!next.simulation?'실물 영상 기능은 사용자 검수 전 사용할 수 없어요.':video?.readiness==='ASSET_PENDING'?'실행 전에 저장 위치를 선택해 주세요.':unknown?'영상 정리가 확인되지 않았어요. 새 녹화를 시작하지 마세요.':'선택한 저장 위치에 합성 테스트 영상만 저장해요.';
 $('video-start').disabled=!videoReady||recording||!$('video-consent').checked||next.conversation_busy||next.mode!=='conversation';
 $('video-stop').disabled=!recording;
 const result=video?.result,saved=result?.completed===true;
 $('video-result').textContent=saved?'모의 영상 저장과 검증을 완료했어요.':result&&['failed','unknown','rejected'].includes(result.status)?'영상 결과를 확인해 주세요. 자동 재시도하지 않아요.':'';
 $('video-download').hidden=!saved;if(saved)$('video-download').href='/api/video/'+encodeURIComponent(result.video_id);
 const cameraReady=next.capabilities?.camera==='mock';
 $('camera-readiness').textContent=cameraReady?'합성 사진·미리보기·위치 추적을 검사해요.':'실물 카메라는 사용자 검수 전 사용할 수 없어요.';
 for(const id of ['photo-take','camera-preview','camera-track'])$(id).disabled=!cameraReady||next.conversation_busy;
 const preview=next.camera?.preview_available&&$('camera-dialog').open;
 $('camera-image').hidden=!preview;if(preview)$('camera-image').src='/api/preview?sequence='+next.sequence;
 $('music-readiness').textContent=next.media?.catalog?.length?'현재 목록은 검증된 모의 재생 자료예요. 실제 음원·출력은 별도 검수가 필요해요.':'사용자 음원과 출처·라이선스 검토가 필요해요.';
 const catalogKey=JSON.stringify(next.media?.catalog||[]);
 if(catalogKey!==musicCatalogKey){musicCatalogKey=catalogKey;$('music-list').replaceChildren();for(const item of next.media?.catalog||[]){const b=document.createElement('button');b.textContent=item.title;b.disabled=item.readiness!=='READY'||next.capabilities?.music!=='mock';b.onclick=()=>intent('play_song',item.id).catch(()=>error('음악을 시작하지 못했어요.'));$('music-list').append(b);}}
 for(const id of ['music-pause','music-resume','music-next','music-stop'])$(id).disabled=next.mode!=='music';
 const telemetry=next.system_diagnostics?.telemetry;
 $('system-readiness').textContent=telemetry?.power?.status==='OBSERVED'?('전원 현재 이상: '+(telemetry.power.current_fault?'있음':'없음')+' · 과거 기록: '+(telemetry.power.historical_fault?'있음':'없음')):'온도·전원 실측은 아직 실행하지 않았어요. 자동 설정 변경은 하지 않아요.';
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
 control('ptt').then(result=>{if(current===pttEpoch&&pttPending)pttRequest=result.request_id;}).catch(()=>{if(current===pttEpoch){pttPending=false;error('버튼 녹음을 시작하지 못했어요.');}});}
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
function key(event,pressed){if(event.key==='Enter'&&!pressed)enterDown=false;if(event.repeat)return;
 if(pressed&&event.ctrlKey&&event.altKey&&event.code==='KeyD'&&state?.developer_enabled&&state.phase==='ready'){event.preventDefault();location.assign('/developer');return;}
 if(event.key==='Escape'){
  event.preventDefault();event.stopPropagation();
  const covering=document.querySelector('dialog[open]:not(#game-menu)');
  if(covering){if(pressed){cancelPTT();covering.close();}return;}
  if(['game','game_menu'].includes(currentMode)){control('input',{key:'ESC',pressed}).catch(()=>error('게임 종료 상태를 확인해 주세요.'));return;}
  if(pressed){cancelPTT();for(const d of document.querySelectorAll('dialog[open]'))d.close();menu();}return;}
 if(['BUTTON','INPUT','SELECT','TEXTAREA'].includes(event.target.tagName))return;
 if(event.key==='Enter'&&currentMode==='conversation'&&!document.querySelector('dialog[open]')&&state?.phase==='ready'){
  event.preventDefault();event.stopPropagation();if(!pressed){enterDown=false;return;}if(enterDown)return;enterDown=true;ptt();return;}
 if(['game','game_menu'].includes(currentMode)&&keys[event.key]){event.preventDefault();event.stopPropagation();control('input',{key:keys[event.key],pressed}).catch(()=>error('게임 입력 상태를 확인해 주세요.'));return;}}
window.addEventListener('keydown',event=>key(event,true));window.addEventListener('keyup',event=>key(event,false));
window.addEventListener('blur',cancelPTT);
let touchStart=null,inputDocument=null;function bindFrameInputs(){try{const doc=frame.contentDocument;if(!doc||doc===inputDocument)return;inputDocument=doc;doc.addEventListener('keydown',event=>key(event,true),true);doc.addEventListener('keyup',event=>key(event,false),true);doc.defaultView.addEventListener('blur',cancelPTT);
 doc.addEventListener('pointerdown',event=>{touchStart={x:event.clientX,y:event.clientY};if(event.clientX<60&&event.clientY<60)touchStart.timer=setTimeout(menu,1200);});for(const name of ['pointerup','pointercancel'])doc.addEventListener(name,()=>{clearTimeout(touchStart?.timer);touchStart=null;});doc.addEventListener('pointermove',event=>{if(touchStart&&Math.hypot(event.clientX-touchStart.x,event.clientY-touchStart.y)>12){clearTimeout(touchStart.timer);touchStart=null;}});}catch(_){}}
frame.addEventListener('load',()=>{cancelPTT();bindFrameInputs();});bindFrameInputs();
window.addEventListener('pagehide',()=>{cancelPTT();closed=true;epoch++;clearTimeout(timer);controller?.abort();window.BMOIntegration?.close();});
document.addEventListener('visibilitychange',()=>{epoch++;clearTimeout(timer);controller?.abort();if(document.hidden)cancelPTT();else{refreshAdapter();poll();}});
window.BMODesktop={state:null,desktop,control,intent,menu};poll();
})();
