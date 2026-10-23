/* External shell only. The approved face artwork and speech owner are retained. */
(() => {"use strict";
  const token=document.querySelector('meta[name="bmo-command-token"]').content;
  const $=id=>document.getElementById(id);let state=null,closed=false,timer=null,controller=null,lastGame=null,lastGaze=null,pollGeneration=0;
  const post=async(path,payload,nonce=crypto.randomUUID())=>{
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-BMO-Token':token},body:JSON.stringify({version:1,nonce,payload})});
    if(!response.ok)throw new Error('요청을 처리할 수 없습니다.');return response.json();};
  const intent=(name,target=null)=>post('/api/intent',{name,target});
  const control=(action,value=null)=>post('/api/control',{action,value});
  function resultMessage(result){if(!result)return '';
    if(result.status==='rejected'||result.status==='failed'||result.status==='unknown')return '실행을 완료하지 못했습니다. 자료·지원 상태를 확인하세요.';
    if(result.completion==='position_unverified')return '모의 명령 응답 확인 · 실제 팔 위치는 확인되지 않았습니다.';
    return result.status==='running'||result.status==='accepted'?'모의 작업 진행 중':'모의 작업 완료 · 실제 장치 동작 없음';}
  function fillCatalog(element,items,handler){element.replaceChildren();for(const item of items){const button=document.createElement('button');button.textContent=item.title+(item.simulated?' · 모의':'');button.disabled=item.readiness!=='READY';button.dataset.catalogId=item.id;button.addEventListener('click',()=>handler(item.id).catch(showError));element.append(button);}}
  function showError(error){$('result').textContent=error.message;}
  function render(next){if(state&&(next.mode_generation<state.mode_generation||next.sequence<state.sequence))return;state=next;window.BMOFeatures.state=next;
    const mode=next.mode;for(const [id,selected] of [['face-panel',mode==='conversation'||mode==='music'],['game-menu',mode==='game_menu'],['game-panel',mode==='game'],['photo-panel',mode==='photo'],['music-panel',mode==='music']])$(id).hidden=!selected;
    fillCatalog($('game-list'),next.games.catalog,id=>intent('launch_game',id));
    const song=$('songs').value;const items=next.media.catalog;$('songs').replaceChildren();for(const item of items){const option=document.createElement('option');option.value=item.id;option.textContent=item.title+' · 모의';option.disabled=item.readiness!=='READY';$('songs').append(option);}if(items.some(x=>x.id===song))$('songs').value=song;$('song-play').disabled=!items.length;
    $('result').textContent=resultMessage(next.last_result);$('media-status').textContent=next.media.state==='paused'?'모의 일시 정지':'합성 파일의 무음 재생 시뮬레이션';
    const countdown=next.photo_countdown;$('photo-status').textContent=countdown!==null?`합성 사진 저장까지 ${Math.ceil(countdown)}`:next.photo?.completed?'합성 사진 파일 저장 확인':'실제 카메라는 꺼져 있습니다.';
    $('photo-result').hidden=!next.photo?.photo_id;if(next.photo?.photo_id)$('photo-result').src='/api/photo/'+encodeURIComponent(next.photo.photo_id);
    $('preview-panel').hidden=!next.camera.preview;if(next.camera.preview)$('preview').src='/api/preview?t='+Date.now();else $('preview').removeAttribute('src');
    $('track-button').disabled=next.camera.tracking;$('camera-off').disabled=!next.camera.on;
    if(mode==='game'&&lastGame!==next.games.active_id){lastGame=next.games.active_id;$('game-title').textContent='모의 게임 · '+lastGame;$('game-frame').srcdoc='<!doctype html><html><body style="background:#12353a;color:#d7f5d4;font:24px system-ui;text-align:center"><p>합성 게임 실행 중</p><p>실제 게임 자산은 아직 없습니다.</p></body></html>';}
    if(mode!=='game'){lastGame=null;$('game-frame').removeAttribute('srcdoc');}
    const gaze=next.gaze,face=$('face').contentWindow?.BMOFace;
    const livePhase=window.BMOIntegration?.state?.phase,nativeActive=face&&([livePhase,face.getState().activity].some(phase=>['listening','thinking'].includes(phase)));
    if(nativeActive&&gaze.owner!=='manual')lastGaze=null;
    else if(face&&gaze.apply){const key=JSON.stringify([gaze.owner,gaze.x,gaze.y,next.mode_generation]);if(key!==lastGaze){face.look(gaze.x,gaze.y);face.lookEyes(gaze.x*.15,gaze.y*.15);lastGaze=key;}}
    else if(gaze.owner==='native')lastGaze=null;
  }
  async function poll(){if(closed||document.hidden)return;const generation=pollGeneration,request=new AbortController();controller=request;try{const response=await fetch('/api/features',{cache:'no-store',signal:request.signal});if(!response.ok)throw new Error();const next=await response.json();if(generation===pollGeneration&&!closed&&!document.hidden)render(next);}catch(error){if(error.name!=='AbortError')$('result').textContent='연결을 확인 중입니다. 실제 장치는 자동으로 켜지지 않습니다.';}finally{if(controller===request)controller=null;if(!closed&&generation===pollGeneration&&!document.hidden)timer=setTimeout(poll,80);}}
  document.querySelectorAll('[data-intent]').forEach(button=>button.onclick=()=>intent(button.dataset.intent).catch(showError));
  document.querySelectorAll('[data-control]').forEach(button=>button.onclick=()=>control(button.dataset.control,button.dataset.control==='question'?'Synthetic browser question.':null).catch(showError));
  $('emotion').onchange=()=>control('emotion',$('emotion').value).catch(showError);$('preview-button').onclick=()=>control('preview',!state?.camera.preview).catch(showError);$('song-play').onclick=()=>intent('play_song',$('songs').value).catch(showError);
  const keys={ArrowUp:'UP',ArrowDown:'DOWN',ArrowLeft:'LEFT',ArrowRight:'RIGHT',a:'A',b:'B',Escape:'ESC',Enter:'START',Shift:'SELECT'};
  function sendKey(key,pressed){control('input',{key,pressed}).catch(showError);}
  document.querySelectorAll('[data-key]').forEach(button=>{button.onpointerdown=event=>{button.setPointerCapture(event.pointerId);sendKey(button.dataset.key,true);};button.onpointerup=()=>sendKey(button.dataset.key,false);button.onpointercancel=()=>sendKey(button.dataset.key,false);});
  window.addEventListener('keydown',event=>{if(event.repeat||['INPUT','SELECT','TEXTAREA','BUTTON'].includes(event.target.tagName))return;const key=keys[event.key];if(key){event.preventDefault();sendKey(key,true);}});window.addEventListener('keyup',event=>{const key=keys[event.key];if(key)sendKey(key,false);});
  window.addEventListener('blur',()=>{for(const key of ['START','SELECT'])sendKey(key,false);});
  window.addEventListener('pagehide',()=>{closed=true;clearTimeout(timer);controller?.abort();});
  document.addEventListener('visibilitychange',()=>{pollGeneration++;clearTimeout(timer);controller?.abort();if(!document.hidden&&!closed)poll();});
  window.BMOFeatures={state:null,intent,control,post};poll();
})();
