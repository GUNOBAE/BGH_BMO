"""Actual WAV-to-profile preparation, exact source binding and runtime ownership."""
from array import array
from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from bmo_ai import media_assets
from bmo_ai.daily_preferences import DailyPreferences
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.media import FakeMediaBackend, MediaFeature
from bmo_ai.features.media.library import review_wav,install_wav,load_library
from bmo_ai.features.media.singing_analysis import prepare_profile,load_profile,from_envelopes,ARRAYS


class SingingAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory(prefix='bmo-singing-')))
        self.content=self.root/'music'
        self.source=self.root/'source.wav'
        rate=8000
        self.samples=array('h',(int(math.sin(i*.12)*18000*(.3+.7*((i//1600)%3)/2)) for i in range(rate*8)))
        with wave.open(str(self.source),'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(rate);wav.writeframes(self.samples.tobytes())
        review=review_wav(self.source)
        install_wav(review,self.content,'song',title='Generated fixture',license_id='generated',license_reviewed=True)
        self.asset=load_library(self.content)[0]
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('No network')))

    def prepare(self):
        return prepare_profile(self.content,self.asset)

    def feature(self):
        return MediaFeature(self.content,(self.asset,),FakeMediaBackend(pausable=True))

    def test_real_samples_bound_to_actual_hash_create_once_without_audio(self):
        before={p.name:p.read_bytes() for p in self.content.iterdir()}
        with patch('subprocess.Popen',side_effect=AssertionError('No output player')):
            result=self.prepare()
            second=self.prepare()
        self.assertFalse(result['reused']);self.assertTrue(second['reused'])
        profile,raw,digest=load_profile(self.content,self.asset)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),result['profile_sha256'])
        self.assertEqual(profile['audio_sha256'],self.asset.sha256)
        self.assertEqual(profile['analyzer_version'],'1.0.0')
        self.assertEqual(len(profile['level']),400)
        self.assertGreater(max(profile['level'])-min(profile['level']),.3)
        for name,original in before.items(): self.assertEqual((self.content/name).read_bytes(),original)
        cache=next(self.content.glob('.singing-*.json'))
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode),0o600)
        self.assertEqual(cache.stat().st_nlink,1)
        self.assertNotIn(str(self.root),raw.decode())

    def test_missing_profile_does_not_disable_music_and_never_analyses_on_start(self):
        with patch('bmo_ai.features.media.singing_analysis.prepare_profile',side_effect=AssertionError('runtime analysis')):
            feature=self.feature()
            self.addCleanup(feature.close)
            self.assertEqual(feature.catalog()[0].readiness,'READY')
            feature.start('song',request_id='music:one',mode_generation=2)
            self.assertEqual(feature.snapshot()['singing_profile']['status'],'missing')
            with self.assertRaises(FeatureError):
                feature.singing_profile_bytes(request_id='music:one',mode_generation=2,playback_generation=1)

    def test_request_and_generation_fences_pause_seek_replay_end(self):
        self.prepare();feature=self.feature();self.addCleanup(feature.close)
        feature.start('song',request_id='music:one',mode_generation=2)
        identity=dict(request_id='music:one',mode_generation=2,playback_generation=1)
        raw=feature.singing_profile_bytes(**identity)
        metadata=feature.snapshot()['singing_profile']
        self.assertEqual(metadata['profile_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(metadata['url'],'/api/music-singing/music:one_1?mode_generation=2')
        feature.pause();self.assertEqual(raw,feature.singing_profile_bytes(**identity))
        feature.seek(1.)
        with self.assertRaises(FeatureError):feature.singing_profile_bytes(**identity)
        identity['playback_generation']=2
        self.assertEqual(raw,feature.singing_profile_bytes(**identity))
        feature.replay();identity['playback_generation']=3
        self.assertEqual(raw,feature.singing_profile_bytes(**identity))
        feature.stop('user')
        with self.assertRaises(FeatureError):feature.singing_profile_bytes(**identity)
        feature.start('song',request_id='music:two',mode_generation=3)
        with self.assertRaises(FeatureError):feature.singing_profile_bytes(**identity)

    def test_mood_is_explicit_hash_bound_and_changes_no_audio_or_clock(self):
        feature=self.feature();self.addCleanup(feature.close)
        self.assertEqual(feature.singing_mood('song'),'joyful')
        feature.start('song',request_id='music:one',mode_generation=2);feature.pause();feature.seek(2.)
        before=feature.snapshot()
        values=feature.set_singing_mood('song','gentle')
        after=feature.snapshot()
        for key in ('request_id','playback_generation','position_seconds','state','output_started'):
            self.assertEqual(before[key],after[key])
        store=DailyPreferences(self.root/'prefs')
        self.assertTrue(store.update(singing_moods=values))
        restarted=self.feature();self.addCleanup(restarted.close)
        restarted.load_singing_moods(DailyPreferences(self.root/'prefs').load()['singing_moods'])
        self.assertEqual(restarted.singing_mood('song'),'gentle')
        values['song']['audio_sha256']='0'*64
        restarted.load_singing_moods(values)
        self.assertEqual(restarted.singing_mood('song'),'joyful')
        with self.assertRaises(FeatureError):feature.set_singing_mood('song','sad')
        with self.assertRaises(FeatureError):feature.set_singing_mood('unknown','joyful')
        self.assertFalse(store.update(singing_moods={'song':{'audio_sha256':'bad','mood':'gentle'}}))

    def test_hash_mismatch_invalid_cache_symlink_and_unknown_data_are_rejected(self):
        self.prepare();cache=next(self.content.glob('.singing-*.json'));original=cache.read_bytes()
        invalid=json.loads(original);invalid['audio_sha256']='0'*64
        cache.write_text(json.dumps(invalid))
        with self.assertRaises(FeatureError):self.prepare()
        feature=self.feature();self.addCleanup(feature.close)
        feature.start('song',request_id='music:one',mode_generation=2)
        self.assertEqual(feature.snapshot()['singing_profile']['status'],'invalid')
        cache.write_bytes(original);invalid=json.loads(original);invalid['level'][0]=float('nan')
        cache.write_text(json.dumps(invalid))
        with self.assertRaises(FeatureError):load_profile(self.content,self.asset)
        cache.write_bytes(original);invalid=json.loads(original);invalid['private_extra']='untrusted'
        cache.write_text(json.dumps(invalid))
        with self.assertRaises(FeatureError):load_profile(self.content,self.asset)
        cache.unlink();cache.symlink_to(self.source)
        with self.assertRaises(FeatureError):self.prepare()
        self.assertTrue(cache.is_symlink())

    def test_changed_audio_and_time_duration_limits_leave_no_cache(self):
        with self.assertRaises(FeatureError):prepare_profile(self.content,replace(self.asset,sha256='0'*64))
        with self.assertRaises(FeatureError):prepare_profile(self.content,replace(self.asset,duration=601))
        ticks=iter([0.,31.])
        with self.assertRaises(FeatureError) as caught:prepare_profile(self.content,self.asset,clock=lambda:next(ticks))
        self.assertEqual(caught.exception.code,'singing_analysis_timeout')
        self.assertEqual(list(self.content.glob('.singing-*.json')),[])
        self.assertEqual(list(self.content.glob('.media-stage-*')),[])

    def test_cli_prepares_existing_library_without_reimport_or_output(self):
        output=io.StringIO()
        with redirect_stdout(output),patch('subprocess.Popen',side_effect=AssertionError('No output')):
            result=media_assets.main(['--prepare-singing','--content-root',str(self.content)])
        self.assertEqual(result,0)
        report=json.loads(output.getvalue())
        self.assertEqual(report['status'],'READY');self.assertTrue(report['audio_preserved'])
        self.assertEqual(report['playback'],'NOT_RUN')
        self.assertEqual(report['items'][0]['audio_sha256'],self.asset.sha256)

    @unittest.skipUnless(shutil.which('node'),'node required for supplied analyzer numeric comparison')
    def test_numeric_profile_matches_supplied_analyzer_for_real_pcm(self):
        self.prepare();profile,raw,digest=load_profile(self.content,self.asset)
        source=Path(__file__).parents[1]/'bmo_ai/desktop_web/bmo_music_analysis.js'
        if not source.exists(): self.skipTest('frontend analyzer asset not installed yet')
        code="const fs=require('fs'),a=require(process.argv[1]),v=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(a.analysePCM([v],8000),(k,v)=>ArrayBuffer.isView(v)?Array.from(v):v));"
        result=subprocess.run(['node','-e',code,str(source)],input=json.dumps([x/32768. for x in self.samples]),text=True,capture_output=True,check=True,timeout=10)
        expected=json.loads(result.stdout)
        for key in ARRAYS:
            self.assertEqual(len(profile[key]),len(expected[key]))
            self.assertLess(max(abs(a-b) for a,b in zip(profile[key],expected[key])),1e-5,key)
        self.assertEqual(profile['summary']['bpm'],expected['summary']['bpm'])
        self.assertAlmostEqual(profile['summary']['confidence'],expected['summary']['confidence'],places=5)

    def test_stereo_opposite_phase_keeps_whole_mix_amplitude(self):
        stereo=self.root/'stereo.wav'
        samples=array('h')
        for value in self.samples:
            samples.extend((value,-value))
        with wave.open(str(stereo),'wb') as wav:
            wav.setnchannels(2);wav.setsampwidth(2);wav.setframerate(8000);wav.writeframes(samples.tobytes())
        review=review_wav(stereo)
        install_wav(review,self.content,'stereo',title='Opposite generated channels',license_id='generated',license_reviewed=True)
        asset=next(x for x in load_library(self.content) if x.id=='stereo')
        prepare_profile(self.content,asset)
        profile,raw,digest=load_profile(self.content,asset)
        self.assertGreater(max(profile['level']),.9)
        self.assertGreater(max(profile['onset']),.5)

    def test_silence_and_constant_energy_do_not_fabricate_onsets(self):
        for value in (0.,.5):
            profile=from_envelopes(array('f',[value])*500,array('f',[value])*500,array('f',[value])*500)
            self.assertEqual(max(profile['onset']),0.)
            self.assertIsNone(profile['summary']['bpm'])
            if value==0:self.assertEqual(max(profile['level']),0.)


if __name__=='__main__':unittest.main()
