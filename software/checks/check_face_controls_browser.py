#!/usr/bin/env python3
"""Real product browser: face volume, semantic menu and full-pack consent.

Only synthetic local media and injected provider/device IO are used. No private
asset, physical playback or listening approval is implied by this check.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
import zipfile

SOFTWARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE))
from checks import check_v10_media_browser as media
from checks.check_full_batch_browser import replace_once
from bmo_ai.screen_profiles import SCREEN_PROFILES
from bmo_ai.voice_pack_context import context_catalog


def prepared_catalogue(directory):
    """Give generated PCM the public script metadata and distinct clip hashes."""
    archive = Path(directory)/'synthetic-v10.zip'
    with zipfile.ZipFile(archive) as source:
        files = {name: source.read(name) for name in source.namelist()}
    value = json.loads(files['catalogue.json'])
    catalogue = context_catalog()
    for index, row in enumerate(value['entries']):
        row['spoken_text'] = catalogue[row['clip_id']]['en']
        raw = files[row['file']]
        if row['clip_id'].startswith('C') and row['clip_id'][1:].isdigit():
            # A synthetic sustained envelope makes actual R10 mouth calls
            # observable while this one idle player's cancellation is tested.
            from test_voice_pack_v10 import pcm
            raw = pcm(3.)
        # Alter only one synthetic PCM sample, never a user asset. Distinct
        # hashes make a wrong random clip/caption pairing observable.
        raw = raw[:-2]+(2400+index).to_bytes(2, 'little', signed=True)
        files[row['file']] = raw
        row['sha256'] = hashlib.sha256(raw).hexdigest()
    files['catalogue.json'] = json.dumps(value).encode()
    with zipfile.ZipFile(archive, 'w') as target:
        for name, raw in files.items():
            target.writestr(name, raw)
    return {row['clip_id']: row['sha256'] for row in value['entries']}


def child_source():
    source = replace_once(media.child_source(),
        "    active=sum(process.poll() is None for process in players)",
        "    if (root/'hold-next-player').exists():\n        (root/'hold-next-player').unlink();command=[sys.executable,'-c','import time; time.sleep(5)']\n    active=sum(process.poll() is None for process in players)")
    return replace_once(source,
        "            counts['brain']+=1;time.sleep(.15)\n            return super().create(**request)", r'''
            counts['brain']+=1;time.sleep(.15)
            schema=request['text']['format']['schema']
            token=schema['properties']['request']['properties']['turn_token']['enum'][0]
            message=request['input'][-1]['content']
            name={'Open the menu, please.':'open_menu','메뉴 닫아.':'close_menu'}.get(message)
            semantic=dict(turn_token=token,kind='menu' if name else 'none',
                disposition='execute' if name else 'none',emotion='none',
                subtitle_mode='unchanged',confirmation_token=None)
            speech='All right.'
            reply=dict(version=5,speech=speech,emotion='neutral',gesture='none',
                intent=dict(name=name,target=None) if name else None,plan=dict(version=1,steps=[]),reaction='none',
                character=dict(delivery_id='calm',intensity='low',gaze_id='none',source='dialogue'),
                request=semantic,captions=dict(en=speech,ko='좋아.'),photo_success=None)
            from types import SimpleNamespace
            return SimpleNamespace(status='completed',incomplete_details=None,error=None,output_text=json.dumps(reply))
''')


def missing_greeting_caption_check(profile='ci-synthetic', chromium=None, *, screenshot=None):
    """Reproduce the Pi notice with actual missing cache and saved-voice IO."""
    from playwright.sync_api import sync_playwright
    face = media.desktop.FIXTURE if profile == 'ci-synthetic' else Path(SCREEN_PROFILES[profile]['default_path'])
    source = replace_once(child_source(), "for clip in ('boot_01','idle_01'):", "for clip in ('idle_01',):")
    source = replace_once(source, "if scenario=='greeting_missing':cache=GreetingCache(cache_path,GreetingSpec.from_config(config))",
        "if scenario in ('greeting_missing','character'):cache=GreetingCache(cache_path,GreetingSpec.from_config(config))")
    source = replace_once(source, 'def play(*,emit,cancel_check):', 'def play(*,emit,cancel_check,**settings):')
    source = replace_once(source, 'return original(emit=observed,cancel_check=cancel_check)',
        'return original(emit=observed,cancel_check=cancel_check,**settings)')
    with tempfile.TemporaryDirectory(prefix='bmo-notice-caption-') as directory:
        prepare = media.prepare
        def prepare_fixture(root):prepare(root);prepared_catalogue(root)
        with patch.object(media,'prepare',prepare_fixture), patch.object(media,'child_source',lambda:source), media.fixture_for(directory,profile,face) as fixture:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=chromium,headless=True,
                    env={'PATH':os.defpath,'HOME':directory,'LC_ALL':'C.UTF-8'},
                    args=['--no-sandbox','--disable-gpu','--mute-audio','--disable-audio-output',
                          '--disable-background-networking','--disable-component-update','--no-first-run'])
                try:
                    page = browser.new_page(viewport={'width':800,'height':480})
                    page.set_default_timeout(10000)
                    page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(fixture.url) else route.abort())
                    page.goto(fixture.url,wait_until='domcontentloaded')
                    try:page.wait_for_function("()=>BMODesktop.state?.phase==='ready'&&BMODesktop.state.code==='greeting_missing'")
                    except Exception as error:
                        error.add_note(json.dumps(dict(startup=fixture.state(),audit=fixture.features().get('test_fixture'))))
                        raise
                    page.evaluate("()=>BMODesktop.control('voice_full_accept',true)")
                    page.wait_for_function('()=>BMODesktop.features?.voice_pack?.full_user_accepted===true')
                    page.evaluate("()=>BMODesktop.control('subtitles','ko')")
                    (Path(directory)/'hold-next-player').touch()
                    page.evaluate("()=>BMODesktop.control('voice_event','music_selected')")
                    page.locator('#subtitles').wait_for(state='visible')
                    evidence = page.evaluate("""()=>{
                      const notice=document.querySelector('#notice'),subtitle=document.querySelector('#subtitles');
                      const a=notice.getBoundingClientRect(),b=subtitle.getBoundingClientRect();
                      return {notice:{x:a.x,y:a.y,width:a.width,height:a.height},
                        subtitle:{x:b.x,y:b.y,width:b.width,height:b.height},
                        overlap:Math.max(a.left,b.left)<Math.min(a.right,b.right)&&Math.max(a.top,b.top)<Math.min(a.bottom,b.bottom),
                        noticeVisible:!notice.hidden,captionVisible:!subtitle.hidden,
                        code:BMODesktop.state.code,clip:BMOIntegration.state.caption_asset.clip_id};
                    }""")
                    assert not evidence['noticeVisible'] and evidence['captionVisible'] and evidence['code']=='greeting_missing',evidence
                    assert not evidence['overlap'],evidence
                    assert page.locator('#face').bounding_box() == {'x':0,'y':0,'width':800,'height':480}
                    if screenshot:page.screenshot(path=str(screenshot))
                    page.evaluate("()=>BMODesktop.control('cancel')")
                    page.locator('#subtitles').wait_for(state='hidden')
                    assert page.locator('#notice').is_hidden()
                    page.mouse.click(400,200)
                    page.locator('#settings-menu').click()
                    page.locator('#system-menu').click()
                    assert 'greeting_missing' in page.locator('#greeting-readiness').inner_text()
                    assert page.locator('#chime-readiness').is_visible()
                    page.locator('#system-dialog .dialog-close').click()
                    code,counts,error=fixture.finish()
                    assert code==0 and counts['forbidden']==0,error
                    return evidence
                finally:browser.close()


def run(profile='ci-synthetic', chromium='/usr/bin/chromium'):
    from playwright.sync_api import sync_playwright
    face = media.desktop.FIXTURE if profile == 'ci-synthetic' else Path(SCREEN_PROFILES[profile]['default_path'])
    original_face = hashlib.sha256(face.read_bytes()).hexdigest()
    source = child_source()
    checks = []
    with tempfile.TemporaryDirectory(prefix='bmo-face-controls-') as directory:
        prepare = media.prepare
        hashes = {}
        def prepare_fixture(root):
            prepare(root)
            hashes.update(prepared_catalogue(root))
        with patch.object(media, 'prepare', prepare_fixture), patch.object(media, 'child_source', lambda: source), media.fixture_for(directory, profile, face) as fixture:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=chromium, headless=True,
                    env={'PATH': os.defpath, 'HOME': directory, 'LC_ALL': 'C.UTF-8'},
                    args=['--no-sandbox', '--disable-gpu', '--mute-audio', '--disable-audio-output',
                          '--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'])
                try:
                    page = browser.new_page(viewport={'width': 800, 'height': 480})
                    page.set_default_timeout(12000)
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    actions = []
                    page.on('request', lambda request: actions.append(request.post_data_json.get('payload', {}))
                        if request.method == 'POST' and request.url.endswith('/api/control') else None)
                    page.route('**/*', lambda route: route.continue_()
                        if route.request.url.startswith(fixture.url) else route.abort())
                    page.add_init_script("""window.__browserAudioCalls=0;
                        const nativePlay=HTMLMediaElement.prototype.play;
                        HTMLMediaElement.prototype.play=function(){
                          if(this.tagName==='VIDEO'&&this.muted)return nativePlay.call(this);
                          window.__browserAudioCalls++;throw Error('Browser audio forbidden');};""")

                    def settled(expression):
                        page.wait_for_function('()=>'+expression)

                    def volume(level, muted=False):
                        page.wait_for_function("""([level,muted])=>{
                          const v=BMODesktop.features?.volume;
                          return v?.level===level&&v.muted===muted
                            &&document.querySelector('#face-volume-value').textContent===Math.round(level*100)+'%'
                            &&document.querySelector('#master-volume-value').textContent===Math.round(level*100)+'%'
                            &&document.querySelector('#music-volume-value').textContent===Math.round(level*100)+'%';
                        }""", arg=[level, muted])

                    def question(text):
                        previous = fixture.features()['test_fixture']['brain']
                        page.evaluate('text=>BMODesktop.control("question",text)', text)
                        page.wait_for_function("""previous=>BMODesktop.features?.test_fixture.brain===previous+1
                            &&!BMODesktop.features.conversation_busy&&BMODesktop.features.audio_owner===null""", arg=previous)

                    page.goto(fixture.url, wait_until='domcontentloaded')
                    settled("BMODesktop.state?.code==='greeting_completed'&&BMODesktop.features?.mode==='conversation'")
                    volume(.25)
                    assert page.locator('#face-volume').is_hidden()
                    # Actual lower-right screen touch: no exposed menu required.
                    page.mouse.click(780, 450)
                    page.locator('#face-volume').wait_for(state='visible')
                    settled('BMODesktop.state.character_life.settings_active===true')
                    for _ in range(3):
                        page.locator('#face-volume-up').click()
                    volume(.40)
                    page.locator('#face-volume-down').click()
                    volume(.35)
                    page.locator('#face-volume-mute').click()
                    volume(.35, True)
                    assert page.locator('#face-volume-mute').inner_text() == 'Unmute'
                    page.locator('#face-volume-mute').click()
                    volume(.35)
                    assert [value['value'] for value in actions if value['action'] == 'volume'] == [
                        dict(level=level, muted=muted) for level, muted in
                        ((.30, False), (.35, False), (.40, False), (.35, False), (.35, True), (.35, False))]
                    page.locator('#face-volume').wait_for(state='hidden', timeout=4500)
                    settled('BMODesktop.state.character_life.settings_active===false')
                    checks.append('lower-right touch, rapid five-point steps and mute share authoritative Host values; HUD clears after three idle seconds')

                    # Held GP8 is still input, never a native focused volume click.
                    page.mouse.click(780, 450)
                    page.locator('#face-volume-up').focus()
                    previous = fixture.features()['test_fixture']
                    page.keyboard.down('Enter')
                    page.keyboard.up('Enter')
                    settled("BMODesktop.features.test_fixture.brain==="+str(previous['brain']+1)+"&&!BMODesktop.features.conversation_busy")
                    volume(.35)
                    assert page.locator('#face-volume').is_hidden()
                    assert fixture.features()['test_fixture']['mic_sources'] == previous['mic_sources']+1
                    checks.append('actual Enter down/up on a focused HUD button starts one PTT and consumes native activation without changing volume')

                    # Keep the injected output alive long enough to prove mute
                    # cancels the owner, rather than observing a natural end.
                    page.mouse.click(780, 450)
                    (Path(directory)/'hold-next-player').touch()
                    previous = fixture.features()['test_fixture']
                    page.evaluate('()=>BMODesktop.control("question","Tell me something small.")')
                    settled("BMODesktop.features.phase==='speaking'&&BMODesktop.features.conversation_busy")
                    began = time.monotonic()
                    page.locator('#face-volume-mute').click()
                    volume(.35, True)
                    settled('!BMODesktop.features.conversation_busy&&BMODesktop.features.audio_owner===null')
                    assert time.monotonic()-began < 3, 'Mute waited for the five-second player to finish'
                    assert fixture.features()['test_fixture']['players'] == previous['players']+1
                    page.locator('#face-volume-mute').click()
                    volume(.35)
                    question('A new question after mute.')
                    checks.append('HUD mute cancels an active owned utterance promptly; unmute accepts the next conversation without overlapping players')

                    question('Open the menu, please.')
                    page.locator('#menu').wait_for(state='visible')
                    sequence = fixture.features()['ui_menu']['sequence']
                    page.locator('#resume').click()
                    page.locator('#menu').wait_for(state='hidden')
                    settled('BMODesktop.state.character_life.settings_active===false')
                    assert fixture.features()['ui_menu']['sequence'] == sequence
                    assert page.locator('#menu').is_hidden(), 'The same semantic event reopened the menu'
                    question('Open the menu, please.')
                    page.locator('#menu').wait_for(state='visible')
                    question('메뉴 닫아.')
                    page.locator('#menu').wait_for(state='hidden')
                    checks.append('one injected GPT response reaches Host menu intent and real dialog; consumed sequence does not reopen manually closed menu; Korean close restores face')

                    # Full contextual permission is independent of representative
                    # auditions and per-file listening approval.
                    page.keyboard.press('Escape')
                    page.locator('#settings-menu').click()
                    page.locator('#voice-menu').click()
                    page.locator('#voice-full-consent').check()
                    assert not page.locator('#voice-full-enable').is_disabled()
                    previous = fixture.features()['test_fixture']
                    page.locator('#voice-full-enable').click()
                    settled('BMODesktop.features.voice_pack.full_user_accepted===true')
                    bank = fixture.features()['voice_pack']
                    assert bank['candidates'] == bank['batch_released'] == 142
                    assert bank['approved'] == bank['listened'] == bank['played'] == 0
                    assert fixture.features()['test_fixture']['players'] == previous['players']
                    page.locator('#voice-dialog .dialog-close').click()
                    page.evaluate("()=>BMODesktop.control('subtitles','ko')")
                    settled("BMODesktop.features.subtitles.mode==='ko'")
                    settled('BMODesktop.state.character_life.settings_active===false')
                    # Drive only the existing scheduler's injected clock, not
                    # the Host watchdog, audio clock or caption/mouth position.
                    page.evaluate("()=>BMODesktop.control('voice_select',{event:'idle',candidate_id:'C01'})")
                    settled("BMODesktop.features.voice_pack.selected_events.idle==='C01'")
                    page.evaluate("()=>BMODesktop.desktop('presence_mode','normal')")
                    settled("BMODesktop.state.character_life.presence.mode==='normal'")
                    baseline_idle = fixture.features()['test_fixture']
                    (Path(directory)/'hold-next-player').touch()
                    staged = Path(directory)/'presence-clock.stage'
                    staged.write_text('500')
                    os.replace(staged, Path(directory)/'presence-clock.txt')
                    page.wait_for_function("""caption=>{
                      const p=BMOIntegration.state?.performance_plan;
                      const face=document.querySelector('#face').contentWindow.BMOFace.getState();
                      const subtitle=document.querySelector('#subtitles');
                      return BMOIntegration.state?.source==='cached_idle'&&BMOIntegration.state.playback==='started'
                        &&p?.clip_id==='C01'&&face.selectedEmotion===p.face_emotion
                        &&BMODesktop.features.gaze.owner==='presence'
                        &&face.look.x===.08&&face.look.y===-.04&&face.mouth!=='closed'
                        &&!subtitle.hidden&&subtitle.textContent===caption;
                    }""", arg=context_catalog()['C01']['ko'])
                    idle = page.evaluate('()=>BMOIntegration.state')
                    assert idle['performance_plan']['sha256'] == hashes['C01']
                    assert idle['caption_asset']['sha256'] == hashes['C01']
                    assert page.locator('#subtitles').inner_text() == context_catalog()['C01']['ko']
                    assert fixture.state()['character_life']['presence']['suppression_reason'] == 'playing'
                    page.locator('#face').focus()
                    page.keyboard.down('Enter');page.keyboard.up('Enter')
                    settled('BMODesktop.features.test_fixture.brain==='+str(baseline_idle['brain']+1)+
                        '&&!BMODesktop.features.conversation_busy&&BMODesktop.features.audio_owner===null')
                    settled("!BMOIntegration.state?.performance_plan&&BMODesktop.features.gaze.owner!=='presence'")
                    assert fixture.features()['test_fixture']['mic_sources'] == baseline_idle['mic_sources']+1
                    assert fixture.features()['test_fixture']['max_players'] == 1
                    page.evaluate("()=>BMODesktop.desktop('presence_mode','off')")
                    settled("BMODesktop.state.character_life.presence.mode==='off'")
                    checks.append('exact C01 PCM drives local Korean caption, bounded gaze, original emotion and WAV mouth only while playing; real GP8 down/up clears idle pose for one next PTT turn')
                    catalogue, selected = context_catalog(), []
                    baseline = fixture.features()['test_fixture']
                    for ending in ('natural', 'cancel', 'off'):
                        if ending != 'natural':
                            (Path(directory)/'hold-next-player').touch()
                        page.evaluate("()=>BMODesktop.control('voice_event','music_selected')")
                        page.locator('#subtitles').wait_for(state='visible')
                        snapshot = page.evaluate('()=>BMOIntegration.state')
                        assert 'caption_asset' in snapshot, snapshot
                        asset = snapshot['caption_asset']
                        clip = asset['clip_id']
                        assert clip in ('M04', 'M05', 'M06'), asset
                        assert asset['candidate_id'] == clip and asset['sha256'] == hashes[clip]
                        assert snapshot['playback'] == 'started' and snapshot['phase'] == 'speaking'
                        assert page.locator('#subtitles').inner_text() == catalogue[clip]['ko']
                        selected.append(clip)
                        if ending == 'cancel':
                            page.locator('#face').focus()
                            page.keyboard.press('Enter')
                        elif ending == 'off':
                            page.evaluate("()=>BMODesktop.control('subtitles','off')")
                        page.locator('#subtitles').wait_for(state='hidden')
                        if ending == 'off':
                            page.evaluate("()=>BMODesktop.control('cancel')")
                        settled('!BMODesktop.features.conversation_busy&&BMODesktop.features.audio_owner===null')
                    assert len(set(selected)) == 3, selected
                    current = fixture.features()['test_fixture']
                    assert (current['brain'], current['synthesis'], current['history_messages']) == (
                        baseline['brain'], baseline['synthesis'], baseline['history_messages']), (baseline,current)
                    (Path(directory)/'fail-next-player').touch()
                    page.evaluate("()=>BMODesktop.control('subtitles','ko')")
                    page.evaluate("()=>BMODesktop.control('voice_event','music_selected')")
                    settled('BMODesktop.features.test_fixture.players==='+str(current['players']+1)+
                        '&&!BMODesktop.features.conversation_busy&&BMODesktop.features.audio_owner===null')
                    assert page.locator('#subtitles').is_hidden()
                    question('A new question after the cached clip.')
                    page.locator('#subtitles').wait_for(state='hidden')
                    checks.append('random M04/M05/M06 captions match selected ID and unique SHA at actual player-start; local Korean text clears on end/cancel/OFF/error and cached events make no GPT/TTS/history writes')
                    page.keyboard.press('Escape')
                    page.locator('#settings-menu').click()
                    page.locator('#voice-menu').click()
                    page.locator('#voice-batch-event-name').select_option('music_selected')
                    page.locator('#voice-batch-event-disable').click()
                    settled("BMODesktop.features.voice_pack.batch.disabled_events.includes('music_selected')")
                    page.locator('#voice-batch-event-enable').click()
                    settled("!BMODesktop.features.voice_pack.batch.disabled_events.includes('music_selected')")
                    page.locator('#voice-batch-disable').click()
                    settled('!BMODesktop.features.voice_pack.full_user_accepted&&BMODesktop.features.voice_pack.batch_released===0')
                    page.locator('#voice-dialog .dialog-close').click()
                    checks.append('full142 consent requires no repeated audition, creates no per-file listened/approved record or audio, and retains event OFF/ON and revoke')

                    # Shared control applies immediately to existing music owner;
                    # face animation and decoder ownership remain product code.
                    page.keyboard.press('Escape')
                    page.locator('#music-menu').click()
                    page.locator('[data-song-id=space_potatoes] [data-media-mode=face]').click()
                    settled("BMODesktop.features.mode==='music'&&BMODesktop.features.media.state==='playing'")
                    page.locator('#music-surface').click(position={'x': 5, 'y': 5})
                    assert page.locator('#face-volume-trigger').is_hidden()
                    volume(.35)
                    page.locator('#music-volume').wait_for(state='visible')
                    for _ in range(3):
                        page.locator('#music-volume').press('ArrowRight')
                    volume(.50)
                    page.locator('#music-mute').check()
                    volume(.50, True)
                    assert fixture.features()['media']['state'] == 'playing'
                    page.locator('#music-mute').uncheck()
                    volume(.50)
                    page.locator('#music-back').click()
                    settled("BMODesktop.features.mode==='conversation'&&BMODesktop.features.audio_owner===null")
                    page.mouse.click(780, 450)
                    volume(.50)
                    page.locator('#face-volume').wait_for(state='visible')
                    previous = fixture.features()['test_fixture']['brain']
                    page.keyboard.press('Enter')
                    settled('BMODesktop.features.test_fixture.brain==='+str(previous+1)+'&&!BMODesktop.features.conversation_busy&&BMODesktop.features.audio_owner===null')
                    assert page.evaluate('()=>window.__browserAudioCalls') == 0
                    assert not errors, errors
                    checks.append('music overlay sees face-set volume, music changes return to face HUD, mute preserves playback clock and Back releases owner for the next PTT')
                    code, counts, error = fixture.finish()
                    assert code == 0 and counts['forbidden'] == 0 and counts['players'] == counts['reaped'] and counts['max_players'] == 1, error
                finally:
                    browser.close()
    assert hashlib.sha256(face.read_bytes()).hexdigest() == original_face
    notice = missing_greeting_caption_check(profile, chromium)
    checks.append('optional greeting_missing never covers or reserves the full face; local Korean captions clear on cancellation and separate greeting/chime state remains in readiness')
    return dict(status='PASS', checks=checks, notice_caption_layout=notice,
                not_run=['Pi touch and sound level', 'private clip listening', 'physical GP8'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=('ci-synthetic', *SCREEN_PROFILES), default='ci-synthetic')
    parser.add_argument('--chromium', nargs='?', const=None, default='/usr/bin/chromium')
    args = parser.parse_args()
    print(json.dumps(run(args.profile, args.chromium), ensure_ascii=False, indent=2))
