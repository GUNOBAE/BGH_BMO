/* Product wrapper only. The pinned R10 and Python audio owner stay unchanged. */
(()=>{'use strict';
const $=id=>document.getElementById(id),frame=$('face'),token=document.querySelector('meta[name="bmo-command-token"]').content;
let state=null,closed=false,timer=null,controller=null,epoch=0,handshakeFrame=null,lastSuccess=performance.now(),currentMode='conversation',lastGaze=null,presentedRun=null,presentationGeneration=0;
let enterDown=false,pttEnabled=false,pttPending=false,pttRequest=null,pttEpoch=0;
let featureState=null,musicCatalogKey=null,gameCatalogKey=null,lastFeatureMode=null;
let photoReviewKeyHeld=false,photoPreviewContext=null,photoPreviewAcknowledged=null,photoFocusPending=null,suppressedCaption=null,subtitlePending=null,subtitleRequest=0;
let videoKeyHeld=false,videoPreviewContext=null,videoPreviewAcknowledged=null,videoActionPending=false,videoReviewId=null;
let volumePending=null,volumeSerial=0,armCatalogKey=null,voiceCatalogKey=null;
let previewRun=null,previewSerial=0,previewTimer=null,previewAbort=null,previewBitmap=null,previewDraw=null,previewBusy=false,previewSuppressedKey=null;
const previewEvidence={target_fps:20,active:false,pending:0,max_pending:0,requests:0,received:0,decoded:0,displayed:0,
 dropped:0,not_displayed:0,duplicates:0,errors:0,last_received_id:null,last_decoded_id:null,last_displayed_id:null,
 last_frame_age_ms:null,last_display_age_ms:null,last_display_age_upper_bound_ms:null,
 age_basis:'server_age_plus_client_elapsed; transit_unknown',received_fps:null,decoded_fps:null,displayed_fps:null};
const previewSamples={received:[],decoded:[],displayed:[]};
const inputEvidence={enter_presses:0,keyups:0,repeats_ignored:0,blocked_mode:0,request_sent:0,request_accepted:0,request_rejected:0,cancelled_blur:0,physical_identity:'UNVERIFIED'};
function bump(name){inputEvidence[name]=Math.min(999999,inputEvidence[name]+1);}
const messages={credentials_missing:'최초 키 설정이 필요해요. 실행 가이드를 확인해 주세요.',greeting_missing:'시작 인사 캐시가 아직 준비되지 않았어요.',greeting_unapproved:'시작 인사의 청취 승인이 필요해요.',greeting_corrupt:'시작 인사 캐시를 확인해 주세요.',face_ready_timeout:'얼굴 준비 시간이 초과됐어요.',host_startup_timeout:'대화 엔진 준비 시간이 초과됐어요.',greeting_timeout:'시작 인사 재생 정리를 기다리고 있어요.',browser_disconnected:'연결을 다시 확인하고 있어요.'};
async function post(path,payload){const response=await fetch(path,{method:'POST',keepalive:payload.action==='cancel',headers:{'Content-Type':'application/json','X-BMO-Token':token},body:JSON.stringify({version:1,nonce:crypto.randomUUID(),payload})});if(!response.ok)throw Error('요청을 완료하지 못했어요.');return response.json();}
const desktop=(action,value=null)=>post('/api/desktop',{action,value});
function hideCaption(){$('subtitles').textContent='';$('subtitles').hidden=true;}
function clearCaption(){const current=window.BMOIntegration?.state;suppressedCaption=current?.utterance_id||null;hideCaption();}
function control(action,value=null){
 if(['ptt','question','cancel','stop','reset','repeat'].includes(action))clearCaption();
 if(['cancel','stop','reset'].includes(action)||action==='preview'&&value===false)stopPreview(true);
 if(action==='subtitles'&&['off','en','ko'].includes(value)){
  const serial=++subtitleRequest;subtitlePending={mode:value,serial};hideCaption();
  return post('/api/control',{action,value}).catch(error=>{if(subtitlePending?.serial===serial)subtitlePending=null;throw error;});
 }
 return post('/api/control',{action,value});
}
function intent(name,target=null){if(name==='return_to_face'){clearCaption();stopVideoReview();}if(name==='return_to_face')stopPreview(true);return post('/api/intent',{name,target});}
function error(message){$('notice').hidden=false;$('notice-message').textContent=message;}
function menu(){cancelPTT();window.BMOPlayableGames?.suspend('menu');if(!$('menu').open)$('menu').showModal();}
function isTextInput(target){return !!(target?.isContentEditable||target?.closest?.('input,select,textarea'));}
function restoreFaceFocus(){
 if(closed||!canTalk()||currentMode!=='conversation'||document.querySelector('dialog[open]')||isTextInput(document.activeElement))return;
 frame.focus({preventScroll:true});
}
for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('close',()=>{restoreFaceFocus();if(featureState)syncPreview(featureState);});
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
 const problem=next.phase==='error'||next.phase==='ready'&&next.code!=='greeting_completed'&&!(next.character_life?.enabled&&next.code==='greeting_cancelled');
 $('notice').hidden=!problem;if(problem)$('notice-message').textContent=messages[next.code]||'준비 상태를 확인해 주세요. 메뉴에서 재시도하거나 종료할 수 있어요.';
 $('diagnostic').textContent=next.code;$('talk').disabled=!canTalk();$('repeat').disabled=next.phase!=='ready';
 $('presence-controls').hidden=!next.character_life?.enabled;
 if(next.character_life?.enabled)$('presence-mode').value=next.character_life.presence.mode;
 $('greeting-retry').disabled=next.phase!=='ready'||next.code==='greeting_completed';}
async function modes(){if(!canTalk())return;const r=await fetch('/api/features',{cache:'no-store'});if(!r.ok)return;const next=await r.json();currentMode=next.mode;
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
 const wakePhase=next.wake_word?.handoff?.phase;
 const wakeBoundary=next.microphone_broker?.question_boundary?.status;
 const wakeRetry=['AMBIGUOUS_PREFIX_RETRY_REQUIRED','FRESH_AFTER_UTTERANCE_QUIET'].includes(wakeBoundary)
  &&['waiting_wake_end','question_listening'].includes(wakePhase);
 const wakeHint=wakeRetry?'Please pause, then ask again. Or press the button.':
  ({confirming:'One moment…',waiting_wake_end:'I heard you…',question_listening:'I’m listening. What’s up?'})[wakePhase]||'';
 $('ptt-state').hidden=!inputFailed&&!wakeHint&&!['calibrating','listening','speech','transcribing'].includes(capture);
 const failures={no_speech:'말소리를 감지하지 못했어요. 버튼을 누르고 다시 말해 주세요.',stt_empty:'말을 알아듣지 못했어요. 다시 말해 주세요.'};
 $('ptt-state').textContent=inputFailed?(next.ptt?.cleanup_fault?'녹음 정리를 확인해 주세요. 앱을 종료한 뒤 검수해 주세요.':failures[failure?.code]||(capture==='failed'&&failure?.stage!=='stt'?'녹음을 완료하지 못했어요. 마이크 설정과 연결을 확인해 주세요.':'대화를 완료하지 못했어요. 준비 상태에서 실패 단계를 확인해 주세요.')):
  wakeHint||({calibrating:'주변 소리를 확인해요',listening:'듣고 있어요',speech:'말소리를 감지했어요',transcribing:'말을 확인하고 있어요'})[capture]||'';
 if(next.mode==='game_menu'&&!document.querySelector('dialog[open]:not(#game-menu)')){
  if(!$('game-menu').open)$('game-menu').showModal();
  const key=JSON.stringify(next.games.catalog);
  if(key!==gameCatalogKey){gameCatalogKey=key;$('game-list').replaceChildren();
   if(!next.games.catalog.length)$('game-list').textContent='게임 자료가 아직 준비되지 않았어요.';
   for(const item of next.games.catalog){const b=document.createElement('button');b.dataset.gameId=item.id;b.textContent=item.title;b.disabled=item.readiness!=='READY';
    b.onclick=()=>intent('launch_game',item.id).catch(()=>error('게임을 시작하지 못했어요.'));$('game-list').append(b);}
  }
  for(const button of $('game-list').querySelectorAll('button'))button.setAttribute('aria-current',String(button.dataset.gameId===next.games.selected_id));
 }
 else if($('game-menu').open)$('game-menu').close();
 window.BMOPlayableGames?.sync(next);
 $('foreground').hidden=!['game','photo','video'].includes(next.mode)||next.mode==='game'&&next.games?.web?.active===true;
 const face=frame.contentWindow?.BMOFace,gaze=next.gaze,phase=window.BMOIntegration?.state?.phase;
 if(face&&gaze){const native=[phase,face.getState().activity].some(p=>['listening','thinking'].includes(p));if(native&&gaze.owner!=='manual')lastGaze=null;
 else if(gaze.apply){const id=JSON.stringify([gaze.owner,gaze.x,gaze.y,next.mode_generation]);if(id!==lastGaze){face.look(gaze.x,gaze.y);face.lookEyes(gaze.x*.15,gaze.y*.15);if(gaze.owner==='reaction')face.blink();lastGaze=id;}}}
}
function renderFeatures(next){
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
 if(!videoScreen)$('foreground-status').textContent=next.mode==='game'?'게임 실행 중':
  (photoPhase==='failed'?(photoReasons[photoFailure?.stage]||'Couldn’t finish that photo.'):
   ({preparing:'Getting the camera ready…',countdown:'Hold still!',focusing:'Checking the focus…',
    focus_review:focusMessage,capturing:'Taking your photo…',
    photo_review:next.photo?.focus_status==='unconfirmed'?'Photo saved. Focus unconfirmed.':
     next.photo?.focus_status==='unsupported'?'Photo saved. This camera cannot confirm focus.':'Photo saved.'})[photoPhase])||'사진을 준비하고 있어요.';
 const photoReady=photoScreen&&photoPhase==='photo_review'&&next.photo?.completed===true&&typeof next.photo?.photo_id==='string';
 $('photo-result-image').hidden=!photoReady;$('photo-download').hidden=!photoReady;
 if(photoReady){const url='/api/photo/'+encodeURIComponent(next.photo.photo_id);if($('photo-result-image').getAttribute('src')!==url)$('photo-result-image').src=url;$('photo-download').href=url;}
 const photoLive=photoScreen&&['preparing','countdown','focusing','focus_review'].includes(photoPhase)&&next.camera?.on===true;
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
 const liveMusic=next.capabilities?.music==='explicit_supervised';
 $('music-readiness').textContent=next.media?.catalog?.length?(liveMusic?'검토한 로컬 WAV를 선택한 스피커에서 재생해요. 실제 음량은 사용자 검수가 필요해요.':next.simulation?'현재 목록은 검증된 모의 재생 자료예요. 실제 음원·출력은 별도 검수가 필요해요.':'로컬 음원은 준비됐어요. 실제 재생에는 감독 실행과 음량 검토가 필요해요.'):'사용자 음원과 출처·라이선스 검토가 필요해요.';
 const catalogKey=JSON.stringify(next.media?.catalog||[]);
 if(catalogKey!==musicCatalogKey){musicCatalogKey=catalogKey;$('music-list').replaceChildren();for(const item of next.media?.catalog||[]){const b=document.createElement('button');b.textContent=item.title;b.disabled=item.readiness!=='READY'||!['mock','explicit_supervised'].includes(next.capabilities?.music);b.onclick=()=>intent('play_song',item.id).catch(()=>error('음악을 시작하지 못했어요.'));$('music-list').append(b);}}
 for(const [id,capability] of [['music-pause','pause'],['music-resume','resume']])$(id).disabled=next.mode!=='music'||!next.media?.capabilities?.includes(capability);
 for(const id of ['music-next','music-stop'])$(id).disabled=next.mode!=='music';
 const telemetry=next.system_diagnostics?.telemetry;
 $('system-readiness').textContent=telemetry?.power?.status==='OBSERVED'?('전원 현재 이상: '+(telemetry.power.current_fault?'있음':'없음')+' · 과거 기록: '+(telemetry.power.historical_fault?'있음':'없음')):'온도·전원 실측은 아직 실행하지 않았어요. 자동 설정 변경은 하지 않아요.';
 const arm=next.system_diagnostics?.arms,armLink=arm?.transport?.status;
 const armStatus={NOT_CONNECTED:'연결 확인 전',ACKNOWLEDGED:'프로토콜 응답 확인',UNKNOWN:'연결 상태 미확정',STOPPED:'중지됨',CLOSED:'닫힘'};
 $('arm-readiness').textContent='팔 앱 연결: '+(armStatus[armLink]||'미관측')+' · 실제 배선·전원·움직임: 미검수';
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
 const volume=volumePending||next.volume||next.audio_output?.volume;
 if(!volume)return;
 if(Number.isFinite(volume.level)&&volume.level>=0&&volume.level<=1){
  if(document.activeElement!==$('master-volume'))$('master-volume').value=String(volume.level);
  $('master-volume-value').textContent=Math.round(volume.level*100)+'%';
 }
 if(typeof volume.muted==='boolean')$('master-mute').checked=volume.muted;
}
function renderArms(next){
 const arms=next.system_diagnostics?.arms||{},motion=next.motion||{};
 const gestures=arms.supported_gestures||motion.supported_gestures||motion.capabilities||[];
 const names=Array.isArray(gestures)?gestures.filter(id=>typeof id==='string'&&/^[a-z][a-z0-9_]{0,63}$/.test(id)):[];
 const blocked=motion.blocked_reasons||arms.blocked_reasons||[];
 const reason=blocked[0]||motion.reason_code||arms.reason_code||arms.preflight?.code||'arm_state_unverified';
 const labels={arm_runtime_off:'팔 동작이 현재 실행에서 꺼져 있어요.',real_disabled:'실물 팔 동작이 현재 실행에서 꺼져 있어요.',
  operator_conditions_missing:'현재 감독 조건이 준비되지 않았어요.',arm_operator_required:'현재 감독 조건이 준비되지 않았어요.',
  arm_firmware_mismatch:'검토된 보드 SHA와 일치하지 않아요.',arm_controller_disabled:'보드 원격 동작이 비활성 상태예요.',arm_board_disabled:'보드 원격 동작이 비활성 상태예요.',
  arm_port_busy:'팔 포트를 다른 프로그램이 사용하고 있어요.',arm_link_unknown:'팔 통신 상태를 확인할 수 없어요.',
  arm_stop_unverified:'팔 중지 응답을 확인하지 못했어요.',arm_operator_condition_missing:'현재 감독 조건이 준비되지 않았어요.',arm_transport_error:'팔 통신 상태를 확인할 수 없어요.',
  hello_required:'감독 실행이 설정됐어요. 첫 명령에서 기존 B1 연결 응답을 확인해요.',position_unverified:'B1 연결 응답을 확인했어요. 실제 위치는 미확인이에요.',
  arm_closed:'팔 연결이 닫혔어요.',arm_stopped:'팔이 중지됐어요. 자동으로 다시 활성화하지 않아요.',motion_stopped:'팔이 중지됐어요. 자동으로 다시 활성화하지 않아요.'};
 const link=arms.transport?.status||motion.controller?.status||'UNVERIFIED';
 $('arms-status').textContent=(labels[reason]||reason)+' · '+link+' · 실제 위치·배선·움직임은 미검수';
 const allowed=next.capabilities?.arms==='mock'||next.capabilities?.arms==='explicit_supervised';
 const ready=allowed&&arms.preflight?.status!=='BLOCKED'&&motion.closed!==true&&motion.stopped!==true;
 const key=JSON.stringify([names,ready]);
 if(key!==armCatalogKey){
  armCatalogKey=key;$('arms-gestures').replaceChildren();
  if(!names.length)$('arms-gestures').textContent='이 실행에서 지원하는 명명 제스처가 아직 준비되지 않았어요.';
  const titles={wave:'손 흔들기',raise_left_arm:'왼팔 들기',raise_right_arm:'오른팔 들기',both_up:'양팔 들기',both_down:'양팔 내리기',celebrate:'만세',shrug:'어깨 으쓱'};
  for(const name of names){const button=document.createElement('button');button.textContent=titles[name]||name;button.dataset.gesture=name;button.disabled=!ready;
   button.onclick=()=>{button.disabled=true;intent('arm_gesture',name).then(result=>{$('arms-result').textContent=result.code||'팔 요청을 전달했어요. 실제 움직임은 미검수예요.';})
    .catch(()=>{$('arms-result').textContent='팔 요청을 완료하지 못했어요. 연결·감독 상태를 확인해 주세요.';}).finally(()=>{armCatalogKey=null;});};$('arms-gestures').append(button);}
 }
 $('arms-stop').disabled=motion.closed===true;
}
function renderVoiceBank(next){
 const bank=next.voice_pack||next.voice_bank;
 if(!bank){$('voice-readiness').textContent='private 음성팩 전달과 개별 청취 승인 상태를 아직 확인하지 못했어요.';return;}
 const items=bank.inventory||bank.items||bank.clips||[];
 $('voice-readiness').textContent=bank.status==='ASSET_PENDING'?'112개 후보의 private 정리본 전달·가져오기를 기다려요. 가져온 자산은 아직 없어요.':
  (bank.code||bank.status||'VOICE_PACK_UNVERIFIED')+' · 가져온 후보 '+items.length+'개 · 개별 승인 '+(bank.approved||0)+'개';
 for(const button of $('voice-list').querySelectorAll('button[data-voice-event]'))
  button.disabled=button.dataset.assetReady!=='true'||next.mode!=='conversation'||next.conversation_busy||next.audio_owner!==null
   ||next.volume?.effective_level===0;
 const key=JSON.stringify([items,bank.selected_events]);if(key===voiceCatalogKey)return;voiceCatalogKey=key;$('voice-list').replaceChildren();
 for(const item of items.slice(0,224)){
  if(typeof item.id!=='string')continue;
  const row=document.createElement('section');row.className='voice-candidate';row.dataset.candidate=item.id;
  const title=document.createElement('p');title.textContent=(item.text||item.title||item.id)+' · '+item.id+' · '+(item.source_model||'출처 미확인');row.append(title);
  const events=Array.isArray(item.events)?item.events:bank.event_names||[];
  if(events.length){
   const label=document.createElement('label');label.textContent='연결할 이벤트 ';
   const select=document.createElement('select');select.setAttribute('aria-label',item.id+' event');
   for(const event of events){if(typeof event!=='string')continue;const option=document.createElement('option');option.value=event;option.textContent=event;select.append(option);}
   if(Array.isArray(item.selected_events)&&item.selected_events.length)select.value=item.selected_events[0];
   const assign=document.createElement('button');assign.textContent='선택';assign.onclick=()=>{
    assign.disabled=true;control('voice_select',{event:select.value,candidate_id:item.id})
     .then(()=>{$('voice-result').textContent='이벤트 후보를 선택했어요. 개별 승인 전에는 재생하지 않아요.';})
     .catch(()=>{$('voice-result').textContent='이 자산은 해당 이벤트에 연결할 수 없어요.';}).finally(()=>{assign.disabled=false;});};
   const play=document.createElement('button');play.textContent='승인된 선택 음성 재생';play.dataset.voiceEvent=select.value;
   const updatePlay=()=>{
    const ready=item.approved===true&&Array.isArray(item.selected_events)&&item.selected_events.includes(select.value);
    play.dataset.voiceEvent=select.value;play.dataset.assetReady=String(ready);
    play.disabled=!ready||featureState?.mode!=='conversation'||featureState?.conversation_busy||featureState?.audio_owner!==null
     ||featureState?.volume?.effective_level===0;
   };
   select.onchange=updatePlay;updatePlay();
   play.onclick=()=>{if(play.disabled)return;play.disabled=true;control('voice_event',select.value)
    .then(()=>{$('voice-result').textContent='승인된 선택 음성의 재생을 요청했어요. 새 음성은 생성하지 않아요.';})
    .catch(()=>{$('voice-result').textContent='이벤트 선택·개별 승인·출력 상태를 확인해 주세요.';});};
   label.append(select);row.append(label,assign,play);
  }
  for(const [name,variant] of Object.entries(item.variants||{})){
   if(typeof variant.sha256!=='string'||!/^[a-f0-9]{64}$/.test(variant.sha256))continue;
   const line=document.createElement('div');line.className='voice-variant';line.dataset.variant=name;
   const info=document.createElement('p');const duration=Number.isFinite(variant.duration_seconds)?variant.duration_seconds.toFixed(2)+'s':'길이 미확인';
   info.textContent=name+' · baked gain '+variant.baked_gain+' · '+duration+' · SHA '+variant.sha256;line.append(info);
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
 const photo=next.mode==='photo'&&['preparing','countdown','focusing','focus_review'].includes(next.photo_phase)
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
async function poll(){if(closed||document.hidden)return;const current=epoch;controller=new AbortController();const abort=setTimeout(()=>controller?.abort(),1200);try{
 const r=await fetch('/api/desktop',{cache:'no-store',signal:controller.signal});if(!r.ok)throw Error();const next=await r.json();if(current!==epoch||closed)return;
 lastSuccess=performance.now();render(next);await ready();await modes();
}catch(_){if(performance.now()-lastSuccess>2500){clearCaption();$('splash').hidden=true;error('BMO 연결이 끊겼어요. 창을 닫고 수동으로 다시 실행해 주세요.');}}
finally{clearTimeout(abort);controller=null;if(!closed&&current===epoch&&!document.hidden)timer=setTimeout(poll,100);}}
$('notice-menu').onclick=menu;$('resume').onclick=()=>$('menu').close();$('exit').onclick=()=>desktop('exit').catch(()=>{error('BMO가 종료된 상태예요. 창을 닫아 주세요.');window.close();});
function ptt(){if(!pttEnabled){error('버튼 녹음은 사용자 검수용 마이크 opt-in이 필요해요.');return;}
 if(pttPending){control('ptt').catch(()=>error('버튼 입력을 확인해 주세요.'));return;}
 pttPending=true;pttRequest=null;const current=++pttEpoch;
 bump('request_sent');control('ptt').then(result=>{bump('request_accepted');if(current===pttEpoch&&pttPending)pttRequest=result.request_id;}).catch(()=>{bump('request_rejected');if(current===pttEpoch){pttPending=false;error('버튼 녹음을 시작하지 못했어요.');}});}
function cancelPTT(){window.BMOIntegration?.cancelEntrance('input_cancelled');enterDown=false;if(pttPending){pttEpoch++;pttPending=false;pttRequest=null;control('cancel').catch(()=>{});}}
$('talk').onclick=()=>{if(pttEnabled)ptt();else control('question','A manually requested question.').catch(()=>error('대화를 시작하지 못했어요.'));$('menu').close();};
$('repeat').onclick=()=>control('repeat').catch(()=>error('다시 들을 답변이 없어요.'));$('stop').onclick=()=>control('stop').catch(()=>error('정지 상태를 확인해 주세요.'));
$('presence-mode').onchange=()=>desktop('presence_mode',$('presence-mode').value).catch(()=>error('대기 음성 설정을 확인해 주세요.'));
$('subtitle-mode').onchange=()=>control('subtitles',$('subtitle-mode').value).catch(()=>error('자막 설정을 확인해 주세요.'));
$('system-dialog').addEventListener('close',()=>{$('turn-diagnostics').textContent='';});
$('wake-off').onclick=()=>desktop('wake_off').catch(()=>error('호출어 종료 상태를 확인해 주세요.'));
$('wake-on').onclick=()=>{if($('wake-on').disabled)return;control('wake',true).catch(()=>error('현재 실행에 준비한 호출어 모델을 확인해 주세요. GP8은 계속 사용할 수 있어요.'));};
$('games').onclick=()=>{intent('open_game_menu').catch(()=>error('게임 목록을 열지 못했어요.'));$('menu').close();};$('greeting-retry').onclick=()=>desktop('retry_greeting').catch(()=>error('인사 캐시를 확인해 주세요.'));
function openPanel(id){cancelPTT();window.BMOPlayableGames?.suspend('menu');$('menu').close();$(id).showModal();if(featureState)renderFeatures(featureState);}
for(const [id,panel] of [['camera-menu','camera-dialog'],['video-menu','video-dialog'],['arms-menu','arms-dialog'],['voice-menu','voice-dialog'],['music-menu','music-dialog'],['system-menu','system-dialog']])$(id).onclick=()=>openPanel(panel);
for(const button of document.querySelectorAll('.dialog-close'))button.onclick=()=>button.closest('dialog').close();
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
$('arms-stop').onclick=()=>intent('stop_arms').then(result=>{$('arms-result').textContent=result.code||'팔 중지를 요청했어요. 실제 전원과 위치는 별도로 확인해 주세요.';}).catch(()=>error('팔 중지 상태를 확인해 주세요.'));
$('provider-recheck').onclick=()=>control('provider_recheck').catch(()=>error('음성 상태 초기화를 완료하지 못했어요.'));
function setVolume(){
 const serial=++volumeSerial,value={level:Number($('master-volume').value),muted:$('master-mute').checked};volumePending=value;
 control('volume',value).catch(()=>error('음량 설정을 적용하지 못했어요.')).finally(()=>{if(serial===volumeSerial)volumePending=null;});
}
$('master-volume').oninput=()=>{$('master-volume-value').textContent=Math.round(Number($('master-volume').value)*100)+'%';};
$('master-volume').onchange=setVolume;$('master-mute').onchange=setVolume;
for(const [id,action] of [['music-pause','pause'],['music-resume','resume'],['music-next','next']])$(id).onclick=()=>control(action).catch(()=>error('음악 상태를 확인해 주세요.'));
$('music-stop').onclick=()=>intent('stop_media').catch(()=>error('음악 종료를 확인해 주세요.'));
for(const id of ['game-back','foreground-back'])$(id).onclick=()=>intent('return_to_face').catch(()=>error('복귀 상태를 확인해 주세요.'));
const keys={ArrowUp:'UP',ArrowDown:'DOWN',ArrowLeft:'LEFT',ArrowRight:'RIGHT',a:'A',z:'A',b:'B',x:'B',' ':'A',Enter:'START',Shift:'SELECT',Escape:'ESC'};
function key(event,pressed){if(event.isComposing)return;if(event.key==='Enter'){if(!pressed){enterDown=false;bump('keyups');}else if(event.repeat)bump('repeats_ignored');else bump('enter_presses');}
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
function blurPTT(event){setTimeout(()=>{
 // Focusing the iframe blurs its parent window. A later move back to a notice
 // button is still inside BMO, even if it happens before this callback runs.
 // Only loss of the whole document's focus cancels trusted input here.
 if(event.isTrusted&&document.hasFocus())return;photoReviewKeyHeld=false;videoKeyHeld=false;bump('cancelled_blur');cancelPTT();window.BMOPlayableGames?.suspend('blur');
},0);}
window.addEventListener('blur',blurPTT);
let touchStart=null,inputDocument=null;function bindFrameInputs(){try{const doc=frame.contentDocument;if(!doc||doc===inputDocument)return;inputDocument=doc;doc.addEventListener('keydown',event=>key(event,true),true);doc.addEventListener('keyup',event=>key(event,false),true);doc.defaultView.addEventListener('blur',blurPTT);
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
window.addEventListener('pagehide',()=>{clearCaption();photoReviewKeyHeld=false;videoKeyHeld=false;releasePad();cancelPTT();closed=true;stopPreview();stopVideoReview();window.BMOPlayableGames?.close();epoch++;clearTimeout(timer);controller?.abort();window.BMOIntegration?.close();});
document.addEventListener('visibilitychange',()=>{clearCaption();epoch++;clearTimeout(timer);controller?.abort();if(document.hidden){stopPreview();$('video-review-player').pause();window.BMOPlayableGames?.suspend('hidden');photoReviewKeyHeld=false;videoKeyHeld=false;releasePad();cancelPTT();}else{refreshAdapter();poll();}});
window.BMODesktop={state:null,desktop,control,intent,menu,inputEvidence,gamepadEvidence:padEvidence,previewEvidence};window.BMOPlayableGames?.configure({control,intent});poll();
})();
