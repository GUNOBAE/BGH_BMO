"""Private synthetic files and injected player only; paid/device/secret IO = 0."""
from dataclasses import replace
import errno
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch
import wave

from bmo_ai import audio
from bmo_ai.conversation import TurnCancelled
from bmo_ai.greeting import GreetingPlayer
from bmo_ai.greeting_cache import (GREETING_TEXT, MAGIC, GreetingCache, GreetingError,
    GreetingSpec, default_greeting_directory, make_synthetic_greeting_cache)
from bmo_ai.integration_face import wav_cues
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav


class GreetingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = integration_config()
        self.spec = GreetingSpec.from_config(self.config)
        self.cache = GreetingCache(self.root/'greeting', self.spec)
        self.raw = synthetic_wav()
        self.addCleanup(audio._discard, self.raw, suppress=True)
        self.guards = []
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'subprocess.Popen',
                       'bmo_ai.private_credentials.resolve_credentials', 'bmo_ai.cloud.create_client',
                       'bmo_ai.audio.record_wav', 'bmo_ai.audio.transcribe',
                       'bmo_ai.fish_tts.synthesize_wav', 'bmo_ai.local_piper.synthesize_wav',
                       'bmo_ai.integration_profile.policy_brain'):
            guard = Mock(side_effect=AssertionError('Production IO forbidden'))
            self.enterContext(patch(target, guard));self.guards.append(guard)

    def tearDown(self):
        for guard in self.guards:
            guard.assert_not_called()

    @property
    def file(self):
        return self.cache.directory/'greeting.cache'

    def publish(self, **settings):
        return self.cache.publish(self.raw, **settings)

    def assert_code(self, code, callback):
        with self.assertRaises(GreetingError) as error:
            callback()
        self.assertEqual(error.exception.code, code)
        self.assertNotIn(str(self.root), str(error.exception))
        self.assertNotIn('private-secret', str(error.exception))

    def bundle(self):
        header, _, wav = self.file.read_bytes()[len(MAGIC):].partition(b'\n')
        return json.loads(header), wav

    def rewrite(self, header, wav):
        raw = json.dumps(header, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        self.file.write_bytes(MAGIC+raw+b'\n'+wav)

    def player(self, callback, *, simulate=False):
        value = GreetingPlayer(self.config, self.cache, simulate=simulate, player=callback)
        self.addCleanup(value.close)
        return value

    def test_constructors_and_location_resolution_do_not_open_files(self):
        with patch('os.open', side_effect=AssertionError('No IO at construction')):
            cache = GreetingCache(self.root/'not-created', self.spec)
            GreetingPlayer(self.config, cache, simulate=True)
            self.assertEqual(default_greeting_directory(environ={},home=self.root),self.root/'.cache/bmo/greeting-welcome-en-v1')
            self.assertEqual(default_greeting_directory(environ={'XDG_CACHE_HOME':str(self.root/'xdg')}),self.root/'xdg/bmo/greeting-welcome-en-v1')
        self.assertFalse(cache.directory.exists())
        self.assertEqual(self.spec.public()['text'], "Oh! You're back! BMO missed you!")
        self.assertEqual(self.spec.public()['gain'], .25)

    def test_welcome_asset_is_separate_from_preserved_legacy_cache(self):
        legacy_root = self.root / '.cache' / 'bmo' / 'greeting'
        for directory in (self.root / '.cache', self.root / '.cache' / 'bmo', legacy_root):
            directory.mkdir(mode=0o700, exist_ok=True)
        legacy_file = legacy_root / 'greeting.cache'
        legacy_file.write_bytes(b'old-approved-BMO-voice')
        welcome_root = default_greeting_directory(environ={}, home=self.root)
        self.assertNotEqual(legacy_root, welcome_root)
        record = GreetingCache(welcome_root, self.spec).publish(
            self.raw, approved=True, simulated=True)
        self.assertTrue(record['approved'])
        self.assertEqual(legacy_file.read_bytes(), b'old-approved-BMO-voice')
        self.assertTrue((welcome_root / 'greeting.cache').is_file())

    def test_missing_cache_does_not_create_directories_or_trigger_player(self):
        callback = Mock(side_effect=AssertionError('No player without asset'))
        events = []
        self.assert_code('greeting_missing', lambda:self.player(callback).play(emit=lambda k,**f:events.append(k)))
        self.assertFalse(self.cache.directory.exists());callback.assert_not_called()
        self.assertEqual(events,['error'])

    def test_publish_is_private_gained_once_and_cues_match_saved_pcm(self):
        record = self.publish(approved=True)
        self.assertEqual(self.cache.directory.stat().st_mode&0o777,0o700)
        self.assertEqual(self.file.stat().st_mode&0o777,0o600)
        with self.cache.load() as asset:
            self.assertEqual(hashlib.sha256(asset.path.read_bytes()).hexdigest(),record['sha256'])
            self.assertEqual(list(asset.cues),wav_cues(asset.path))
            self.assertNotIn('path',asset.public())
            with wave.open(str(self.raw),'rb') as source,wave.open(str(asset.path),'rb') as gained:
                before=source.readframes(source.getnframes());after=gained.readframes(gained.getnframes())
            original=[int.from_bytes(before[n:n+2],'little',signed=True) for n in range(0,len(before),2)]
            reduced=[int.from_bytes(after[n:n+2],'little',signed=True) for n in range(0,len(after),2)]
            self.assertEqual(reduced,[round(sample*.25) for sample in original])
            snapshot=asset.path
        self.assertFalse(snapshot.exists());self.assertTrue(self.file.exists())

    def test_unapproved_requires_exact_sha_approval_and_audition_does_not_approve(self):
        record=self.publish()
        self.assert_code('greeting_unapproved',self.cache.load)
        self.assert_code('greeting_corrupt',lambda:self.cache.approve('0'*64))
        events=[]
        player=self.player(lambda command,**kw:kw['on_start']())
        result=player.play(emit=lambda k,**f:events.append(k),allow_unapproved=True)
        self.assertTrue(result['completed']);self.assertFalse(result['approved'])
        self.assertEqual(events,['prepared','started','ended'])
        self.assert_code('greeting_unapproved',self.cache.load)
        self.cache.approve(record['sha256'])
        with self.cache.load() as asset:self.assertTrue(asset.approved)

    def test_simulated_asset_never_becomes_live_even_after_approval(self):
        record=self.publish(simulated=True)
        self.cache.approve(record['sha256'])
        self.assert_code('greeting_simulated_only',self.cache.load)
        with self.cache.load(simulate=True) as asset:self.assertTrue(asset.simulated)
        cache=make_synthetic_greeting_cache(self.root/'owned-simulation',self.config)
        with cache.load(simulate=True) as asset:self.assertTrue(asset.approved and asset.simulated)

    def test_voice_text_version_gain_or_cues_fingerprint_change_invalidates_asset(self):
        self.publish(approved=True)
        for changes in ({'fish_gain':.3},{'fish_voice_id':'1'*32},{'fish_model':'s2.1-pro'},
                        {'fish_name_mode':'literal'},{'fish_delivery':'plain'}):
            config=replace(self.config,**changes)
            other=GreetingCache(self.cache.directory,GreetingSpec.from_config(config))
            self.assert_code('greeting_spec_changed',other.load)
        header,wav=self.bundle();header['spec']['version']=True
        self.rewrite(header,wav)
        self.assert_code('greeting_spec_changed',self.cache.load)

    def test_corrupt_wav_hash_and_self_consistent_but_wrong_cues_are_rejected(self):
        self.publish(approved=True)
        original=self.file.read_bytes()
        header,wav=self.bundle()
        self.rewrite(header,wav[:-1]+bytes([wav[-1]^1]))
        self.assert_code('greeting_corrupt',self.cache.load)
        self.file.write_bytes(original)
        header,wav=self.bundle();header['cues']=[{'time':0,'mouth':'wide'}]
        raw=json.dumps(header['cues'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        header['cues_sha256']=hashlib.sha256(raw).hexdigest()
        self.rewrite(header,wav)
        self.assert_code('greeting_corrupt',self.cache.load)

    def test_wrong_permissions_symlink_fifo_and_hardlink_fail_closed(self):
        self.publish(approved=True)
        self.file.chmod(0o644)
        self.assert_code('greeting_unsafe',self.cache.load)
        self.file.chmod(0o600);self.cache.directory.chmod(0o755)
        self.assert_code('greeting_unsafe',self.cache.load)
        self.cache.directory.chmod(0o700)
        saved=self.cache.directory/'saved';self.file.rename(saved)
        self.file.symlink_to(saved)
        self.assert_code('greeting_unsafe',self.cache.load)
        self.file.unlink();os.mkfifo(self.file,0o600)
        self.assert_code('greeting_unsafe',self.cache.load)
        self.file.unlink();os.link(saved,self.file)
        self.assert_code('greeting_unsafe',self.cache.load)
        self.file.unlink();saved.rename(self.file)
        link=self.root/'linked-directory';link.symlink_to(self.cache.directory,target_is_directory=True)
        self.assert_code('greeting_unsafe',lambda:GreetingCache(link,self.spec).load())

    def test_malformed_or_oversized_bundle_is_rejected_without_temp_player(self):
        self.publish(approved=True)
        for payload in (b'',MAGIC+b'{}\n'+b'a'*44,MAGIC+b'{"version":1,"version":1}\n'+b'a'*44):
            self.file.write_bytes(payload)
            self.assert_code('greeting_corrupt',self.cache.load)
        with self.file.open('wb') as output:output.truncate(9*1024*1024)
        self.assert_code('greeting_unsafe',self.cache.load)

    def test_storage_failure_or_cancel_before_commit_preserves_previous_complete_bundle(self):
        self.publish(approved=True);before=self.file.read_bytes()
        with patch('bmo_ai.greeting_cache.os.replace',side_effect=OSError(errno.ENOSPC,'private-secret')):
            self.assert_code('greeting_storage_failed',self.publish)
        self.assertEqual(self.file.read_bytes(),before)
        checks=[0]
        def cancel_at_commit():
            checks[0]+=1
            return checks[0]==3
        with self.assertRaises(TurnCancelled):self.publish(cancel_check=cancel_at_commit)
        self.assertEqual(self.file.read_bytes(),before)
        self.assertEqual(list(self.cache.directory.glob('.greeting.*.tmp')),[])

    def test_concurrent_preparation_cannot_overwrite_reserved_asset(self):
        self.publish(approved=True);before=self.file.read_bytes()
        with self.cache._write_lock():
            self.assert_code('greeting_busy',self.publish)
        self.assertEqual(self.file.read_bytes(),before)

    def test_invalid_or_too_long_wav_is_not_committed(self):
        self.raw.write_bytes(b'not a wav')
        self.assert_code('greeting_corrupt',self.publish)
        self.assertFalse(self.file.exists())
        with wave.open(str(self.raw),'wb') as output:
            output.setparams((1,2,8000,0,'NONE','NONE'));output.writeframes(bytes(11*8000*2))
        self.assert_code('greeting_corrupt',self.publish)
        self.assertFalse(self.file.exists())

    def test_prepare_requires_explicit_live_and_synthesizes_once_without_playing_or_approving(self):
        generated=[]
        def synthesize(text,**kwargs):
            self.assertEqual(text,self.spec.spoken)
            self.assertTrue(text.endswith('Oh! You\'re back! Beemo missed you!'))
            self.assertEqual(kwargs['reference_id'],self.config.fish_voice_id)
            self.assertEqual(kwargs['model'],self.config.fish_model)
            self.assertEqual(kwargs['api_key'],'fixture-only')
            value=synthetic_wav();generated.append(value);return value
        callback=Mock(side_effect=synthesize)
        self.assert_code('greeting_prepare_denied',lambda:self.cache.prepare(fish_synthesizer=callback))
        callback.assert_not_called()
        result=self.cache.prepare(live_opt_in=True,fish_synthesizer=callback,api_key='fixture-only')
        callback.assert_called_once();self.assertFalse(result['approved'] or result['simulated'])
        self.assertFalse(generated[0].exists())
        self.assert_code('greeting_unapproved',self.cache.load)

    def test_failed_synthesis_or_late_cancel_keeps_previous_and_never_uses_piper(self):
        self.publish(approved=True);before=self.file.read_bytes()
        callback=Mock(side_effect=RuntimeError('private-secret'))
        self.assert_code('greeting_synthesis_failed',lambda:self.cache.prepare(live_opt_in=True,fish_synthesizer=callback))
        cancelled=[False];paths=[]
        def late(*a,**kw):
            path=synthetic_wav();paths.append(path);cancelled[0]=True;return path
        with self.assertRaises(TurnCancelled):
            self.cache.prepare(live_opt_in=True,fish_synthesizer=late,cancel_check=lambda:cancelled[0])
        self.assertFalse(paths[0].exists());self.assertEqual(self.file.read_bytes(),before)

    def test_play_uses_existing_single_python_player_without_gain_or_synthesis(self):
        self.publish(approved=True)
        seen=[];events=[]
        def callback(command,**settings):
            self.assertEqual(command[:2],['aplay','-q'])  # Inspect only; never execute ALSA.
            seen.append(Path(command[-1]));settings['cancel_check']();settings['on_start']()
        with patch('bmo_ai.greeting_cache.attenuated_copy',side_effect=AssertionError('Gain twice forbidden')):
            result=self.player(callback).play(emit=lambda k,**f:events.append((k,f)))
        self.assertTrue(result['completed']);self.assertEqual(len(seen),1)
        self.assertFalse(seen[0].exists());self.assertEqual([k for k,_ in events],['prepared','started','ended'])
        self.assertTrue(all(fields['source']=='cached_greeting' for _,fields in events))
        self.assertTrue(events[0][1]['cues']);self.assertEqual(events[1][1]['emotion'],'happy')

    def test_play_verified_snapshot_is_not_redirected_by_later_bundle_replacement(self):
        record=self.publish(approved=True)
        def callback(command,**settings):
            self.file.write_bytes(b'corrupt after load')
            self.assertEqual(hashlib.sha256(Path(command[-1]).read_bytes()).hexdigest(),record['sha256'])
            settings['on_start']()
        self.player(callback).play(emit=lambda *a,**k:None)
        self.assert_code('greeting_corrupt',self.cache.load)

    def test_greeting_never_changes_dialogue_repeat_cache_or_history(self):
        self.publish(approved=True)
        dialogue=IntegrationVoice(self.config,None,emit=lambda *a,**k:None,simulate=True,
                                 player=lambda command,**kw:kw['on_start']())
        self.addCleanup(dialogue.close)
        dialogue.speak('A synthetic normal answer.')
        original=dialogue._cache;before=hashlib.sha256(original['path'].read_bytes()).hexdigest()
        greeting=self.player(lambda command,**kw:kw['on_start']())
        greeting.play(emit=lambda *a,**k:None)
        self.assertIs(dialogue._cache,original);self.assertTrue(dialogue.has_cache)
        self.assertFalse(greeting.voice.has_cache)
        self.assertEqual(hashlib.sha256(original['path'].read_bytes()).hexdigest(),before)
        self.assertTrue(dialogue.repeat())

    def test_player_failure_or_cancel_keeps_cache_and_cleans_owned_snapshot(self):
        self.publish(approved=True);before=self.file.read_bytes()
        for cancel in (False,True):
            with self.subTest(cancel=cancel):
                events=[];paths=[];player=None
                def callback(command,**settings):
                    paths.append(Path(command[-1]));settings['on_start']()
                    if cancel:
                        player.cancel();settings['cancel_check']()
                    raise audio.AudioPlaybackError('private-secret','device_unavailable')
                player=self.player(callback)
                with self.assertRaises(TurnCancelled if cancel else audio.AudioPlaybackError):
                    player.play(emit=lambda k,**f:events.append(k))
                self.assertEqual(events[-1],'cancelled' if cancel else 'error')
                self.assertNotIn('ended',events);self.assertFalse(paths[0].exists())
                self.assertEqual(self.file.read_bytes(),before)

    def test_busy_and_close_never_create_an_extra_player(self):
        self.publish(approved=True);started,release=Event(),Event();calls=[];errors=[]
        def callback(command,**settings):
            calls.append(command);settings['on_start']();started.set()
            if not release.wait(2):raise AssertionError('Synthetic gate timeout')
            settings['cancel_check']()
        player=self.player(callback)
        def worker():
            try:player.play(emit=lambda *a,**k:None)
            except BaseException as error:errors.append(error)
        thread=Thread(target=worker);thread.start()
        try:
            self.assertTrue(started.wait(1))
            self.assert_code('greeting_player_busy',lambda:player.play(emit=lambda *a,**k:None))
            player.cancel();release.set();thread.join(2)
            self.assertFalse(thread.is_alive());self.assertEqual(len(calls),1)
            self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],TurnCancelled)
            self.assertTrue(player.close())
            self.assert_code('greeting_closed',lambda:player.play(emit=lambda *a,**k:None))
            self.assertTrue(self.file.exists())
        finally:
            release.set();thread.join(2)


if __name__=='__main__':unittest.main()
