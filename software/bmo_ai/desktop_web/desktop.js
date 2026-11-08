/* Product wrapper only. The pinned R10 and Python audio owner stay unchanged. */
(()=>{'use strict';
const $=id=>document.getElementById(id),frame=$('face'),token=document.querySelector('meta[name="bmo-command-token"]').content;
let pollWorker=null,pollOwner="main_fallback",pollSerial=0,renderSerial=Promise.resolve();
let state=null,closed=false,timer=null,controller=null,epoch=0,handshakeFrame=null,lastSuccess=performance.now(),currentMode='conversation',lastGaze=null,presentedRun=null,presentationGeneration=0;
let enterDown=false,pttEnabled=false,pttPending=false,pttRequest=null,pttEpoch=0,pttFinishPending=false;
let selectKey=null,selectTimer=null;const panelParents=new Map();
let featureState=null,musicCatalogKey=null,gameCatalogKey=null,lastFeatureMode=null;
let nativeLaunchPending=null,nativeInputActive=false,nativeInputEpoch=0;
const nativeHeldKeys=new Set();
let photoReviewKeyHeld=false,photoPreviewContext=null,photoPreviewAcknowledged=null,photoFocusPending=null,suppressedCaption=null,subtitlePending=null,subtitleRequest=0;
let videoKeyHeld=false,videoPreviewContext=null,videoPreviewAcknowledged=null,videoActionPending=false,videoReviewId=null;
let volumePending=null,volumeSerial=0,volumeRequest=null,volumeQueue=Promise.resolve(),armCatalogKey=null,voiceCatalogKey=null;
let faceVolumeTimer=null,uiActivityTimer=null,uiActivitySent=false,uiMenuSequence=null;
let noticeUntil=0,noticeText='',presenceTestPending=false,presenceTestSeen=null,armPreferencePending=false,armRequest=null;
let mediaKeyHeld=false,mediaVideoKey=null,mediaSeekPending=false,mediaVideoFailure=null,mediaSyncRequired=false,mediaPlayPending=null;
let mediaHoldTimer=null,mediaHoldStarted=0,mediaHoldTriggered=false,mediaStopping=false,mediaControlsTimer=null,musicErrorTimer=null;
let previewRun=null,previewSerial=0,previewTimer=null,previewAbort=null,previewBitmap=null,previewDraw=null,previewBusy=false,previewSuppressedKey=null;
const previewEvidence={target_fps:20,active:false,pending:0,max_pending:0,requests:0,received:0,decoded:0,displayed:0,
 dropped:0,not_displayed:0,duplicates:0,errors:0,last_received_id:null,last_decoded_id:null,last_displayed_id:null,
 last_frame_age_ms:null,last_display_age_ms:null,last_display_age_upper_bound_ms:null,
 age_basis:'server_age_plus_client_elapsed; transit_unknown',received_fps:null,decoded_fps:null,displayed_fps:null};
const previewSamples={received:[],decoded:[],displayed:[]};
const inputEvidence={enter_presses:0,keyups:0,repeats_ignored:0,blocked_mode:0,request_sent:0,request_accepted:0,request_rejected:0,cancelled_blur:0,physical_identity:'UNVERIFIED'};
function bump(name){inputEvidence[name]=Math.min(999999,inputEvidence[name]+1);}
const messages={credentials_missing:'최초 키 설정이 필요해요. 실행 가이드를 확인해 주세요.',greeting_missing:'시작 인사 캐시가 아직 준비되지 않았어요.',greeting_unapproved:'시작 인사의 청취 승인이 필요해요.',greeting_corrupt:'시작 인사 캐시를 확인해 주세요.',face_ready_timeout:'얼굴 준비 시간이 초과됐어요.',host_startup_timeout:'대화 엔진 준비 시간이 초과됐어요.',greeting_timeout:'시작 인사 재생 정리를 기다리고 있어요.',browser_disconnected:'연결을 다시 확인하고 있어요.'};
async function post(path,payload){const response=await fetch(path,{method:'POST',keepalive:['cancel','media_observation'].includes(payload.action),headers:{'Content-Type':'application/json','X-BMO-Token':token},body:JSON.stringify({version:1,nonce:crypto.randomUUID(),payload})});if(!response.ok)throw Error('요청을 완료하지 못했어요.');return response.json();}
const desktop=(action,value=null)=>post('/api/desktop',{action,value});
function hideCaption(){$('subtitles').textContent='';$('subtitles').hidden=true;}
function clearCaption(){const current=window.BMOIntegration?.state;suppressedCaption=current?.utterance_id||null;hideCaption();}
function control(action,value=null){
 if(nativeOwnsInput()&&['gamepad','game_key','game_ready','game_event','input'].includes(action))
  return Promise.resolve({status:'rejected',code:'native_input_owned'});
 if(['ptt','question','cancel','stop','reset','repeat'].includes(action))clearCaption();
 if(['cancel','stop','reset'].includes(action)||action==='preview'&&value===false)stopPreview(true);
 if(action==='subtitles'&&['off','en','ko'].includes(value)){
  const serial=++subtitleRequest;subtitlePending={mode:value,serial};hideCaption();
  return post('/api/control',{action,value}).catch(error=>{if(subtitlePending?.serial===serial)subtitlePending=null;throw error;});
 }
 return post('/api/control',{action,value});
}
function intent(name,target=null,media_mode=null){
 if(name==='return_to_face'){clearCaption();stopVideoReview();stopPreview(true);}
 const payload={name,target};if(media_mode!==null)payload.media_mode=media_mode;
 const nativeRequest=name==='launch_game'&&target==='supertux'?{request_id:null}:null;
 if(nativeRequest){nativeLaunchPending=nativeRequest;retireBrowserGameInput();}
 return post('/api/intent',payload).then(result=>{
  if(nativeRequest&&nativeLaunchPending===nativeRequest){
   nativeRequest.request_id=result.request_id;
   if(!['accepted','running'].includes(result.status))nativeLaunchPending=null;
  }
  return result;
 }).catch(reason=>{if(nativeRequest&&nativeLaunchPending===nativeRequest)nativeLaunchPending=null;throw reason;});
}
function nativeOwnsInput(next=featureState){return nativeLaunchPending!==null||next?.games?.native?.active===true;}
function syncNativeGame(next){
 const native=next.games?.native;
 if(nativeLaunchPending?.request_id){
  const receipt=(next.requests||[]).find(item=>item.request_id===nativeLaunchPending.request_id);
  if(native?.active===true||receipt&&!['accepted','running'].includes(receipt.status))nativeLaunchPending=null;
 }
 const active=nativeOwnsInput(next);
 if(active&&!nativeInputActive)retireBrowserGameInput();
 nativeInputActive=active;
 const status=$('native-game-status');status.hidden=!native?.enabled;
 if(!status.hidden){
  const label={not_installed:'SuperTux is not installed.',setup_required:'SuperTux needs setup before launch.',
   ready:'SuperTux is ready to launch. Screen and controller checks are still required.',
   starting:'Starting SuperTux. Waiting for its window…',running:'SuperTux is open.',
   failed:'SuperTux could not finish this session.',exited:'SuperTux has closed.',
   cleanup_failed:'SuperTux could not be fully closed. Check the running session before starting again.'}[native.state]||'SuperTux is unavailable.';
  status.dataset.state=native.state||'unknown';status.dataset.code=native.code||'';
  status.textContent=label+(native.audio_scope==='silent_native'?' This version plays without game audio.':'');
 }
}
function error(message){
 noticeText=message;noticeUntil=performance.now()+4200;
 const notice=$('notice');notice.hidden=false;$('notice-message').textContent=message;
 if(currentMode==='music'){
  notice.dataset.musicError='true';clearTimeout(musicErrorTimer);
  musicErrorTimer=setTimeout(()=>{delete notice.dataset.musicError;notice.hidden=true;},4200);
 }
}
function syncShowcaseGames(){
 const hide=$('demo-games-hide').checked;
 $('games').hidden=hide;
 $('games-readiness').dataset.showcaseHidden=String(hide);
}
try{$('demo-games-hide').checked=localStorage.getItem('bmo-showcase-games-hidden')==='true';}catch(_){}
$('demo-games-hide').onchange=()=>{
 try{localStorage.setItem('bmo-showcase-games-hidden',String($('demo-games-hide').checked));}catch(_){}
 syncShowcaseGames();
};
syncShowcaseGames();
function syncUiActivity(){
 clearTimeout(uiActivityTimer);uiActivityTimer=null;
 const active=!closed&&!document.hidden&&(!!document.querySelector('dialog[open]')||!$('face-volume').hidden);
 if(active||uiActivitySent){uiActivitySent=active;desktop('ui_activity',active).catch(()=>{});}
 if(active)uiActivityTimer=setTimeout(syncUiActivity,2000);
}
function hideFaceVolume(){
 clearTimeout(faceVolumeTimer);faceVolumeTimer=null;
 const hadFocus=$('face-volume').contains(document.activeElement)||document.activeElement===$('face-volume-trigger');
 $('face-volume').hidden=true;$('face-volume-trigger').setAttribute('aria-expanded','false');
 syncUiActivity();if(hadFocus)restoreFaceFocus();
}
function showFaceVolume(){
 if(!canTalk()||nativeOwnsInput()||currentMode!=='conversation'||document.querySelector('dialog[open]'))return;
 $('face-volume').hidden=false;$('face-volume-trigger').setAttribute('aria-expanded','true');
 clearTimeout(faceVolumeTimer);faceVolumeTimer=setTimeout(hideFaceVolume,3000);syncUiActivity();
 renderVolume(featureState||{});
}
function menu(){hideFaceVolume();cancelPTT();window.BMOPlayableGames?.suspend('menu');for(const dialog of document.querySelectorAll('dialog[open]'))if(dialog!==$('menu'))dialog.close();if(!$('menu').open){$('menu').showModal();$('conversation-menu').focus({preventScroll:true});}syncUiActivity();}
function closeToFace(){for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();if(currentMode==='game_menu')intent('return_to_face').catch(()=>error('복귀 상태를 확인해 주세요.'));restoreFaceFocus();}
function backPanel(){
 const dialog=document.querySelector('dialog[open]');if(!dialog)return;
 if(dialog.id==='menu'){closeToFace();return;}
 const parent=panelParents.get(dialog.id)||'menu';
 if(dialog.id==='game-menu')intent('return_to_face').catch(()=>error('복귀 상태를 확인해 주세요.'));
 if(parent==='menu')menu();else openPanel(parent,false);
}
function panelControls(dialog){return [...dialog.querySelectorAll('button,input,select,textarea,a[href]')].filter(el=>!el.disabled&&el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden');}
function moveMenuFocus(dialog,key){
 const controls=panelControls(dialog),current=controls.includes(document.activeElement)?document.activeElement:null;
 if(!controls.length)return;if(!current){controls[0].focus();return;}
 const rect=current.getBoundingClientRect(),x=rect.x+rect.width/2,y=rect.y+rect.height/2,horizontal=['ArrowLeft','ArrowRight'].includes(key),sign=['ArrowRight','ArrowDown'].includes(key)?1:-1;
 const candidates=controls.filter(el=>el!==current).map(el=>{const r=el.getBoundingClientRect(),dx=r.x+r.width/2-x,dy=r.y+r.height/2-y;return {el,forward:(horizontal?dx:dy)*sign,cross:Math.abs(horizontal?dy:dx)};}).filter(row=>row.forward>4).sort((a,b)=>(a.forward+a.cross*3)-(b.forward+b.cross*3));
 if(candidates.length)candidates[0].el.focus({preventScroll:false});
}
function isMenuTextEditing(target){return !!(target?.isContentEditable||target?.closest?.('textarea,input:not([type=checkbox]):not([type=range]):not([type=button]):not([type=submit])'));}
function activateMenuControl(target){if(!target)return;if(target.matches('select,input[type=range]')){target.dataset.bmoAdjusting=String(target.dataset.bmoAdjusting!=='true');return;}target.click();}
function clearSelectKey(){clearTimeout(selectTimer);selectTimer=null;selectKey=null;}
function menuKey(event,pressed){
 const isSelect=event.code==='KeyZ'||event.key?.toLowerCase()==='z',dialog=document.querySelector('dialog[open]');
 // Consume a held GP6 release even when its long press already closed a panel.
 if(isSelect&&selectKey){event.preventDefault();event.stopPropagation();if(pressed)return true;
  const held=selectKey;clearSelectKey();if(!held.long&&held.dialog===dialog){if(dialog){const target=panelControls(dialog).includes(document.activeElement)?document.activeElement:panelControls(dialog)[0];activateMenuControl(target);}else if(faceTapAllowed())menu();}return true;}
 // Native game input never enters this menu navigation path.
 if(currentMode==='game'||nativeOwnsInput())return false;
 if(isSelect&&(dialog||faceTapAllowed())&&!isMenuTextEditing(event.target)){
  event.preventDefault();event.stopPropagation();if(!pressed||event.repeat)return true;
  selectKey={dialog,long:false};if(dialog)selectTimer=setTimeout(()=>{if(selectKey?.dialog===dialog&&dialog.open){selectKey.long=true;backPanel();}},900);return true;
 }
 if(dialog&&['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(event.key)&&!isMenuTextEditing(event.target)){
  if(event.target?.dataset?.bmoAdjusting==='true')return false;
  event.preventDefault();event.stopPropagation();if(pressed)moveMenuFocus(dialog,event.key);return true;
 }return false;
}
function isTextInput(target){return !!(target?.isContentEditable||target?.closest?.('input,select,textarea'));}
function restoreFaceFocus(){
 if(closed||!canTalk()||currentMode!=='conversation'||document.querySelector('dialog[open]')||isTextInput(document.activeElement))return;
 frame.focus({preventScroll:true});
}
for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('close',()=>{for(const input of dialog.querySelectorAll('[data-bmo-adjusting]'))delete input.dataset.bmoAdjusting;syncUiActivity();restoreFaceFocus();if(featureState)syncPreview(featureState);});
function refreshAdapter(){if(window.BMOIntegration?.closed){window.BMOIntegration=new window.BMOFaceAdapter(frame);}handshakeFrame=null;presentedRun=null;presentationGeneration++;}
function canTalk(){return state?.phase==='ready'||state?.character_life?.enabled===true&&state?.phase==='greeting';}
function presentFace(run){
 const generation=++presentationGeneration,adapter=window.BMOIntegration,doc=frame.contentDocument;
 const acknowledge=()=>{if(generation!==presentationGeneration)return;
  if(!closed&&!document.hidden&&state?.run_id===run&&$('splash').hidden&&frame.isConnected
    &&adapter&&!adapter.closed&&window.BMOIntegration===adapter&&frame.contentDocument===doc&&handshakeFrame===doc)
   desktop('face_presented',{run_id:run}).catch(()=>{if(generation===presentationGeneration)presentedRun=null;});
  else presentedRun=null;};
 const done=()=>requestAnimationFrame(()=>requestAnimationFrame(acknowledge));
 let shown=false;try{shown=sessionStorage.getItem('bmo-face-entrance')===run;}catch(_){}
 if(state?.character_life?.enabled&&!shown){
  try{sessionStorage.setItem('bmo-face-entrance',run);}catch(_){}
  // Mount the expression-only transform synchronously before the first reveal
  // can paint. The background/main/iframe stay in their original fixed place.
  const presentation=adapter?.presentExpression({
    reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches});
  Promise.resolve(presentation).then(done,done);
 }else done();}
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
  const run=next.run_id;presentedRun=run;presentFace(run);}
 // Values are achieved backend checkpoints, not an elapsed-time progress guess.
 const progress=Number.isInteger(next.progress_percent)?Math.max(0,Math.min(100,next.progress_percent)):0;
 $('progress-percent').textContent=progress+'%';
 $('progress-track').setAttribute('aria-valuenow',String(progress));
 $('progress-fill').style.width=progress+'%';
 const stages={lock_acquired:'BMO 실행을 시작했어요.',server_ready:'얼굴 서버 준비 완료',browser_registered:'화면 창을 준비하고 있어요.',browser_spawned:'화면 창 실행 완료',engine_ready:'대화 엔진 준비 완료',face_handshake_verified:'BMO 얼굴 연결 확인',local_app_ready:'시작 준비 완료'};
 if(next.character_life?.enabled){$('splash').querySelector('h1').textContent='Starting BMO…';
  $('progress-track').setAttribute('aria-label','Getting BMO ready');
  $('loading-stage').textContent=next.progress_percent===100?'Ready!':'Getting ready…';
 }else $('loading-stage').textContent=stages[next.progress_stage]||'실행 상태를 확인하고 있어요.';
 // An optional spoken greeting never reserves or covers the normal face.
 // Its state, distinct from the approved startup chime, belongs in readiness.
 const problem=next.phase==='error',transient=performance.now()<noticeUntil;
 $('notice').hidden=!problem&&!transient;
 if(problem||transient)$('notice-message').textContent=transient?noticeText:messages[next.code]||'준비 상태를 확인해 주세요. 메뉴에서 재시도하거나 종료할 수 있어요.';
 $('diagnostic').textContent=next.code;$('talk').disabled=!canTalk();$('repeat').disabled=next.phase!=='ready';
 const greeting=next.spoken_greeting_status||next.code;
 $('greeting-readiness').textContent='시작 인사: '+(messages[greeting]||({greeting_completed:'재생 완료',greeting_cancelled:'취소됨'})[greeting]||greeting)+' · '+greeting;
 const chime=next.startup_chime_status||next.character_life?.startup_chime_status||'NOT_ATTEMPTED';
 $('chime-readiness').textContent='시작 효과음: '+chime;
 $('presence-controls').hidden=!next.character_life?.enabled;
 if(next.character_life?.enabled)$('presence-mode').value=next.character_life.presence.mode;
 renderPresenceTest(next);
 $('greeting-retry').disabled=next.phase!=='ready'||next.code==='greeting_completed';}
function renderPresenceTest(next){
 const test=next.character_life?.manual_test||{},active=['queued','preparing','playing'].includes(test.state);
 $('presence-test').disabled=presenceTestPending||active||!next.character_life?.enabled||!canTalk();
 const key=JSON.stringify([test.state,test.code,test.attempts]);
 if(presenceTestSeen!==key){
  const changed=presenceTestSeen!==null;presenceTestSeen=key;
  if(changed&&['blocked','error'].includes(test.state))presenceTestMessage(test.code);
 }
 const presence=next.character_life?.presence;
 $('presence-rule').textContent='자동: 3–5분마다 기회 판단 · 최근 15분에 최대 3회 · 같은 대사는 30분 동안 쉬어요.';
 $('presence-readiness').textContent=JSON.stringify({presence:presence||null,manual_test:test},null,2);
}
function presenceTestMessage(code){
 const messages={presence_test_muted:'음소거 중이에요. 볼륨에서 Mute를 끈 뒤 다시 눌러 주세요.',
  presence_test_busy:'혼잣말을 이미 준비하거나 말하고 있어요.',presence_test_conversation_active:'대화가 끝난 뒤 다시 눌러 주세요.',
  presence_test_audio_busy:'다른 소리가 끝난 뒤 다시 눌러 주세요.',presence_test_game_active:'게임에서 얼굴로 돌아온 뒤 눌러 주세요.',
  presence_test_recording_active:'녹화가 끝난 뒤 눌러 주세요.',presence_test_foreground_activity:'얼굴 화면으로 돌아온 뒤 눌러 주세요.',
  presence_test_recent_distress:'지금은 조용히 함께 있을게요. 조금 뒤에 다시 눌러 주세요.',
  presence_test_strong_emotion:'지금은 조용히 함께 있을게요. 조금 뒤에 다시 눌러 주세요.',
  presence_test_assets_unavailable:'사용할 혼잣말 음성이 없어요. 음성팩 설정을 확인해 주세요.',
  presence_test_not_ready:'BMO 준비가 끝난 뒤 다시 눌러 주세요.'};
 error(messages[code]||'혼잣말을 재생하지 못했어요. 준비 상태를 확인해 주세요.');
}
async function modes(){if(!canTalk())return;const r=await fetch('/api/features',{cache:'no-store'});if(!r.ok)return;const next=await r.json();currentMode=next.mode;
 window.BMODesktop.features=next;
 featureState=next;syncNativeGame(next);renderFeatures(next);
 syncGamepad(next);
 pttEnabled=next.ptt?.enabled===true&&next.capabilities?.conversation_ptt_microphone!=='denied';
 // Bind completion to THIS admitted command, rather than a stale snapshot's
 // idle phase or a previous capture generation. Queued input stays cancellable.
 const receipt=pttRequest&&(next.requests||[]).find(item=>item.request_id===pttRequest);
 if(receipt&&!['accepted','running'].includes(receipt.status)){pttPending=false;pttRequest=null;}
 const capture=next.ptt?.state;
 const diagnosis=next.turn_diagnostics,failure=diagnosis?.failure;
 const inputFailed=capture==='failed'||next.ptt?.cleanup_fault===true||failure;
 const wakePhase=next.wake_word?.handoff?.phase;
 const wakeBoundary=next.microphone_broker?.question_boundary?.status;
 const wakeRetry=['AMBIGUOUS_PREFIX_RETRY_REQUIRED','FRESH_AFTER_UTTERANCE_QUIET'].includes(wakeBoundary)
  &&['waiting_wake_end','question_listening'].includes(wakePhase);
 const wakeHint=wakeRetry?'Please pause, then ask again. Or press the button.':
  ({confirming:'One moment…',waiting_wake_end:'I heard you…',question_listening:'I’m listening. What’s up?'})[wakePhase]||'';
 $('ptt-finish').hidden=next.mode!=='conversation'||!['listening','speech'].includes(capture)||!!document.querySelector('dialog[open]');
 $('ptt-finish').disabled=pttFinishPending;
 $('ptt-state').hidden=!inputFailed&&!wakeHint&&!['calibrating','listening','speech','transcribing'].includes(capture);
 const failures={no_speech:'말소리를 감지하지 못했어요. 버튼을 누르고 다시 말해 주세요.',stt_empty:'말을 알아듣지 못했어요. 다시 말해 주세요.'};
 $('ptt-state').textContent=inputFailed?(next.ptt?.cleanup_fault?'녹음 정리를 확인해 주세요. 앱을 종료한 뒤 검수해 주세요.':failures[failure?.code]||(capture==='failed'&&failure?.stage!=='stt'?'녹음을 완료하지 못했어요. 마이크 설정과 연결을 확인해 주세요.':'대화를 완료하지 못했어요. 준비 상태에서 실패 단계를 확인해 주세요.')):
  wakeHint||({calibrating:'주변 소리를 확인해요',listening:'듣고 있어요',speech:'말소리를 감지했어요',transcribing:'말을 확인하고 있어요'})[capture]||'';
 if(next.mode==='game_menu'&&!document.querySelector('dialog[open]:not(#game-menu)')){
  const openingGameMenu=!$('game-menu').open;
  const key=JSON.stringify([next.games.catalog,next.games_readiness,next.game_setup_error]);
  if(key!==gameCatalogKey){gameCatalogKey=key;$('game-list').replaceChildren();
   if(!next.games.catalog.length){const readiness=next.games_readiness||{},code=readiness.code||next.game_setup_error||'game_catalog_empty';
    const reasons={games_disabled:'현재 실행에서 게임이 꺼져 있어요. 후보 실행 설정을 확인해 주세요.',playable_games_disabled:'현재 실행에서 게임이 꺼져 있어요. 후보 실행 설정을 확인해 주세요.',game_bundle_missing:'게임 묶음 파일이 없어요. 후보 설치 상태를 확인해 주세요.',game_bundle_hash_mismatch:'게임 묶음 검증에 실패했어요. 검토된 후보 파일을 확인해 주세요.',game_manifest_hash_mismatch:'게임 목록 SHA가 검토된 후보와 달라요.',game_asset_hash_mismatch:'게임 파일 SHA가 검토된 후보와 달라요.',game_pack_missing:'게임 묶음 파일이 없어요. 후보 설치 상태를 확인해 주세요.'};
    $('game-list').textContent=(reasons[code]||'게임을 준비하지 못했어요. 준비 상태의 원인을 확인해 주세요.')+' · '+code;}
   for(const item of next.games.catalog){const b=document.createElement('button');b.dataset.gameId=item.id;b.textContent=item.title;b.disabled=item.readiness!=='READY';
    if(item.id==='supertux'){b.dataset.nativeGame='true';b.dataset.readiness=item.readiness;
     const native=next.games.native;
     b.textContent+=' · '+(item.readiness==='READY'?'Ready to launch':native?.state==='not_installed'?'Not installed':'Setup needed');
     b.setAttribute('aria-describedby','native-game-status');}
    b.onclick=()=>intent('launch_game',item.id).catch(()=>error('게임을 시작하지 못했어요.'));$('game-list').append(b);}
  }
  for(const button of $('game-list').querySelectorAll('button'))button.setAttribute('aria-current',String(button.dataset.gameId===next.games.selected_id));
  if(openingGameMenu){$('game-menu').showModal();($('game-list').querySelector('button:not(:disabled)')||$('game-back')).focus({preventScroll:true});}
 }
 else if($('game-menu').open)$('game-menu').close();
 window.BMOPlayableGames?.sync(next);
 $('foreground').hidden=!['game','photo','video'].includes(next.mode)||next.mode==='game'&&next.games?.web?.active===true;
 const face=frame.contentWindow?.BMOFace,gaze=next.gaze,phase=window.BMOIntegration?.state?.phase;
 if(face&&gaze){const native=[phase,face.getState().activity].some(p=>['listening','thinking'].includes(p));if(native&&gaze.owner!=='manual')lastGaze=null;
 else if(gaze.apply){const id=JSON.stringify([gaze.owner,gaze.x,gaze.y,next.mode_generation]);if(id!==lastGaze){face.look(gaze.x,gaze.y);face.lookEyes(gaze.x*.15,gaze.y*.15);if(gaze.owner==='reaction')face.blink();lastGaze=id;}}}
}
function renderFeatures(next){
 const requestedMenu=next.ui_menu;
 if(Number.isInteger(requestedMenu?.sequence)&&requestedMenu.sequence>=0&&requestedMenu.sequence!==uiMenuSequence){
  const apply=uiMenuSequence!==null||requestedMenu.sequence>0;uiMenuSequence=requestedMenu.sequence;
  if(apply){if(requestedMenu.open===true)menu();else if(requestedMenu.open===false){
   hideFaceVolume();for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();restoreFaceFocus();
  }}
 }
 const faceControls=canTalk()&&!nativeOwnsInput(next)&&next.mode==='conversation'&&!document.querySelector('dialog[open]');
 $('face-volume-trigger').hidden=!faceControls;
 if(!faceControls&&!$('face-volume').hidden)hideFaceVolume();
 if(lastFeatureMode==='music'&&next.mode==='conversation'&&$('music-dialog').open)$('music-dialog').close();
 lastFeatureMode=next.mode;
 const wakeConfigured=next.wake_word?.requested===true,wakeEnabled=next.wake_word?.armed===true;
 $('wake-on').disabled=!wakeConfigured||wakeEnabled||next.mode!=='conversation'||next.conversation_busy||next.audio_owner!==null;
 $('wake-off').disabled=!wakeEnabled&&next.wake_word?.enabled!==true;
 $('wake-readiness').textContent=!wakeConfigured?'호출어를 사용하려면 준비된 모델과 호출어 옵션을 지정해 앱을 다시 실행해 주세요. GP8은 그대로 사용할 수 있어요.':
  wakeEnabled?'호출어가 선택됐어요. 실제 모델 인식·ACK 자산 상태는 준비 상태에서 확인해 주세요.':'현재 실행에 준비한 모델만 사용해요. 자동 다운로드하지 않아요.';
 const video=next.camera_video||next.video,videoScreen=next.mode==='video',videoPhase=next.video_phase||'idle',recording=video?.recording===true||videoScreen&&videoPhase==='recording',unknown=video?.code==='video_close_failed';
 $('video-indicator').hidden=!recording&&!unknown;
 const liveVideo=next.capabilities?.camera_video==='explicit_supervised';
 $('video-explanation').textContent='소리 없이 영상을 저장해요. 실제 미리보기가 표시된 뒤 5초 후 시작하고, GP8 또는 Stop으로 저장해요.';
 const elapsed=Number.isFinite(next.video_elapsed)?Math.max(0,next.video_elapsed):Number.isFinite(video?.elapsed)?Math.max(0,video.elapsed):0;
 const elapsedText=Math.floor(elapsed/60)+':'+String(Math.floor(elapsed)%60).padStart(2,'0');
 $('video-indicator').textContent=unknown?'영상 종료를 확인하지 못했어요. 검수가 필요해요.':'● REC '+elapsedText+' · 소리 없음';
 const liveBackendAvailable=next.system_diagnostics?.camera?.available===true;
 const videoReady=(next.simulation===true||liveVideo&&liveBackendAvailable)
  &&(video?.readiness==='READY'||video?.readiness==='UNVERIFIED'&&video?.code==='video_ready');
 const videoCodes={video_encoder_unavailable:'압축 영상 encoder가 없어요. 후보 환경의 ffmpeg 준비 상태를 확인해 주세요.',video_probe_unavailable:'영상 검증 도구 ffprobe가 없어요.',video_storage_not_selected:'실행 전에 private 영상 저장 위치를 선택해 주세요.',video_disk_full:'저장 공간이 부족해요.',video_encoder_failed:'영상 압축을 마치지 못했어요.',video_invalid_container:'저장 영상을 검증하지 못했어요.'};
 $('video-readiness').textContent=unknown?'영상 정리가 확인되지 않았어요. 새 녹화를 시작하지 마세요.':videoCodes[video?.code]||
  (liveVideo&&!liveBackendAvailable?'현재 Python 환경에 카메라 라이브러리가 없어요. 실행 가이드를 확인해 주세요.':liveVideo?'기본30초·최대60초 무음 영상이에요. 녹화 중에는 음성 대화를 쉬어요.':!next.simulation?'카메라 영상은 현재 실행의 명시적 opt-in이 필요해요.':video?.readiness==='ASSET_PENDING'?'실행 전에 저장 위치를 선택해 주세요.':'합성 카메라 경계로 앱 흐름을 검수해요. 실제 카메라 품질 검사는 별도예요.');
 $('video-consent').parentElement.lastChild.textContent=liveVideo?'사용자가 보는 동안 실제 카메라 영상을 저장하는 데 동의해요.':'선택한 저장 위치에 모의 영상을 만드는 데 동의해요.';
 $('video-start').textContent='영상 준비';
 $('video-start').disabled=!videoReady||recording||videoActionPending||!$('video-consent').checked||next.conversation_busy||next.mode!=='conversation';
 $('video-stop').disabled=!recording||videoActionPending;
 const result=next.video?.video_id?next.video:video?.result,saved=result?.completed===true;
 $('video-result').textContent=saved?(liveVideo?'카메라 영상 저장과 검증을 완료했어요.':'모의 영상 저장과 검증을 완료했어요.'):result&&['failed','unknown','rejected'].includes(result.status)?'영상 결과를 확인해 주세요. 자동 재시도하지 않아요.':'';
 $('video-download').hidden=!saved;if(saved)$('video-download').href='/api/video/'+encodeURIComponent(result.video_id);
 $('video-download').textContent=liveVideo?'검증된 카메라 영상 파일':'검증된 모의 영상 파일';
 renderVideo(next,video,videoScreen,videoPhase,elapsedText);
 const photoScreen=next.mode==='photo',photoPhase=next.photo_phase||'idle';
 const photoFailure=next.photo_diagnostic;
 const canTakeUnconfirmed=next.camera?.focus?.can_take_anyway!==false,canRefocus=next.photo_refocus_used!==true;
 const focusOptions=[...(canTakeUnconfirmed?['Take anyway']:[]),...(canRefocus?['Refocus']:[]),'Back'];
 const focusMessage='Focus uncertain — '+(focusOptions.length===3?'Take anyway, Refocus, or Back.':focusOptions.join(' or ')+'.');
 const photoReasons={focus:'The camera could not finish focusing.',still_capture:'The camera could not finish that capture.',
  capture_result:'The camera result could not be verified.',frame_validation:'The photo frame could not be verified.',
  storage_prepare:'The photo folder is not ready.',storage:'The photo could not be saved.',
  review:'The saved photo could not be displayed. It may still be on disk.',preview:'The camera preview is not ready.',
  camera_open:'The camera could not be opened.',cancel:'Photo cancelled.'};
 document.body.dataset.photoScreen=String(photoScreen);
 $('photo-view').hidden=!photoScreen;
 if(!videoScreen)$('foreground-status').textContent=next.mode==='game'?(next.games?.native?.active===true?
  ({starting:'Starting SuperTux. Waiting for its window…',running:'SuperTux is open. Return here to stop it.',cleanup_failed:'SuperTux cleanup needs attention.'}[next.games.native.state]||'Preparing SuperTux…'):'게임 실행 중'):
  (photoPhase==='failed'?(photoReasons[photoFailure?.stage]||'Couldn’t finish that photo.'):
   ({preparing:'Getting the camera ready…',preparing_voice:'Getting ready for your photo…',countdown_audio_start:'Starting the five-second countdown…',countdown:'Hold still!',focusing:'Checking the focus…',
    focus_review:focusMessage,capturing:'Taking your photo…',
    photo_review:next.photo?.focus_status==='unconfirmed'?'Photo saved. Focus unconfirmed.':
     next.photo?.focus_status==='unsupported'?'Photo saved. This camera cannot confirm focus.':'Photo saved.'})[photoPhase])||'사진을 준비하고 있어요.';
 if(photoScreen&&photoPhase==='countdown'&&next.photo_countdown_audio?.status==='visual_fallback')$('foreground-status').textContent='Five-second visual countdown · no countdown audio';
 const photoReady=photoScreen&&photoPhase==='photo_review'&&next.photo?.completed===true&&typeof next.photo?.photo_id==='string';
 $('photo-result-image').hidden=!photoReady;$('photo-download').hidden=!photoReady;
 if(photoReady){const url='/api/photo/'+encodeURIComponent(next.photo.photo_id);if($('photo-result-image').getAttribute('src')!==url)$('photo-result-image').src=url;$('photo-download').href=url;}
 const photoLive=photoScreen&&['preparing','preparing_voice','countdown_audio_start','countdown','focusing','focus_review'].includes(photoPhase)&&next.camera?.on===true;
 $('photo-preview-image').hidden=!photoLive;
 const focusChoice=photoScreen&&photoPhase==='focus_review';
 if(!focusChoice||photoFocusPending?.action==='refocus'&&next.photo_refocus_used===true)photoFocusPending=null;
 $('photo-focus-actions').hidden=!focusChoice;
 $('photo-take-anyway').disabled=!focusChoice||photoFocusPending!==null||!canTakeUnconfirmed;
 $('photo-refocus').disabled=!focusChoice||photoFocusPending!==null||!canRefocus;
 const seconds=Number.isFinite(next.photo_countdown)?Math.ceil(next.photo_countdown):null;
 $('photo-countdown').hidden=!(photoScreen&&photoPhase==='countdown'&&seconds>0);
 $('photo-countdown').textContent=seconds===null?'':String(Math.max(1,Math.min(5,seconds)));
 if(['off','en','ko'].includes(next.subtitles?.mode))$('subtitle-mode').value=subtitlePending?.mode||next.subtitles.mode;
 renderCaption();
 const liveCamera=next.capabilities?.camera==='explicit_supervised',cameraAvailable=next.system_diagnostics?.camera?.available===true;
 const cameraReady=next.capabilities?.camera==='mock'||liveCamera&&cameraAvailable;
 $('camera-readiness').textContent=liveCamera?(cameraAvailable?'감독 모드예요. 촬영 버튼을 누를 때 카메라를 열어요.':'현재 Python 환경에 카메라 라이브러리가 없어요. 실행 가이드를 확인해 주세요.'):cameraReady?'합성 사진·미리보기·위치 추적을 검사해요.':'실물 카메라는 사용자 검수 전 사용할 수 없어요.';
 for(const id of ['photo-take','camera-preview','camera-track'])$(id).disabled=!cameraReady||next.conversation_busy;
 if(liveCamera&&!next.system_diagnostics?.camera?.tracking_available)$('camera-track').disabled=true;
 const preview=next.camera?.on===true&&$('camera-dialog').open;
 $('camera-image').hidden=!preview;
 $('camera-preview').textContent=next.camera?.preview===true?'미리보기 끄기':'미리보기 켜기';
 const tracking=next.camera?.tracking===true||next.camera?.tracker?.enabled===true;
 $('camera-track').textContent=tracking?'얼굴 위치 시선 끄기':'얼굴 위치 시선 켜기';
 $('camera-track').setAttribute('aria-pressed',String(tracking));
 if(next.camera?.tracking_suspended===true||Array.isArray(next.camera?.tracking_suspended)&&next.camera.tracking_suspended.length)
  $('camera-readiness').textContent+=' 사진·영상이 끝나면 이전 시선 추적으로 돌아가요.';
 syncPreview(next);
 renderVolume(next);renderArms(next);renderVoiceBank(next);
 renderMusic(next);
 const gameReady=next.games_readiness||{};
 $('games-readiness').textContent='게임 '+(next.games?.catalog?.length||0)+'개 · '+(gameReady.status||'확인 전')+' · '+(gameReady.code||next.game_setup_error||'game_status_unavailable');
 const telemetry=next.system_diagnostics?.telemetry;
 $('system-readiness').textContent=telemetry?.power?.status==='OBSERVED'?('전원 현재 이상: '+(telemetry.power.current_fault?'있음':'없음')+' · 과거 기록: '+(telemetry.power.historical_fault?'있음':'없음')):'온도·전원 실측은 아직 실행하지 않았어요. 자동 설정 변경은 하지 않아요.';
 const arm=next.system_diagnostics?.arms,armLink=arm?.transport?.status;
 const armLaunch=arm?.launcher||{},armTurn=arm?.conversation||{};
 $('arm-readiness').textContent='Arm transport: '+(armLink||'NOT_OBSERVED')+
  ' · supervised launch: '+(armLaunch.supervised_effective===true?'ON':'OFF')+
  ' · conversation gestures: '+(armLaunch.emotion_arms_effective===true?'ON':'OFF')+
  ' · '+(armTurn.blocked_reasons?.join(', ')||armTurn.stage||'not_observed')+
  ' · physical movement: UNVERIFIED';
 if($('system-dialog').open){
  const emotions=new Set(['neutral','happy','sad','surprised','angry','crying']);
  let faceState={};try{faceState=frame.contentWindow?.BMOFace?.getState()||{};}catch(_){}
  const selected=[faceState.selectedEmotion,faceState.emotion].find(value=>emotions.has(value));
  const rendered=[faceState.renderEmotion,faceState.emotion].find(value=>emotions.has(value));
  const config=next.character_life?.effective_config||{},effective={};
  for(const name of ['character_life','emotion_mode','chat_model','expressive_voice','reasoning_effort','fish_model','fish_delivery','fish_gain']){
   if(['string','boolean','number'].includes(typeof config[name]))effective[name]=config[name];
  }
  $('turn-diagnostics').textContent=JSON.stringify({...(next.turn_diagnostics||{enabled:false}),
   runtime_provenance:next.runtime_provenance||null,completed_turns:next.completed_turns||null,
   presence:state?.character_life?.presence||null,
   saved_voice_selection:next.voice_pack?.selection_diagnostics||null,
   expression_trace:next.character_life?.expression_trace||null,effective_config:effective,
   photo:next.photo_diagnostic||null,photo_focus:next.photo_focus||null,photo_feedback:next.photo_feedback||null,
   video:next.camera_video||next.video||null,preview_browser:previewSnapshot(),arms:arm||null,
   tracking:{requested:next.camera?.tracking_requested===true,active:next.camera?.tracking===true,
    suspended:next.camera?.tracking_suspended||[],target:next.camera?.target||null,transform:next.camera?.tracking_transform||null},
   volume:next.volume||null,providers:next.providers||null,
   face_state:{selected_emotion:selected||null,rendered_emotion:rendered||null}},null,2);
 }
 $('input-evidence').textContent=JSON.stringify(inputEvidence,null,2);
 const provider=next.providers?.voice;
 const cloud=next.providers?.cloud;
 const cloudReasons={ready:'OpenAI 오류가 관측되지 않았어요. 실제 인증 확인을 대신하지 않아요.',
  credentials_missing:'OpenAI 대화 키 설정이 필요해요.',missing_key:'OpenAI 대화 키 설정이 필요해요.',missing_sdk:'OpenAI 실행 도구가 준비되지 않았어요.',
  authentication:'OpenAI 인증을 확인해 주세요.',permission:'OpenAI 프로젝트·모델 접근 권한을 확인해 주세요.',model:'설정한 OpenAI 모델을 사용할 수 없어요.',
  rate_limit:'OpenAI 요청·사용량 제한에 도달했어요.',connection:'OpenAI에 연결하지 못했어요.',timeout:'OpenAI 응답 시간이 초과됐어요.',
  server:'OpenAI 서비스에서 오류가 발생했어요.',request:'OpenAI가 요청을 처리하지 못했어요.',cloud:'OpenAI 요청을 완료하지 못했어요.'};
 $('cloud-readiness').textContent=cloud?(cloudReasons[cloud.code]||'OpenAI 대화를 현재 사용할 수 없어요.')+
  (cloud.blocked?(cloud.recovery==='restart_required'?' 설정을 준비한 뒤 앱을 다시 실행해 주세요. 사진·게임·메뉴는 계속 사용할 수 있어요.':' 자동 재시도는 멈췄어요. 설정 적용 후 오류 상태를 초기화할 수 있어요.'):''):'OpenAI 상태는 아직 관측하지 않았어요.';
 const providerReasons={ready:'음성 오류가 관측되지 않았어요. 실제 인증 확인을 대신하지 않아요.',fish_authentication:'Fish 인증을 확인해 주세요. 새 키를 적용한 뒤 앱을 다시 시작하거나 상태 확인을 눌러 주세요.',fish_balance:'Fish 사용 조건·잔액을 확인해 주세요.',fish_permission:'Fish 계정 접근 권한을 확인해 주세요.',fish_model:'Fish 모델 접근을 확인해 주세요.',fish_rate_limit:'Fish 요청 제한에 도달했어요. 잠시 뒤 명시적으로 다시 시도해 주세요.',fish_connection:'Fish 연결이 끊겼어요.',fish_timeout:'Fish 응답 시간이 초과됐어요.',fish_server:'Fish 서비스에서 오류가 발생했어요.',fish_request:'Fish 요청을 완료하지 못했어요.',audio_timeout:'음성 재생 시간이 초과됐어요.',audio_download_timeout:'음성 다운로드 시간이 초과됐어요.',device_unavailable:'스피커 출력 장치를 확인해 주세요.',tool_unavailable:'음성 재생 도구를 확인해 주세요.'};
 $('provider-readiness').textContent=provider?(providerReasons[provider.code]||provider.code||'음성 상태 확인 전')+(provider.blocked?' · 자동 재시도 중지':''):'음성 provider 상태는 아직 관측하지 않았어요.';
 $('provider-recheck').disabled=!provider?.blocked&&(!cloud?.blocked||cloud.recovery==='restart_required');
 $('gamepad-readiness').textContent=next.usb_gamepad?.enabled?'USB 패드 입력 사용: 표준 매핑의 게임·게임 목록에만 전달해요. 실제 패드 검수는 별도예요.':'USB 패드는 기본으로 꺼져 있어요. GP8 대화 입력은 그대로 사용할 수 있어요.';
}
function stopVideoReview(){
 const player=$('video-review-player');player.pause();
 if(player.hasAttribute('src')){player.removeAttribute('src');player.load();}
 videoReviewId=null;
}
function renderVideo(next,video,screen,phase,elapsed){
 $('video-view').hidden=!screen;$('video-actions').hidden=!screen;
 const review=screen&&['review','video_review'].includes(phase);
 const result=next.video_result||video?.result||(next.video?.video_id?next.video:null);
 const id=next.video_saved_id||result?.video_id;
 const saved=review&&result?.completed===true&&typeof id==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(id);
 const live=screen&&['preparing','countdown','starting','recording'].includes(phase)&&next.camera?.on===true;
 $('video-preview-image').hidden=!live;$('video-review-player').hidden=!saved;
 const player=$('video-review-player');
 if(saved&&videoReviewId!==id){stopVideoReview();videoReviewId=id;player.src='/api/video/'+encodeURIComponent(id);player.load();}
 else if(!saved&&videoReviewId!==null)stopVideoReview();
 if(videoActionPending){
  const receipt=videoActionPending.request_id&&(next.requests||[]).find(item=>item.request_id===videoActionPending.request_id);
  if(videoActionPending.kind==='start'&&(screen||video?.recording===true)
   ||videoActionPending.kind==='stop'&&!['starting','recording'].includes(phase)
   ||receipt&&!['accepted','running'].includes(receipt.status))videoActionPending=false;
 }
 $('video-record-stop').hidden=!screen||!['starting','recording'].includes(phase);$('video-record-stop').disabled=!!videoActionPending;
 for(const name of ['video-play','video-replay'])$(name).hidden=!saved;
 $('video-play').textContent=player.paused?'Play':'Pause';
 $('video-review-download').hidden=!saved;if(saved)$('video-review-download').href='/api/video/'+encodeURIComponent(id);
 const remaining=Number.isFinite(next.video_countdown)?next.video_countdown:next.video_countdown_remaining;
 const seconds=Number.isFinite(remaining)?Math.max(1,Math.min(5,Math.ceil(remaining))):null;
 $('video-countdown').hidden=!(screen&&phase==='countdown'&&seconds!==null);$('video-countdown').textContent=seconds===null?'':String(seconds);
 if(screen){
  const reason=next.video_diagnostic?.code||result?.code||video?.code;
  const errors={video_encoder_unavailable:'The video encoder is not ready.',video_probe_unavailable:'The video verifier is not ready.',
   video_disk_full:'There is not enough space to save this video.',video_empty:'No video frames were saved.',
   video_encoder_failed:'The video could not finish encoding.',video_invalid_container:'The saved video could not be verified.',
   camera_timeout:'The camera stopped responding.',camera_unavailable:'The camera could not be opened.',video_storage_failed:'The video could not be saved.'};
  $('foreground-status').textContent=phase==='failed'?(errors[reason]||'The video could not be completed. No automatic retry.'):
   ({preparing:'Getting the video ready…',countdown:'Ready? Hold still!',starting:'Starting the recording…',recording:'● REC '+elapsed+' · silent · GP8 / Stop to save',
    finalizing:'Finishing your video…',review:result?.interrupted?'Saved interrupted video.':'Video saved.',video_review:'Video saved.'})[phase]||'Getting the video ready…';
  if(next.video_duration_clamped===true&&['preparing','countdown','starting','recording'].includes(phase))
   $('foreground-status').textContent+=' Length limited to '+next.video_duration+' seconds.';
  $('foreground-back').disabled=phase==='finalizing';
 }else $('foreground-back').disabled=false;
}
function renderVolume(next){
 const receipt=volumeRequest&&(next.requests||[]).find(item=>item.request_id===volumeRequest);
 if(receipt&&!['accepted','running'].includes(receipt.status)){
  volumePending=null;volumeRequest=null;
  if(['failed','rejected','unknown'].includes(receipt.status))error('The volume setting could not be applied.');
 }
 const volume=volumePending||next.volume||next.audio_output?.volume;
 if(!volume)return;
 if(Number.isFinite(volume.level)&&volume.level>=0&&volume.level<=1){
  if(document.activeElement!==$('master-volume'))$('master-volume').value=String(volume.level);
  if(document.activeElement!==$('music-volume'))$('music-volume').value=String(volume.level);
  $('music-volume-value').textContent=Math.round(volume.level*100)+'%';
  $('master-volume-value').textContent=Math.round(volume.level*100)+'%';
  $('face-volume-value').textContent=Math.round(volume.level*100)+'%';
  $('face-volume-down').disabled=volume.level<=0;$('face-volume-up').disabled=volume.level>=1;
 }
 if(typeof volume.muted==='boolean'){$('master-mute').checked=volume.muted;$('music-mute').checked=volume.muted;
  $('face-volume-mute').textContent=volume.muted?'Unmute':'Mute';$('face-volume-mute').setAttribute('aria-pressed',String(volume.muted));}
}
function renderArms(next){
 const arms=next.system_diagnostics?.arms||{},motion=next.motion||{},usage=next.arm_usage||{};
 const titles={wave:'손 흔들기',raise_left_arm:'왼팔 들기',raise_right_arm:'오른팔 들기',both_up:'양팔 들기',both_down:'양팔 내리기',celebrate:'만세',shrug:'어깨 으쓱'};
 const names=Object.keys(titles);
 const blocked=motion.blocked_reasons||arms.blocked_reasons||[];
 const reason=usage.requires_first_test?'arm_first_test_required':usage.enabled===false?'arm_runtime_off':arms.action_status?.code||blocked[0]||motion.reason_code||arms.reason_code||arms.preflight?.code||'arm_state_unverified';
 const labels={arm_runtime_off:'팔 동작이 현재 실행에서 꺼져 있어요.',real_disabled:'실물 팔 동작이 현재 실행에서 꺼져 있어요.',
  arm_first_test_required:'처음 한 번은 파이에서 입회 검사를 완료해 주세요. 동작 버튼은 그대로 표시돼요.',
  operator_conditions_missing:'현재 감독 조건이 준비되지 않았어요.',arm_operator_required:'현재 감독 조건이 준비되지 않았어요.',
  arm_firmware_mismatch:'검토된 보드 SHA와 일치하지 않아요.',arm_controller_disabled:'보드 원격 동작이 비활성 상태예요.',arm_board_disabled:'보드 원격 동작이 비활성 상태예요.',
  arm_port_busy:'팔 포트를 다른 프로그램이 사용하고 있어요.',arm_link_unknown:'팔 통신 상태를 확인할 수 없어요.',
  arm_stop_unverified:'팔 중지 응답을 확인하지 못했어요.',arm_operator_condition_missing:'현재 감독 조건이 준비되지 않았어요.',arm_transport_error:'팔 통신 상태를 확인할 수 없어요.',
  hello_required:'감독 실행이 설정됐어요. 첫 명령에서 기존 B1 연결 응답을 확인해요.',position_unverified:'B1 연결 응답을 확인했어요. 실제 위치는 미확인이에요.',
  arm_closed:'팔 연결이 닫혔어요.',arm_stopped:'팔이 중지됐어요. 자동으로 다시 활성화하지 않아요.',motion_stopped:'팔이 중지됐어요. 자동으로 다시 활성화하지 않아요.'};
 const allowed=['mock','explicit_supervised','explicit_commissioned'].includes(next.capabilities?.arms);
 const ready=typeof arms.action_status?.request_allowed==='boolean'?arms.action_status.request_allowed:
  allowed&&usage.enabled!==false&&!usage.requires_first_test&&arms.preflight?.status!=='BLOCKED'&&motion.closed!==true&&motion.stopped!==true;
 const armState=usage.state||(!allowed||usage.enabled===false?'off':usage.requires_first_test?'setup_required':motion.stopped?'stopped':motion.transport?.status==='NOT_CONNECTED'?'connection_unverified':ready?'ready':'connection_problem');
 const prepareAvailable=usage.prepare_available===true||(next.capabilities?.arms==='explicit_supervised'&&motion.real_enabled===true&&motion.closed!==true&&motion.active!==true&&motion.prepare_requires_explicit_user_action===true);
 const prepareCode=usage.prepare_code||motion.prepare_code;
 $('arms-status').dataset.state=armState;
 $('arms-prepare').disabled=!prepareAvailable;
 $('arms-prepare').title=prepareCode==='arm_prepare_firmware_required'?'파이에서 검토된 복귀 프로토콜 설치가 필요해요.':'';
 $('arms-status').textContent=ready?'팔 요청을 보낼 수 있어요. 실제 움직임은 직접 확인해 주세요.':
  (labels[reason]||'팔을 사용할 준비가 아직 안 됐어요. 준비 상태를 확인해 주세요.')+
  (reason==='arm_runtime_off'?' 아래에서 팔 사용을 켜 주세요.':'');
 const stateTitle={off:'꺼짐',setup_required:'첫 설정 필요',ready:'사용 가능',stopped:'중지됨',connection_problem:'연결 문제',connection_unverified:'연결 확인 필요'}[armState];
 if(stateTitle)$('arms-status').textContent=stateTitle+' · '+$('arms-status').textContent;
 if(prepareCode==='arm_prepare_firmware_required')$('arms-status').textContent+=' 파이에서 검토된 복귀 프로토콜 설치가 필요해요.';
 else if(armState==='stopped'&&prepareAvailable)$('arms-status').textContent+=' 현재 상태를 확인하려면 팔 다시 준비를 눌러 주세요. 준비만으로 움직이지 않아요.';
 if(motion.performance_supported===false)$('arms-status').textContent+=' 대화 연출은 파이에서 새 발화 연출 후보 검수가 필요해요. 메뉴 동작은 별도로 사용할 수 있어요.';
 if(!armPreferencePending){$('arms-enabled').checked=usage.enabled===true;$('arms-conversation').checked=usage.conversation_gestures===true;}
 $('arms-enabled').disabled=armPreferencePending||typeof usage.enabled!=='boolean';$('arms-conversation').disabled=armPreferencePending||typeof usage.conversation_gestures!=='boolean';
 const key=JSON.stringify([names,ready]);
 if(key!==armCatalogKey){
  armCatalogKey=key;$('arms-gestures').replaceChildren();
  for(const name of names){const button=document.createElement('button');button.textContent=titles[name]||name;button.dataset.gesture=name;button.disabled=!ready;
   button.onclick=()=>{button.disabled=true;intent('arm_gesture',name).then(result=>{armRequest=result.request_id;renderArmReceipt(result);})
    .catch(()=>{$('arms-result').textContent='팔 요청을 완료하지 못했어요. 연결·감독 상태를 확인해 주세요.';}).finally(()=>{armCatalogKey=null;});};$('arms-gestures').append(button);}
 }
 $('arms-stop').disabled=false;
 const receipt=armRequest&&(next.requests||[]).find(item=>item.request_id===armRequest);
 if(receipt)renderArmReceipt(receipt);
}
function renderArmReceipt(result){
 const failed=['rejected','failed','unknown'].includes(result.status);
 $('arms-result').textContent=failed?'팔 요청을 완료하지 못했어요. 현재 연결·중지 상태를 준비 상태에서 확인해 주세요.':
  result.status==='cancelled'?'팔 요청이 취소됐어요. 실제 움직임과 중지 상태는 직접 확인해 주세요.':
  result.code==='arm_stop_requested'?'팔 중지를 요청했어요. 실제 움직임과 전원은 직접 확인해 주세요.':
  ['arm_prepared','arm_prepared_position_unverified','arm_ready_position_unverified'].includes(result.code)?'팔 준비 응답을 확인했어요. 동작은 별도로 요청해 주세요.':
  result.code==='ack_position_unverified'?'팔 통신 응답을 확인했어요. 실제 움직임은 직접 확인해 주세요.':
  result.simulated===true&&result.status==='simulated'?'소프트웨어에서 요청을 확인했어요. 실물 팔 동작은 확인하지 않았어요.':
  '팔 요청을 받았어요. 통신 응답을 확인하고 있어요.';
}
function mediaObservation(event,error_code=null){
 if(closed)return Promise.resolve();
 return control('media_observation',{event,generation:featureState?.media?.playback_generation||0,
  hidden:document.hidden,focused:document.hasFocus(),error_code}).catch(()=>{});
}
function mediaCommand(action,source,value=null){
 if(action==='resume'||action==='replay'){
  mediaVideoFailure=null;mediaSyncRequired=true;
  if($('notice').dataset.musicError){noticeUntil=0;noticeText='';clearTimeout(musicErrorTimer);delete $('notice').dataset.musicError;$('notice').hidden=true;}
 }
 return control('media_command',{action,source,generation:featureState?.media?.playback_generation||0,value});
}
function videoErrorCode(error){return ({1:'aborted',2:'network',3:'decode',4:'unsupported'})[error?.code]||'unknown';}
function stopMediaVideo(){
 const player=$('music-video');mediaVideoKey=null;mediaVideoFailure=null;mediaPlayPending=null;player.pause();
 if(player.hasAttribute('src')){player.removeAttribute('src');player.load();}
}
function hideMusicControls(){
 if($('music-toolbar').contains(document.activeElement)){mediaControlsTimer=setTimeout(hideMusicControls,1200);return;}
 $('music-surface').dataset.controlsVisible='false';
}
function showMusicControls(){
 if(currentMode!=='music'||$('music-surface').hidden)return;
 $('music-surface').dataset.controlsVisible='true';clearTimeout(mediaControlsTimer);
 mediaControlsTimer=setTimeout(hideMusicControls,2600);
}
function stopMusic(source='touch_stop'){
 if(mediaStopping)return Promise.resolve();
 mediaStopping=true;clearTimeout(mediaHoldTimer);clearTimeout(mediaControlsTimer);
 $('music-surface').dataset.controlsVisible='false';
 clearCaption();stopMediaVideo();
 for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();
 return mediaCommand('stop',source).catch(()=>{mediaStopping=false;error('음악 종료를 확인해 주세요.');});
}
function renderMusic(next){
 const media=next.media||{},active=next.mode==='music',live=next.capabilities?.music==='explicit_supervised';
 const catalog=media.catalog||[];
 $('music-readiness').textContent=catalog.length?(live?'검증된 로컬 음악을 선택한 출력 장치에서 재생해요.':next.simulation?'합성 자료로 앱 연결을 검사해요. 실제 음원·출력은 별도 검수가 필요해요.':'음악을 가져왔어요. 재생에는 현재 실행의 명시적 허가가 필요해요.'):'사용자 음악을 private 목록에 가져오면 여기서 선택할 수 있어요.';
 const key=JSON.stringify([catalog,next.capabilities?.music]);
 if(key!==musicCatalogKey){musicCatalogKey=key;$('music-list').replaceChildren();for(const item of catalog){
  const row=document.createElement('section');row.className='music-item';row.dataset.songId=item.id;
  const title=document.createElement('p');title.textContent=item.title;row.append(title);
  const modes=Array.isArray(item.variants)?item.variants:Object.keys(item.variants||{});
  for(const mode of modes.length?modes:[null]){
   if(mode!==null&&!['face','video'].includes(mode))continue;
   const button=document.createElement('button');button.dataset.mediaMode=mode||'face';
   button.textContent=modes.length>1?(mode==='video'?'영상으로 보기':'BMO가 부르기'):item.title;
   button.disabled=item.readiness!=='READY'||!['mock','explicit_supervised'].includes(next.capabilities?.music);
   button.onclick=()=>intent('play_song',item.id,mode).then(()=>{if(modes.length>1)$('music-dialog').close();}).catch(()=>error('음악을 시작하지 못했어요.'));
   row.append(button);
  }
  $('music-list').append(row);
 }}
 for(const [id,capability] of [['music-pause','pause'],['music-resume','resume']])$(id).disabled=!active||!media.capabilities?.includes(capability);
 for(const id of ['music-next','music-stop'])$(id).disabled=!active;
 const wasActive=!$('music-surface').hidden;
 $('music-surface').hidden=!active;document.body.dataset.musicScreen=String(active);
 if(!active){
  mediaStopping=false;clearTimeout(mediaControlsTimer);$('music-surface').dataset.controlsVisible='false';
  clearTimeout(musicErrorTimer);delete $('notice').dataset.musicError;
 }else if(!wasActive){
  mediaStopping=false;$('music-surface').dataset.controlsVisible='false';
 }
 const variant=media.display_mode||media.variant||'face',video=active&&variant==='video'&&next.audio_owner==='music';
 $('music-video-view').hidden=!video;
 const item=catalog.find(item=>item.id===media.active_id);
 const position=Number.isFinite(media.position_seconds)?media.position_seconds:Number.isFinite(media.position)?media.position:0;
 const duration=Number.isFinite(media.duration_seconds)?media.duration_seconds:Number.isFinite(media.duration)?media.duration:0;
 const format=value=>Math.floor(Math.max(0,value)/60)+':'+String(Math.floor(Math.max(0,value)%60)).padStart(2,'0');
 $('music-current').textContent=active?(item?.title||media.active_id||'음악')+' · '+(variant==='video'?'영상':'BMO가 부르기')+' · '+format(position)+' / '+format(duration):'';
 $('music-toggle').textContent=media.state==='playing'?'Pause':'Play';
 $('music-toggle').disabled=!active||!media.capabilities?.includes(media.state==='playing'?'pause':'resume');
 $('music-seek').max=String(Math.max(.01,duration));
 if(!mediaSeekPending&&document.activeElement!==$('music-seek'))$('music-seek').value=String(Math.min(duration,Math.max(0,position)));
 $('music-seek').disabled=!active||!media.capabilities?.includes('seek')||duration<=0;
 $('music-replay').disabled=!active||!media.capabilities?.includes('replay');
 const player=$('music-video');player.muted=true;player.volume=0;
 const url=typeof media.video_url==='string'&&media.video_url.startsWith('/api/')?media.video_url:null;
 const identity=video&&url?JSON.stringify([media.active_id,url,media.playback_generation]):null;
 if(!identity){if(mediaVideoKey!==null||player.hasAttribute('src'))stopMediaVideo();return;}
 if(identity!==mediaVideoKey){
  // A seek/replay changes the authoritative playback generation. Reuse the
  // same decoder source where possible; the Python sample clock owns time.
  const changedSource=player.getAttribute('src')!==url;mediaVideoKey=identity;mediaVideoFailure=null;mediaSyncRequired=true;
  if(changedSource){player.pause();player.src=url;player.load();}
 }
 if(mediaVideoFailure===mediaVideoKey||document.hidden)return;
 if(player.readyState>=1&&(mediaSyncRequired||Math.abs(player.currentTime-position)>.12)){
  try{player.currentTime=Math.min(Math.max(0,position),Number.isFinite(player.duration)?Math.max(0,player.duration-.001):position);}catch(_){}mediaSyncRequired=false;
 }
 if(media.state==='playing'&&media.output_started!==false){
  if(player.ended&&duration>0&&position>=duration-.2)return;
  if(player.paused&&mediaPlayPending?.key!==mediaVideoKey){
   const playbackKey=mediaVideoKey,attempt={key:playbackKey};mediaPlayPending=attempt;
   player.play().catch(failure=>{if(!closed&&mediaPlayPending===attempt&&playbackKey===mediaVideoKey&&mediaVideoFailure!==playbackKey){
    const code=failure?.name==='AbortError'?'aborted':failure?.name==='NotSupportedError'?'unsupported':'play_rejected';
    mediaObservation('video_play_rejected',code);
    // A background rendering interruption does not own local PCM playback.
    // Visibility restoration requests a single fresh decoder attempt.
    if(document.hidden){mediaSyncRequired=true;return;}
    mediaVideoFailure=playbackKey;mediaCommand('pause','video_play_rejected').catch(()=>{});
    error('음악 영상을 재생하지 못했어요. 음악을 멈추고 파일·codec 상태를 확인해 주세요.');}})
    .finally(()=>{if(mediaPlayPending===attempt)mediaPlayPending=null;});
  }
 }else{
  // pause() can reject play() without changing the owner's seek generation.
  // Retire that attempt before pausing so it cannot poison a later resume.
  mediaPlayPending=null;player.pause();
 }
}

let batchPreviewRunning=false,batchPreviewAbort=false,batchPreviewCompleted=false,batchPreviewArchive=null,batchEventNamesKey=null;
async function auditionVoiceBatch(){
 if(batchPreviewRunning)return;
 const pack=featureState?.voice_pack,batch=pack?.batch;
 if(!batch?.ready||batch.batch_user_accepted||featureState?.mode!=='conversation'||featureState.conversation_busy
  ||featureState.audio_owner!==null||featureState.volume?.effective_level===0)return;
 const candidates=new Map((pack.inventory||[]).map(item=>[item.id,item]));
 const samples=(batch.sample_ids||[]).filter(id=>candidates.get(id)?.variants?.raw?.sha256);
 if(samples.length<5)return;
 batchPreviewRunning=true;batchPreviewAbort=false;batchPreviewCompleted=false;$('voice-batch-preview-stop').disabled=false;
 try{
  for(let index=0;index<samples.length;index++){
   if(batchPreviewAbort)break;
   const id=samples[index],sha256=candidates.get(id).variants.raw.sha256;
   $('voice-result').textContent='대표 클립 미리듣기 '+(index+1)+'/'+samples.length+' · '+id;
   const request=await control('voice_preview',{candidate_id:id,sha256});
   if(typeof request.request_id!=='string')throw Error('preview_request_unverified');
   let finished=false;
   for(let tick=0;tick<240;tick++){
    if(batchPreviewAbort)break;
    await new Promise(resolve=>setTimeout(resolve,150));
    const response=await fetch('/api/features',{cache:'no-store'});
    if(!response.ok)throw Error('preview_snapshot_unavailable');
    const now=await response.json();
    if(now.mode!=='conversation')throw Error('preview_interrupted');
    // Only this admitted review's terminal receipt proves completion.
    // Historic played flags or another owner's busy/idle edge are insufficient.
    const receipt=(now.requests||[]).find(item=>item.request_id===request.request_id);
    if(!receipt||['accepted','running'].includes(receipt.status))continue;
    if(receipt.code!=='voice_review_completed'||receipt.completed!==true
      ||receipt.completion!=='review_playback')throw Error('preview_interrupted');
    finished=true;break;
   }
   if(batchPreviewAbort)break;
   if(!finished)throw Error('preview_completion_unverified');
  }
  if(!batchPreviewAbort)batchPreviewCompleted=true;
  $('voice-result').textContent=batchPreviewAbort?'대표 미리듣기를 중단했어요. 청취 완료 기록을 만들지 않았어요.':
   '대표 클립 연속 재생이 끝났어요. 실제 개별 청취 승인은 별도이며, 묶음 사용 승인은 아래에서 결정하세요.';
 }catch(_){
  $('voice-result').textContent=batchPreviewAbort?'대표 미리듣기를 중단했어요.':
   '일부 미리듣기가 완료되지 않았어요. 현재 오디오 출력과 대화 상태를 확인해 주세요.';
 }finally{
  batchPreviewRunning=false;batchPreviewAbort=false;$('voice-batch-preview-stop').disabled=true;
 }
}
function renderVoiceBatch(next,bank){
 const batch=bank?.batch||{},ready=bank?.version===10&&batch.ready===true;
 if(bank?.archive_sha256!==batchPreviewArchive){batchPreviewArchive=bank?.archive_sha256||null;batchPreviewCompleted=false;}
 $('voice-batch').hidden=!ready;
 if(!ready)return;
 const mapping=batch.event_mapping||{},disabled=batch.disabled_events||[],accepted=batch.batch_user_accepted===true;
 const fullAccepted=bank.full_user_accepted===true;
 $('voice-full-summary').textContent='Verified saved voices: '+(bank.candidates||0)+' · Full use: '+(fullAccepted?'ON':'OFF')+
  ' · Individually heard: '+(bank.listened||0)+' · Individually approved: '+(bank.approved||0);
 $('voice-full-enable').disabled=fullAccepted||!$('voice-full-consent').checked||batchPreviewRunning
  ||next.mode!=='conversation'||next.conversation_busy||next.audio_owner!==null;
 $('voice-batch-summary').textContent=fullAccepted?'All saved voices enabled for matching situations · '+(bank.batch_released||0)+' clips · Individual listening and approval records preserved.':'초기 '+(batch.batch_selected||0)+'개 + 음악 자연종료 '+(batch.extra_release_ids?.length||0)+
  '개 별도 포함 · 대표 미리 재생 시작 '+(batch.preview_played||0)+'/'+(batch.sample_ids?.length||0)+
  ' · 활성화 '+(accepted?'ON':'OFF')+' · 개별 청취 승인 '+(bank.approved||0)+' · 실제 개별 청취 기록 '+(bank.listened||0)+
  ' · 현재 묶음 재생권한 '+(bank.batch_released||0)+'개';
 $('voice-batch-preview').disabled=batchPreviewRunning||accepted||next.mode!=='conversation'
  ||next.conversation_busy||next.audio_owner!==null||next.volume?.effective_level===0;
 $('voice-batch-preview-stop').disabled=!batchPreviewRunning;
 $('voice-batch-enable').disabled=accepted||batchPreviewRunning||!batchPreviewCompleted||!$('voice-batch-consent').checked
  ||(batch.preview_played||0)<(batch.preview_required||5)||next.mode!=='conversation'||next.conversation_busy||next.audio_owner!==null;
 $('voice-batch-disable').disabled=!accepted;
 const key=JSON.stringify(mapping);
 if(batchEventNamesKey!==key){
  batchEventNamesKey=key;$('voice-batch-event-name').replaceChildren();
  for(const [event,id] of Object.entries(mapping)){
   const option=document.createElement('option');option.value=event;option.textContent=event+' → '+id;
   $('voice-batch-event-name').append(option);
  }
 }
 const event=$('voice-batch-event-name').value;
 $('voice-batch-event-disable').disabled=!accepted||!event||disabled.includes(event);
 $('voice-batch-event-enable').disabled=!accepted||!event||!disabled.includes(event);
}

function renderVoiceBank(next){
 const bank=next.voice_pack||next.voice_bank;
 if(!bank){$('voice-readiness').textContent='private 음성팩 전달과 개별 청취 승인 상태를 아직 확인하지 못했어요.';return;}
 const items=bank.inventory||bank.items||bank.clips||[];
 renderVoiceBatch(next,bank);
 $('voice-readiness').textContent=bank.status==='ASSET_PENDING'?'private 음성팩 전달·가져오기를 기다려요. 가져온 자산은 아직 없어요.':
  (bank.code||bank.status||'VOICE_PACK_UNVERIFIED')+' · 등록 '+items.length+'개 · 초기 우선 '+(bank.initial_selection_count||items.filter(item=>item.initial_priority===true||item.priority===true).length)+'개 · 개별 승인 '+(bank.approved||0)+'개 · 묶음 재생허용 '+(bank.batch_released||0)+'개';
 for(const button of $('voice-list').querySelectorAll('button[data-voice-event]'))
  button.disabled=button.dataset.assetReady!=='true'||next.mode!=='conversation'||next.conversation_busy||next.audio_owner!==null
   ||next.volume?.effective_level===0;
 for(const button of $('voice-list').querySelectorAll('button[data-voice-review]'))button.disabled=next.mode!=='conversation'||next.conversation_busy||next.audio_owner!==null||next.volume?.effective_level===0;
 const key=JSON.stringify([items,bank.selected_events]);if(key===voiceCatalogKey)return;voiceCatalogKey=key;$('voice-list').replaceChildren();
 for(const item of items.slice(0,224).sort((a,b)=>Number(b.initial_priority===true||b.priority===true)-Number(a.initial_priority===true||a.priority===true))){
  if(typeof item.id!=='string')continue;
  const row=document.createElement('section');row.className='voice-candidate';row.dataset.candidate=item.id;
  const title=document.createElement('p');title.textContent=(item.text||item.title||item.id)+' · '+item.id+' · '+(item.source_model||'출처 미확인');row.append(title);
  const status=document.createElement('p');status.className='voice-status';status.textContent='등록됨 · '+(item.initial_priority===true||item.priority===true?'초기 연결 우선 · ':'')+'청취 '+(item.listened===true?'기록됨':'미기록')+' · 개별 승인 '+(item.approved===true?'있음':'없음')+' · 묶음 허용 '+(item.batch_released===true?'ON':'OFF')+' · 선택 '+(item.selected_events?.length||0)+'개 · 재생 '+(item.played===true?'시작 기록됨':Number.isInteger(item.play_count)&&item.play_count>0?item.play_count+'회':'미기록');row.append(status);
  const events=Array.isArray(item.events)?item.events:bank.event_names||[];
  if(events.length){
   const label=document.createElement('label');label.textContent='연결할 이벤트 ';
   const select=document.createElement('select');select.setAttribute('aria-label',item.id+' event');
   for(const event of events){if(typeof event!=='string')continue;const option=document.createElement('option');option.value=event;option.textContent=event;select.append(option);}
   if(Array.isArray(item.selected_events)&&item.selected_events.length)select.value=item.selected_events[0];
   const assign=document.createElement('button');assign.textContent='선택';assign.onclick=()=>{
    assign.disabled=true;control('voice_select',{event:select.value,candidate_id:item.id})
     .then(()=>{$('voice-result').textContent='이벤트 후보를 선택했어요. 개별 또는 묶음 사용 승인 후 재생할 수 있어요.';})
     .catch(()=>{$('voice-result').textContent='이 자산은 해당 이벤트에 연결할 수 없어요.';}).finally(()=>{assign.disabled=false;});};
   const play=document.createElement('button');play.textContent='승인된 선택 음성 재생';play.dataset.voiceEvent=select.value;
   const updatePlay=()=>{
    const ready=(item.approved===true||item.batch_released===true)&&Array.isArray(item.selected_events)&&item.selected_events.includes(select.value);
    play.dataset.voiceEvent=select.value;play.dataset.assetReady=String(ready);
    play.disabled=!ready||featureState?.mode!=='conversation'||featureState?.conversation_busy||featureState?.audio_owner!==null
     ||featureState?.volume?.effective_level===0;
   };
   select.onchange=updatePlay;updatePlay();
   play.onclick=()=>{if(play.disabled)return;play.disabled=true;control('voice_event',select.value)
    .then(()=>{$('voice-result').textContent='승인된 선택 음성의 재생을 요청했어요. 새 음성은 생성하지 않아요.';})
    .catch(()=>{$('voice-result').textContent='이벤트 선택·재생 승인·출력 상태를 확인해 주세요.';});};
   label.append(select);row.append(label,assign,play);
  }
  for(const [name,variant] of Object.entries(item.variants||{})){
   if(typeof variant.sha256!=='string'||!/^[a-f0-9]{64}$/.test(variant.sha256))continue;
   const line=document.createElement('div');line.className='voice-variant';line.dataset.variant=name;
   const info=document.createElement('p');const duration=Number.isFinite(variant.duration_seconds)?variant.duration_seconds.toFixed(2)+'s':'길이 미확인';
   info.textContent=name+' · baked gain '+variant.baked_gain+' · '+duration+' · SHA '+variant.sha256;line.append(info);
   const preview=document.createElement('button');preview.textContent='이 SHA 미리 듣기';preview.dataset.voiceReview=item.id;
   preview.disabled=next.mode!=='conversation'||next.conversation_busy||next.audio_owner!==null||next.volume?.effective_level===0;
   preview.onclick=()=>{preview.disabled=true;control('voice_preview',{candidate_id:item.id,sha256:variant.sha256})
    .then(()=>{$('voice-result').textContent='이 SHA의 검토 재생을 요청했어요. 청취 승인과 이벤트 선택은 따로 기록해 주세요.';})
    .catch(()=>{$('voice-result').textContent='검토 재생을 시작하지 못했어요. 자산·출력 상태를 확인해 주세요.';});};line.append(preview);
   if(variant.approved===true){const approved=document.createElement('span');approved.textContent='이 SHA 개별 청취 승인됨';line.append(approved);}
   else{
    const label=document.createElement('label'),heard=document.createElement('input');heard.type='checkbox';label.append(heard,document.createTextNode('별도로 직접 들은 이 SHA의 음성을 승인해요.'));
    const approve=document.createElement('button');approve.textContent='이 SHA 승인';approve.disabled=true;heard.onchange=()=>{approve.disabled=!heard.checked;};
    approve.onclick=()=>{if(!heard.checked)return;approve.disabled=true;control('voice_approve',{candidate_id:item.id,sha256:variant.sha256,listening_approved:true})
     .then(()=>{$('voice-result').textContent='선택한 SHA의 개별 청취 승인을 요청했어요.';})
     .catch(()=>{$('voice-result').textContent='SHA와 자산 상태를 확인해 주세요. 승인하지 않았어요.';approve.disabled=!heard.checked;});};line.append(label,approve);
   }
   row.append(line);
  }
  $('voice-list').append(row);
 }
}
function renderCaption(){
 const snapshot=window.BMOIntegration?.state,caption=snapshot?.caption;
 if(subtitlePending){
  if(featureState?.subtitles?.mode!==subtitlePending.mode||caption?.mode!==subtitlePending.mode){hideCaption();return;}
  subtitlePending=null;
 }
 const visible=snapshot?.playback==='started'&&snapshot?.phase==='speaking'&&snapshot?.utterance_id!==suppressedCaption
  &&['en','ko'].includes(caption?.mode)&&['en','ko'].includes(caption?.language)
  &&typeof caption?.text==='string'&&caption.text.length>0&&caption.text.length<=4096;
 $('subtitles').textContent=visible?caption.text:'';$('subtitles').hidden=!visible;
 if(visible)$('subtitles').lang=caption.language;
}
function photoPreviewVisible(image){
 const bounds=image.getBoundingClientRect();
 return !document.hidden&&image.isConnected&&!image.closest('[hidden]')
  &&!Array.from(document.querySelectorAll('dialog[open]')).some(dialog=>!dialog.contains(image))
  &&bounds.width>0&&bounds.height>0&&bounds.bottom>0&&bounds.right>0&&bounds.top<innerHeight&&bounds.left<innerWidth
  &&getComputedStyle(image).visibility!=='hidden';
}
function previewEvent(kind,id){
 const now=performance.now(),samples=previewSamples[kind];
 previewEvidence[kind]++;previewEvidence['last_'+kind+'_id']=id;samples.push(now);
 while(samples.length>101||samples[0]<now-5000)samples.shift();
 previewSnapshot();
}
function previewSnapshot(){
 const now=performance.now();
 for(const kind of ['received','decoded','displayed']){
  const samples=previewSamples[kind];while(samples.length&&samples[0]<now-5000)samples.shift();
  previewEvidence[kind+'_fps']=samples.length>1?Math.round((samples.length-1)*10000/(samples.at(-1)-samples[0]))/10:null;
 }
 return {...previewEvidence};
}
function stopPreview(suppress=false){
 if(suppress){
  if(previewRun)previewSuppressedKey=previewRun.key;
  else if(featureState?.mode==='photo'&&typeof featureState.photo_request_id==='string')
   previewSuppressedKey=JSON.stringify(['photo',featureState.mode_generation,featureState.photo_request_id]);
  else if(featureState?.mode==='video'&&typeof featureState.video_request_id==='string')
   previewSuppressedKey=JSON.stringify(['video',featureState.mode_generation,featureState.video_request_id]);
 }
 previewRun=null;previewEvidence.active=false;photoPreviewContext=null;videoPreviewContext=null;
 clearTimeout(previewTimer);previewTimer=null;previewAbort?.abort();
 if(previewDraw!==null)cancelAnimationFrame(previewDraw);previewDraw=null;
 if(previewBitmap){previewBitmap.bitmap.close();previewBitmap=null;}
 for(const id of ['photo-preview-image','video-preview-image','camera-image']){
  const canvas=$(id);delete canvas.dataset.frameId;canvas.getContext('2d')?.clearRect(0,0,canvas.width,canvas.height);
 }
}
function syncPreview(next){
 const photo=next.mode==='photo'&&['preparing','preparing_voice','countdown_audio_start','countdown','focusing','focus_review'].includes(next.photo_phase)
  &&next.camera?.on===true&&typeof next.photo_request_id==='string';
 const video=next.mode==='video'&&['preparing','countdown','starting','recording'].includes(next.video_phase)
  &&next.camera?.on===true&&typeof next.video_request_id==='string';
 const panel=!photo&&!video&&!['photo','video'].includes(next.mode)&&$('camera-dialog').open&&next.camera?.on===true;
 if(!photo&&!video&&!panel){stopPreview();if(!['photo','video'].includes(next.mode))previewSuppressedKey=null;return;}
 const canvas=$(photo?'photo-preview-image':video?'video-preview-image':'camera-image');
 const key=JSON.stringify([photo?'photo':video?'video':'panel',next.mode_generation,photo?next.photo_request_id:video?next.video_request_id:null]);
 if(previewSuppressedKey===key)return;
 previewSuppressedKey=null;
 if(!previewRun||previewRun.key!==key){
  stopPreview();previewRun={key,serial:++previewSerial,canvas,after:''};previewEvidence.active=true;
  for(const kind of ['received','decoded','displayed'])previewSamples[kind].length=0;
  if(!previewBusy)previewTimer=setTimeout(fetchPreview,0);
 }
 if(photo&&typeof next.photo_preview_token==='string'){
  const ackKey=JSON.stringify([next.mode_generation,next.photo_request_id,next.photo_preview_token]);
  if(photoPreviewContext?.key!==ackKey)photoPreviewContext={key:ackKey,generation:next.mode_generation,
   request_id:next.photo_request_id,preview_token:next.photo_preview_token};
 }else photoPreviewContext=null;
 if(video&&typeof next.video_preview_token==='string'){
  const ackKey=JSON.stringify([next.mode_generation,next.video_request_id,next.video_preview_token]);
  if(videoPreviewContext?.key!==ackKey)videoPreviewContext={key:ackKey,generation:next.mode_generation,
   request_id:next.video_request_id,preview_token:next.video_preview_token};
 }else videoPreviewContext=null;
 drawPreview();photoPreviewDisplayed();videoPreviewDisplayed();
}
async function fetchPreview(){
 previewTimer=null;const run=previewRun;if(!run||closed||document.hidden||previewBusy)return;
 const began=performance.now(),abort=new AbortController();previewAbort=abort;previewBusy=true;
 previewEvidence.pending=1;previewEvidence.max_pending=Math.max(previewEvidence.max_pending,1);previewEvidence.requests++;
 const timeout=setTimeout(()=>abort.abort(),1200);
 let bitmap=null;
 try{
  const response=await fetch('/api/preview/frame?after='+encodeURIComponent(run.after),{cache:'no-store',signal:abort.signal});
  if(previewRun!==run||closed||document.hidden)return;
  if(response.status===204)return;
  if(!response.ok)throw Error('Preview unavailable');
  const id=response.headers.get('X-BMO-Frame-Id'),ageHeader=response.headers.get('X-BMO-Frame-Age-Ms'),
   capturedHeader=response.headers.get('X-BMO-Captured-At'),age=Number(ageHeader),captured=Number(capturedHeader);
  if(!id||!/^[A-Za-z0-9_.:-]{1,96}$/.test(id)||ageHeader===null||capturedHeader===null
   ||!Number.isFinite(age)||age<0||!Number.isFinite(captured))throw Error('Invalid preview metadata');
  const blob=await response.blob();
  if(previewRun!==run||closed||document.hidden)return;
  if(!['image/jpeg','image/png'].includes(blob.type)||blob.size===0||blob.size>4*1024*1024)throw Error('Invalid preview frame');
  if(id===run.after||id===previewEvidence.last_received_id){run.after=id;previewEvidence.duplicates++;return;}
  const previous=/^(.*):(\d+)$/.exec(run.after),current=/^(.*):(\d+)$/.exec(id);
  if(previous&&current&&previous[1]===current[1]){
   const delta=Number(current[2])-Number(previous[2]);
   if(Number.isSafeInteger(delta)&&delta<=0){previewEvidence.duplicates++;return;}
   if(Number.isSafeInteger(delta)&&delta>1)previewEvidence.dropped+=delta-1;
  }
  run.after=id;previewEvent('received',id);previewEvidence.last_frame_age_ms=age;previewEvidence.last_captured_at=captured;
  const receivedAt=performance.now();bitmap=await createImageBitmap(blob);
  if(previewRun!==run||closed||document.hidden)return;
  if(!bitmap.width||!bitmap.height||bitmap.width*bitmap.height>2073600)throw Error('Invalid preview dimensions');
  previewEvent('decoded',id);
  if(previewBitmap){if(previewBitmap.id!==previewEvidence.last_displayed_id)previewEvidence.not_displayed++;previewBitmap.bitmap.close();}
  previewBitmap={bitmap,id,age,receivedAt,requestBegan:began,run};bitmap=null;drawPreview();
 }catch(error){if(!abort.signal.aborted&&previewRun===run)previewEvidence.errors++;}
 finally{
  bitmap?.close();clearTimeout(timeout);if(previewAbort===abort)previewAbort=null;
  previewBusy=false;previewEvidence.pending=0;
  if(previewRun&&!closed&&!document.hidden)previewTimer=setTimeout(fetchPreview,Math.max(0,50-(performance.now()-began)));
 }
}
function drawPreview(){
 const current=previewBitmap,run=previewRun;
 if(!current||current.run!==run||previewDraw!==null||current.id===run.canvas.dataset.frameId||!photoPreviewVisible(run.canvas))return;
 previewDraw=requestAnimationFrame(()=>{
  previewDraw=null;
  if(closed||previewBitmap!==current||previewRun!==run||!photoPreviewVisible(run.canvas))return;
  const canvas=run.canvas;
  if(canvas.width!==current.bitmap.width||canvas.height!==current.bitmap.height){canvas.width=current.bitmap.width;canvas.height=current.bitmap.height;}
  canvas.getContext('2d',{alpha:false}).drawImage(current.bitmap,0,0);
  canvas.dataset.frameId=current.id;previewEvent('displayed',current.id);
  previewEvidence.last_display_age_ms=current.age+performance.now()-current.receivedAt;
  previewEvidence.last_display_age_upper_bound_ms=current.age+performance.now()-current.requestBegan;
  photoPreviewDisplayed();videoPreviewDisplayed();
 });
}
function photoPreviewDisplayed(){
 const current=photoPreviewContext,image=$('photo-preview-image');
 if(!current||photoPreviewAcknowledged===current.key||!image.dataset.frameId||!photoPreviewVisible(image)||featureState?.mode!=='photo'
  ||featureState.photo_phase!=='preparing'||featureState.mode_generation!==current.generation
  ||featureState.photo_request_id!==current.request_id)return;
 // A decoded frame must have a visible paint opportunity before the host's
 // five-second clock starts. Late image loads cannot arm another request.
 requestAnimationFrame(()=>requestAnimationFrame(()=>{
  if(closed||!photoPreviewVisible(image)||!image.dataset.frameId||photoPreviewContext!==current
   ||photoPreviewAcknowledged===current.key||featureState?.photo_phase!=='preparing'
   ||featureState.mode_generation!==current.generation||featureState.photo_request_id!==current.request_id)return;
  photoPreviewAcknowledged=current.key;
  control('photo_preview_ready',{generation:current.generation,request_id:current.request_id,preview_token:current.preview_token})
   .catch(()=>error('카메라 준비를 확인하지 못했어요. 돌아간 뒤 다시 요청해 주세요.'));
 }));
}
function videoPreviewDisplayed(){
 const current=videoPreviewContext,image=$('video-preview-image');
 if(!current||videoPreviewAcknowledged===current.key||!image.dataset.frameId||!photoPreviewVisible(image)||featureState?.mode!=='video'
  ||featureState.video_phase!=='preparing'||featureState.mode_generation!==current.generation
  ||featureState.video_request_id!==current.request_id)return;
 requestAnimationFrame(()=>requestAnimationFrame(()=>{
  if(closed||!photoPreviewVisible(image)||!image.dataset.frameId||videoPreviewContext!==current
   ||videoPreviewAcknowledged===current.key||featureState?.video_phase!=='preparing'
   ||featureState.mode_generation!==current.generation||featureState.video_request_id!==current.request_id)return;
  videoPreviewAcknowledged=current.key;
  control('video_preview_ready',{generation:current.generation,request_id:current.request_id,preview_token:current.preview_token})
   .catch(()=>error('영상 미리보기 준비를 확인하지 못했어요. Back으로 돌아가 주세요.'));
 }));
}
function pollError(){if(performance.now()-lastSuccess>2500){clearCaption();$('splash').hidden=true;error('BMO 연결이 끊겼어요. 창을 닫고 수동으로 다시 실행해 주세요.');}}
async function applyPoll(next){
 if(closed)return;
 lastSuccess=performance.now();
 if(document.hidden){state=next;window.BMODesktop.state=next;return;}
 render(next);await ready();await modes();
}
async function mainPoll(){
 if(closed||controller)return;const current=epoch;
 controller=new AbortController();const active=controller;const abort=setTimeout(()=>active.abort(),1200);
 try{
  const r=await fetch('/api/desktop',{cache:'no-store',signal:active.signal});if(!r.ok)throw Error();
  const next=await r.json();if(current!==epoch||closed)return;await applyPoll(next);
 }catch(_){pollError();}
 finally{clearTimeout(abort);if(controller===active)controller=null;if(!closed&&current===epoch&&!pollWorker)timer=setTimeout(mainPoll,document.hidden?1000:100);}
}
function poll(){
 if(closed)return;
 if(pollWorker){pollWorker.postMessage({action:'poll'});return;}
 mainPoll();
}
function stopPolling(){
 clearTimeout(timer);controller?.abort();epoch++;
 if(pollWorker){pollWorker.postMessage({action:'stop'});pollWorker.terminate();pollWorker=null;}
}
function startPolling(){
 if(typeof Worker==='function')try{
  const worker=new Worker('/desktop_poll.js');pollWorker=worker;pollOwner='dedicated_worker';
  worker.onmessage=event=>{
   if(closed||worker!==pollWorker)return;
   if(event.data?.kind==='error'){pollError();return;}
   if(event.data?.kind!=='snapshot')return;
   worker.postMessage({action:'ack',serial:event.data.serial});
   // A newer snapshot may supersede an unrendered one, never create parallel
   // /features requests or two heartbeat owners.
   const serial=++pollSerial;lastSuccess=performance.now();
   renderSerial=renderSerial.catch(()=>{}).then(()=>{
    if(serial===pollSerial&&!closed)return applyPoll(event.data.value);
   });
  };
  worker.onerror=()=>{
   if(worker!==pollWorker)return;worker.terminate();pollWorker=null;pollOwner='main_fallback';poll();
  };
  worker.postMessage({action:'visibility',hidden:document.hidden});return;
 }catch(_){pollWorker=null;}
 poll();
}
$('notice-menu').onclick=menu;$('resume').onclick=closeToFace;$('exit').onclick=()=>desktop('exit').catch(()=>{error('BMO가 종료된 상태예요. 창을 닫아 주세요.');window.close();});
function ptt(){if(!pttEnabled){error('버튼 녹음은 사용자 검수용 마이크 opt-in이 필요해요.');return;}
 if(pttPending){control('ptt').catch(()=>error('버튼 입력을 확인해 주세요.'));return;}
 pttPending=true;pttRequest=null;const current=++pttEpoch;
 bump('request_sent');control('ptt').then(result=>{bump('request_accepted');if(current===pttEpoch&&pttPending)pttRequest=result.request_id;}).catch(()=>{bump('request_rejected');if(current===pttEpoch){pttPending=false;error('버튼 녹음을 시작하지 못했어요.');}});}
function cancelPTT(){window.BMOIntegration?.cancelEntrance('input_cancelled');enterDown=false;if(pttPending){pttEpoch++;pttPending=false;pttRequest=null;control('cancel').catch(()=>{});}}
$('talk').onclick=()=>{if(pttEnabled)ptt();else control('question','A manually requested question.').catch(()=>error('대화를 시작하지 못했어요.'));closeToFace();};
$('ptt-finish').onclick=()=>{if(pttFinishPending)return;pttFinishPending=true;$('ptt-finish').disabled=true;control('ptt_finish').catch(()=>error('녹음 마침 상태를 확인해 주세요.')).finally(()=>{pttFinishPending=false;});};
$('repeat').onclick=()=>control('repeat').catch(()=>error('다시 들을 답변이 없어요.'));$('stop').onclick=()=>control('stop').catch(()=>error('정지 상태를 확인해 주세요.'));
$('presence-mode').onchange=()=>desktop('presence_mode',$('presence-mode').value).catch(()=>error('대기 음성 설정을 확인해 주세요.'));
$('presence-test').onclick=async()=>{
 if(presenceTestPending||$('presence-test').disabled)return;
 presenceTestPending=true;$('presence-test').disabled=true;
 for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();
 hideFaceVolume();restoreFaceFocus();
 try{
  // Wait for the closed menu lease before asking the existing idle owner.
  await desktop('ui_activity',false);
  const result=await desktop('presence_test');
  if(['blocked','rejected','failed'].includes(result.status))presenceTestMessage(result.code);
 }catch(_){error('혼잣말을 시작하지 못했어요. 준비 상태를 확인해 주세요.');}
 finally{presenceTestPending=false;if(state)renderPresenceTest(state);}
};
$('subtitle-mode').onchange=()=>control('subtitles',$('subtitle-mode').value).catch(()=>error('자막 설정을 확인해 주세요.'));
$('system-dialog').addEventListener('close',()=>{$('turn-diagnostics').textContent='';});
$('wake-off').onclick=()=>desktop('wake_off').catch(()=>error('호출어 종료 상태를 확인해 주세요.'));
$('wake-on').onclick=()=>{if($('wake-on').disabled)return;control('wake',true).catch(()=>error('현재 실행에 준비한 호출어 모델을 확인해 주세요. GP8은 계속 사용할 수 있어요.'));};
$('games').onclick=()=>{intent('open_game_menu').catch(()=>error('게임 목록을 열지 못했어요.'));$('menu').close();};$('greeting-retry').onclick=()=>desktop('retry_greeting').catch(()=>error('인사 캐시를 확인해 주세요.'));
function openPanel(id,remember=true){hideFaceVolume();cancelPTT();window.BMOPlayableGames?.suspend('menu');const current=document.querySelector('dialog[open]');if(remember)panelParents.set(id,current?.id||'menu');for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();$(id).showModal();syncUiActivity();if(featureState)renderFeatures(featureState);}
for(const dialog of document.querySelectorAll('dialog:not(#menu)')){
 const footer=document.createElement('div');footer.className='panel-navigation';
 const back=document.createElement('button');back.className='panel-back';back.textContent='뒤로';back.onclick=backPanel;
 const face=dialog.querySelector('.dialog-close')||dialog.querySelector('#game-back');
 if(face){footer.append(back,face);dialog.prepend(footer);}
}
for(const [id,panel] of [['conversation-menu','conversation-dialog'],['settings-menu','settings-dialog'],['camera-menu','camera-dialog'],['video-menu','video-dialog'],['arms-menu','arms-dialog'],['voice-menu','voice-dialog'],['music-menu','music-dialog'],['system-menu','system-dialog']])$(id).onclick=()=>openPanel(panel);
for(const button of document.querySelectorAll('.dialog-close'))button.onclick=closeToFace;
$('video-consent').onchange=()=>{if(featureState)renderFeatures(featureState);};
$('video-start').onclick=()=>{
 if($('video-start').disabled||videoActionPending)return;
 const pending={kind:'start',request_id:null};videoActionPending=pending;$('video-start').disabled=true;
 control('video_start',{consent:$('video-consent').checked}).then(result=>{pending.request_id=result.request_id;})
  .catch(()=>{if(videoActionPending===pending)videoActionPending=false;error('영상 준비 상태를 확인해 주세요.');});
 $('video-consent').checked=false;if(featureState?.character_life?.enabled===true)$('video-dialog').close();
};
function saveVideo(){
 if(videoActionPending)return;
 const pending={kind:'stop',request_id:null};videoActionPending=pending;
 control('video_stop').then(result=>{pending.request_id=result.request_id;})
  .catch(()=>{if(videoActionPending===pending)videoActionPending=false;error('영상 저장 상태를 확인해 주세요.');});
}
$('video-stop').onclick=saveVideo;$('video-record-stop').onclick=saveVideo;
$('video-play').onclick=()=>{const player=$('video-review-player');if(player.paused)player.play().catch(()=>error('저장 영상을 재생하지 못했어요. 파일과 codec 준비 상태를 확인해 주세요.'));else player.pause();};
$('video-replay').onclick=()=>{const player=$('video-review-player');player.currentTime=0;player.play().catch(()=>error('저장 영상을 다시 재생하지 못했어요.'));};
for(const name of ['play','pause','ended'])$('video-review-player').addEventListener(name,()=>{$('video-play').textContent=$('video-review-player').paused?'Play':'Pause';});
$('video-review-player').addEventListener('error',()=>{if(videoReviewId!==null)error('저장 영상 재생을 확인하지 못했어요. 파일을 보존하고 준비 상태를 확인해 주세요.');});
$('photo-take').onclick=()=>{intent('take_photo').catch(()=>error('사진 상태를 확인해 주세요.'));$('camera-dialog').close();};
for(const [id,action] of [['photo-take-anyway','take_anyway'],['photo-refocus','refocus']])$(id).onclick=()=>{
 if($(id).disabled||photoFocusPending!==null||featureState?.mode!=='photo'||featureState.photo_phase!=='focus_review')return;
 const value={generation:featureState.mode_generation,request_id:featureState.photo_request_id,action};
 photoFocusPending=value;for(const button of ['photo-take-anyway','photo-refocus'])$(button).disabled=true;
 control('photo_focus',value).catch(()=>{if(photoFocusPending===value){photoFocusPending=null;error('사진 초점 선택을 확인하지 못했어요. Back으로 돌아간 뒤 다시 요청해 주세요.');}});
};
$('camera-preview').onclick=()=>control('preview',featureState?.camera?.preview!==true).catch(()=>error('미리보기를 준비하지 못했어요.'));
$('camera-track').onclick=()=>intent(featureState?.camera?.tracking===true||featureState?.camera?.tracker?.enabled===true?'stop_face_tracking':'start_face_tracking').catch(()=>error('시선 추적을 준비하지 못했어요.'));
$('camera-off').onclick=()=>control('camera_off').catch(()=>error('카메라 종료를 확인해 주세요.'));
$('arms-prepare').onclick=()=>{if($('arms-prepare').disabled)return;$('arms-prepare').disabled=true;control('arm_prepare').then(result=>{armRequest=result.request_id;renderArmReceipt(result);}).catch(()=>error('팔 준비 상태를 확인해 주세요.'));};
$('arms-stop').onclick=()=>intent('stop_arms').then(result=>{armRequest=result.request_id;renderArmReceipt(result);}).catch(()=>error('팔 중지 상태를 확인해 주세요.'));
for(const id of ['arms-enabled','arms-conversation'])$(id).onchange=()=>{
 if(armPreferencePending)return;armPreferencePending=true;
 const value={enabled:$('arms-enabled').checked,conversation_gestures:$('arms-conversation').checked};
 control('arm_usage',value).then(result=>{
  $('arms-result').textContent=['rejected','failed','unknown'].includes(result.status)?'팔 설정을 저장하지 못했어요. 준비 상태를 확인해 주세요.':
   '팔 사용 선호를 저장했어요. 연결·중지 상태와 처음 실물 검사는 별도로 확인해요.';
 }).catch(()=>{$('arms-result').textContent='팔 설정을 저장하지 못했어요. 준비 상태를 확인해 주세요.';})
 .finally(()=>{armPreferencePending=false;if(featureState)renderArms(featureState);});
};
$('provider-recheck').onclick=()=>control('provider_recheck').catch(()=>error('음성 상태 초기화를 완료하지 못했어요.'));
$('voice-batch-preview').onclick=()=>auditionVoiceBatch();
$('voice-batch-preview-stop').onclick=()=>{
 batchPreviewAbort=true;control('cancel').catch(()=>{});
 $('voice-result').textContent='대표 미리듣기 중단을 요청했어요.';
};
$('voice-batch-consent').onchange=()=>{if(featureState)renderVoiceBatch(featureState,featureState.voice_pack);};
$('voice-full-consent').onchange=()=>{if(featureState)renderVoiceBatch(featureState,featureState.voice_pack);};
$('voice-full-enable').onclick=()=>{
 if($('voice-full-enable').disabled||!$('voice-full-consent').checked)return;
 $('voice-full-enable').disabled=true;
 control('voice_full_accept',true).then(()=>{
  $('voice-full-consent').checked=false;
  $('voice-result').textContent='Full contextual use requested. Individual listening and approval records are unchanged.';
 }).catch(()=>{$('voice-result').textContent='Full use could not be enabled. Check the verified pack and current playback.';});
};
$('voice-batch-enable').onclick=()=>{
 if(!batchPreviewCompleted||!$('voice-batch-consent').checked||$('voice-batch-enable').disabled)return;
 $('voice-batch-enable').disabled=true;
 control('voice_batch_accept',true).then(()=>{
  $('voice-result').textContent='검증된 V10 묶음을 상황별 재생으로 활성화했어요. 개별 청취 기록은 변경하지 않았어요.';
  $('voice-batch-consent').checked=false;
 }).catch(()=>{$('voice-result').textContent='활성화 실패. 대표 미리 재생 5개 이상, 현재 대화·PCM·SHA 상태를 확인해 주세요.';});
};
$('voice-batch-disable').onclick=()=>control('voice_batch_disable').then(()=>{
 $('voice-result').textContent='V10 묶음 재생권한을 비활성화했어요. 개별 청취 승인 및 재생 기록은 보존해요.';
}).catch(()=>{$('voice-result').textContent='묶음 비활성화를 확인하지 못했어요.';});
for(const [id,enabled] of [['voice-batch-event-disable',false],['voice-batch-event-enable',true]])
 $(id).onclick=()=>{
  const event=$('voice-batch-event-name').value;if(!event)return;
  $(id).disabled=true;control('voice_batch_event',{event,enabled})
   .then(()=>{$('voice-result').textContent=event+' 이벤트 음성 '+(enabled?'ON':'OFF');})
   .catch(()=>{$('voice-result').textContent='이벤트 재생 정책을 변경하지 못했어요.';});
 };

function submitVolume(value){
 const serial=++volumeSerial;volumePending=value;volumeRequest=null;renderVolume(featureState||{});
 volumeQueue=volumeQueue.catch(()=>{}).then(()=>control('volume',value)).then(result=>{
  if(serial===volumeSerial)volumeRequest=result.request_id;
 }).catch(()=>{if(serial===volumeSerial){volumePending=null;volumeRequest=null;renderVolume(featureState||{});}error('The volume setting could not be applied.');});
}
function setVolume(source='master'){
 const prefix=source==='music'?'music':'master';
 submitVolume({level:Number($(prefix+'-volume').value),muted:$(prefix+'-mute').checked});
}
$('face-volume-trigger').onclick=showFaceVolume;
for(const event of ['pointerdown','keydown'])$('face-volume').addEventListener(event,showFaceVolume);
for(const [id,step] of [['face-volume-down',-5],['face-volume-up',5]])$(id).onclick=()=>{
 const current=volumePending||featureState?.volume;if(!current)return;
 showFaceVolume();submitVolume({level:Math.max(0,Math.min(100,Math.round(current.level*100)+step))/100,muted:current.muted});
};
$('face-volume-mute').onclick=()=>{
 const current=volumePending||featureState?.volume;if(!current)return;
 showFaceVolume();submitVolume({level:current.level,muted:!current.muted});
};
for(const [id,step] of [['menu-volume-down',-5],['menu-volume-up',5]])$(id).onclick=()=>{const current=volumePending||featureState?.volume;if(current)submitVolume({level:Math.max(0,Math.min(100,Math.round(current.level*100)+step))/100,muted:current.muted});};
$('master-volume').oninput=()=>{$('master-volume-value').textContent=Math.round(Number($('master-volume').value)*100)+'%';};
$('master-volume').onchange=setVolume;$('master-mute').onchange=setVolume;
$('music-volume').oninput=()=>{$('music-volume-value').textContent=Math.round(Number($('music-volume').value)*100)+'%';};
$('music-volume').onchange=()=>setVolume('music');$('music-mute').onchange=()=>setVolume('music');
for(const [id,action] of [['music-pause','pause'],['music-resume','resume']])$(id).onclick=()=>mediaCommand(action,'touch_'+action).catch(()=>error('음악 상태를 확인해 주세요.'));
$('music-next').onclick=()=>control('next').catch(()=>error('음악 상태를 확인해 주세요.'));
$('music-stop').onclick=()=>stopMusic('touch_stop');
$('music-toggle').onclick=()=>{const action=featureState?.media?.state==='playing'?'pause':'resume';return mediaCommand(action,'touch_'+action).catch(()=>error('음악 상태를 확인해 주세요.'));};
$('music-replay').onclick=()=>mediaCommand('replay','touch_replay').catch(()=>error('음악을 다시 시작하지 못했어요.'));
$('music-seek').onchange=()=>{const position=Number($('music-seek').value);if(!Number.isFinite(position))return;mediaSeekPending=true;mediaCommand('seek','touch_seek',position).catch(()=>error('음악 재생 위치를 바꾸지 못했어요.')).finally(()=>{mediaSeekPending=false;});};
$('music-back').onclick=()=>stopMusic('touch_back');
for(const event of ['pointerdown','pointermove'])$('music-surface').addEventListener(event,showMusicControls,{passive:true});
$('music-toolbar').addEventListener('focusin',showMusicControls);
$('music-toolbar').addEventListener('focusout',()=>{clearTimeout(mediaControlsTimer);mediaControlsTimer=setTimeout(hideMusicControls,2600);});
$('music-video-view').addEventListener('pointerdown',()=>{if($('music-toolbar').contains(document.activeElement))document.activeElement.blur();},{passive:true});
$('music-video').addEventListener('error',()=>{if(!closed&&$('music-video').error!==null&&mediaVideoKey!==null&&mediaVideoFailure!==mediaVideoKey){mediaVideoFailure=mediaVideoKey;mediaObservation('video_error',videoErrorCode($('music-video').error));mediaCommand('pause','video_error').catch(()=>{});error('음악 영상을 열지 못했어요. 파일은 보존했어요.');}});

for(const id of ['game-back','foreground-back'])$(id).onclick=()=>intent('return_to_face').catch(()=>error('복귀 상태를 확인해 주세요.'));
const keys={ArrowUp:'UP',ArrowDown:'DOWN',ArrowLeft:'LEFT',ArrowRight:'RIGHT',a:'A',z:'A',b:'B',x:'B',' ':'A',Enter:'START',Shift:'SELECT',Escape:'ESC'};
function key(event,pressed){if(event.isComposing)return;
 if(menuKey(event,pressed))return;
 const nativeKey=event.code||event.key;
 if(nativeOwnsInput()&&keys[event.key]){
  event.preventDefault();event.stopPropagation();
  if(pressed)nativeHeldKeys.add(nativeKey);else nativeHeldKeys.delete(nativeKey);
  return;
 }
 if(nativeHeldKeys.has(nativeKey)){
  // A held native-game edge may arrive after the face is restored. Consume
  // its release/repeats; a genuinely new press remains the next GP8 question.
  if(!pressed||event.repeat){event.preventDefault();event.stopPropagation();if(!pressed)nativeHeldKeys.delete(nativeKey);return;}
  nativeHeldKeys.delete(nativeKey);
 }
 if(pressed&&event.key==='Enter'&&!$('face-volume').hidden)hideFaceVolume();if(event.key==='Enter'){if(!pressed){enterDown=false;bump('keyups');}else if(event.repeat)bump('repeats_ignored');else bump('enter_presses');}
 if(event.key==='Enter'&&mediaKeyHeld){
  event.preventDefault();event.stopPropagation();
  if(!pressed){
   clearTimeout(mediaHoldTimer);
   const longHold=mediaHoldTriggered||performance.now()-mediaHoldStarted>=1500;
   mediaKeyHeld=false;mediaHoldTriggered=false;
   if(currentMode==='music'&&!document.querySelector('dialog[open]')){
    if(longHold)stopMusic('gp8_hold');
    else if(!mediaStopping)mediaCommand(featureState?.media?.state==='playing'?'pause':'resume','gp8_short').catch(()=>error('음악 상태를 확인해 주세요.'));
   }
  }
  return;
 }
 if(event.key==='Enter'&&currentMode==='music'&&!document.querySelector('dialog[open]')&&!isTextInput(event.target)){
  event.preventDefault();event.stopPropagation();if(!pressed||event.repeat)return;
  mediaKeyHeld=true;mediaHoldTriggered=false;mediaHoldStarted=performance.now();showMusicControls();
  mediaHoldTimer=setTimeout(()=>{
   if(mediaKeyHeld&&currentMode==='music'&&!document.querySelector('dialog[open]')){
    mediaHoldTriggered=true;stopMusic('gp8_hold');
   }
  },1500);
  return;
 }
 if(event.key==='Escape'&&currentMode==='music'&&!isTextInput(event.target)){event.preventDefault();event.stopPropagation();if(pressed)stopMusic('escape');return;}
 if(event.key==='Enter'&&videoKeyHeld){event.preventDefault();event.stopPropagation();if(!pressed)videoKeyHeld=false;return;}
 if(event.key==='Enter'&&currentMode==='video'&&!document.querySelector('dialog[open]')&&!isTextInput(event.target)){
  event.preventDefault();event.stopPropagation();if(!pressed||event.repeat)return;videoKeyHeld=true;
  if(['starting','recording'].includes(featureState?.video_phase))saveVideo();
  else if(featureState?.video_phase!=='finalizing')intent('return_to_face').catch(()=>error('복귀 상태를 확인해 주세요.'));
  return;}
 if(event.key==='Enter'&&photoReviewKeyHeld){event.preventDefault();event.stopPropagation();if(!pressed)photoReviewKeyHeld=false;return;}
 if(event.key==='Enter'&&currentMode==='photo'
  &&!document.querySelector('dialog[open]')&&!isTextInput(event.target)){
  event.preventDefault();event.stopPropagation();if(!pressed||event.repeat)return;photoReviewKeyHeld=true;
  intent('return_to_face').catch(()=>error('복귀 상태를 확인해 주세요.'));return;}
 // In the conversation surface Enter belongs to GP8/PTT, even if a closed
 // menu or startup notice left a button focused. Suppress native button clicks
 // on BOTH edges and repeats; dialogs and text editing retain their own keys.
 if(event.key==='Enter'&&currentMode==='conversation'&&!document.querySelector('dialog[open]')&&canTalk()&&!isTextInput(event.target)){
  event.preventDefault();event.stopPropagation();if(!pressed)return;if(event.repeat||enterDown)return;enterDown=true;ptt();return;}
 if(event.repeat)return;
 if(pressed&&event.ctrlKey&&event.altKey&&event.code==='KeyD'&&state?.developer_enabled&&state.phase==='ready'){event.preventDefault();location.assign('/developer');return;}
 if(event.key==='Escape'){
  event.preventDefault();event.stopPropagation();
  const covering=document.querySelector('dialog[open]:not(#game-menu)');
  if(covering){if(pressed){cancelPTT();covering.close();}return;}
  if(['photo','video'].includes(currentMode)){if(pressed)intent('return_to_face').catch(()=>error('복귀 상태를 확인해 주세요.'));return;}
  if(['game','game_menu'].includes(currentMode)){control('input',{key:'ESC',pressed}).catch(()=>error('게임 종료 상태를 확인해 주세요.'));return;}
  if(pressed){cancelPTT();for(const d of document.querySelectorAll('dialog[open]'))d.close();menu();}return;}
 if(isTextInput(event.target)||event.target?.closest?.('button'))return;
 if(['game','game_menu'].includes(currentMode)&&keys[event.key]){if(event.key==='Enter'&&pressed)bump('blocked_mode');event.preventDefault();event.stopPropagation();
  if(currentMode==='game'&&featureState?.games?.web?.active===true&&window.BMOPlayableGames){window.BMOPlayableGames.input(event.code==='Space'?'B':keys[event.key],pressed);return;}
  control('input',{key:keys[event.key],pressed}).catch(()=>error('게임 입력 상태를 확인해 주세요.'));return;}}
window.addEventListener('keydown',event=>key(event,true));window.addEventListener('keyup',event=>key(event,false));
function blurPTT(event){const nativeAtEvent=nativeOwnsInput();setTimeout(()=>{
 // Focusing the iframe blurs its parent window. A later move back to a notice
 // button is still inside BMO, even if it happens before this callback runs.
 // Only loss of the whole document's focus cancels trusted input here.
 mediaObservation('blur');
 if(nativeAtEvent||nativeOwnsInput()||event.isTrusted&&document.hasFocus())return;clearSelectKey();photoReviewKeyHeld=false;videoKeyHeld=false;mediaKeyHeld=false;mediaHoldTriggered=false;clearTimeout(mediaHoldTimer);bump('cancelled_blur');cancelPTT();window.BMOPlayableGames?.suspend('blur');
},0);}
window.addEventListener('blur',blurPTT);
window.addEventListener('focus',()=>mediaObservation('focus'));
let touchStart=null,inputDocument=null,hintObserver=null;
function hideIntegratedFaceHint(doc){
 // The approved file remains byte-for-byte unchanged. Only its standalone
 // launcher hint is hidden when it is embedded inside the product wrapper.
 const hint=/^(?:화면\s*돌아가기|Back\s+to\s+(?:screen|face))\s*[·∙•|]\s*Esc(?:ape)?$/i;
 for(const element of doc.querySelectorAll('body *')){
  if(element.namespaceURI!=='http://www.w3.org/1999/xhtml'||!['DIV','P','SPAN','SMALL','BUTTON','ASIDE'].includes(element.tagName))continue;
  const text=element.textContent.trim().replace(/\s+/g,' ');
  if(text.length>=80||!hint.test(text))continue;
  const descendants=[...element.querySelectorAll('*')];
  // Artwork often contributes no textContent. A face wrapper can therefore
  // have the same text as its tiny hint: never hide that wrapper. Match only
  // the smallest text container, with ordinary nonempty inline formatting.
  if(descendants.some(child=>hint.test(child.textContent.trim().replace(/\s+/g,' '))))continue;
  if(descendants.some(child=>child.namespaceURI!=='http://www.w3.org/1999/xhtml'
      ||!['SPAN','KBD','EM','STRONG','B','I','SMALL'].includes(child.tagName)
      ||!child.textContent.trim()))continue;
  element.dataset.bmoIntegrationHint='hidden';element.style.setProperty('display','none','important');element.setAttribute('aria-hidden','true');
 }
}

function faceTapAllowed(){return canTalk()&&!nativeOwnsInput()&&currentMode==='conversation'&&!document.querySelector('dialog[open]')&&$('face-volume').hidden;}
function clearFaceTouch(){clearTimeout(touchStart?.timer);touchStart=null;}
function bindFrameInputs(){try{const doc=frame.contentDocument;if(!doc||!doc.body||doc===inputDocument)return;inputDocument=doc;hintObserver?.disconnect();hideIntegratedFaceHint(doc);hintObserver=new MutationObserver(()=>hideIntegratedFaceHint(doc));hintObserver.observe(doc.body,{childList:true,subtree:true,characterData:true});doc.addEventListener('keydown',event=>key(event,true),true);doc.addEventListener('keyup',event=>key(event,false),true);doc.defaultView.addEventListener('blur',blurPTT);
 doc.addEventListener('pointerdown',event=>{
  clearFaceTouch();if(!faceTapAllowed()||event.isPrimary===false||event.button!==0)return;
  touchStart={id:event.pointerId,x:event.clientX,y:event.clientY,at:performance.now(),held:false};
  if(event.clientX<60&&event.clientY<60)touchStart.timer=setTimeout(()=>{
   if(touchStart&&faceTapAllowed()){touchStart.held=true;menu();}
  },1200);
 });
 doc.addEventListener('pointerup',event=>{
  const start=touchStart;clearFaceTouch();
  if(!start||start.id!==event.pointerId||start.held||!faceTapAllowed())return;
  if(performance.now()-start.at<=650&&Math.hypot(event.clientX-start.x,event.clientY-start.y)<=12)menu();
 });
 for(const event of ['pointercancel','lostpointercapture'])doc.addEventListener(event,clearFaceTouch);
 doc.addEventListener('pointermove',event=>{if(touchStart&&(event.pointerId!==touchStart.id||Math.hypot(event.clientX-touchStart.x,event.clientY-touchStart.y)>12))clearFaceTouch();});
 doc.defaultView.addEventListener('blur',clearFaceTouch);
}catch(_){}}
frame.addEventListener('load',()=>{cancelPTT();bindFrameInputs();});bindFrameInputs();
// The standard browser mapping is opt-in and game-only. Never map a pad button
// to GP8/TALK, GP7, a servo command, camera capture or a provider API.
let padTimer=null,padIndex=null,padGeneration=null,padHeld=new Set(),padSuppressed=new Set(),padSequence=0,padHeartbeat=0,padChain=Promise.resolve(),padPending=0;
const padEvidence={polls:0,edges:0,disconnects:0,mapping:'standard_game_only',physical_verified:false};
function sendPad(key,pressed,generation){
 if(generation===null||closed||nativeOwnsInput()||padPending>=32)return;
 const value={key,pressed,sequence:++padSequence,generation},created=performance.now(),ownerEpoch=nativeInputEpoch;padPending++;
 padChain=padChain.catch(()=>{}).then(()=>{if(closed||nativeOwnsInput()||ownerEpoch!==nativeInputEpoch||performance.now()-created>450)return;return control('gamepad',value);}).catch(()=>{}).finally(()=>{padPending--;});
}
function retireBrowserGameInput(){
 nativeInputEpoch++;clearTimeout(padTimer);padTimer=null;
 padIndex=null;padGeneration=null;padHeld=new Set();padSuppressed=new Set();padHeartbeat=0;
 if(enterDown)nativeHeldKeys.add('Enter');enterDown=false;
}
function releasePad(disconnected=true){
 if(nativeOwnsInput()){retireBrowserGameInput();return;}
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
function gamepadAllowed(next){return !nativeOwnsInput(next)&&next?.usb_gamepad?.enabled===true&&state?.phase==='ready'&&!document.hidden&&document.hasFocus()&&['game','game_menu'].includes(next.mode)&&!document.querySelector('dialog[open]:not(#game-menu)');}
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
window.addEventListener('blur',event=>{
 const index=padIndex,generation=padGeneration,ownerEpoch=nativeInputEpoch;
 setTimeout(()=>{
  // A trusted parent/iframe focus move stays inside BMO. A delayed callback
  // belongs to the pad owner present at that event, never a later game.
  if(nativeOwnsInput()||nativeInputEpoch!==ownerEpoch||event.isTrusted&&document.hasFocus()||padIndex!==index||padGeneration!==generation)return;
  releasePad();
 },0);
});
window.addEventListener('gamepaddisconnected',()=>releasePad());
window.addEventListener('pagehide',()=>{mediaObservation('pagehide');stopPolling();clearSelectKey();hintObserver?.disconnect();clearCaption();photoReviewKeyHeld=false;videoKeyHeld=false;releasePad();cancelPTT();closed=true;hideFaceVolume();clearTimeout(uiActivityTimer);stopPreview();stopVideoReview();stopMediaVideo();window.BMOPlayableGames?.close();epoch++;clearTimeout(timer);controller?.abort();window.BMOIntegration?.close();});
document.addEventListener('visibilitychange',()=>{mediaObservation(document.hidden?'hidden':'visible');clearCaption();clearTimeout(timer);if(pollWorker)pollWorker.postMessage({action:'visibility',hidden:document.hidden});if(document.hidden){hideFaceVolume();stopPreview();$('video-review-player').pause();window.BMOPlayableGames?.suspend('hidden');photoReviewKeyHeld=false;videoKeyHeld=false;releasePad();if(!nativeOwnsInput())cancelPTT();}else{syncUiActivity();refreshAdapter();}poll();});
window.BMODesktop={state:null,desktop,control,intent,menu,inputEvidence,gamepadEvidence:padEvidence,previewEvidence};Object.defineProperty(window.BMODesktop,'pollEvidence',{get:()=>({owner:pollOwner,hidden:document.hidden,closed})});window.BMOPlayableGames?.configure({control,intent});startPolling();
})();
