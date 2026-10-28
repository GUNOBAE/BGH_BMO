"""Temporary generated PCM only; no real voice, listening approval or provider."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from bmo_ai import audio
from bmo_ai.conversation import TurnCancelled
from bmo_ai.integration_profile import integration_config
from bmo_ai.voice_bank import VoiceBank, VoiceBankError, BankPlayer, catalog, wav_quality
from bmo_ai import voice_bank_cli as cli


PRICES = '| `drama-3` | \\$50.00 / M UTF-8 bytes |\n| `s2.1-pro` | \\$15.00 / M UTF-8 bytes |\n| `s2.1-pro-free` | \\$0.00 / M UTF-8 bytes |'


class CharacterVoiceBankTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='voice-bank-unit-')))
        self.config = integration_config()
        self.now = 1000.
        self.bank = VoiceBank(self.root/'bank', self.config, clock=lambda:self.now,
                              chooser=lambda values:values[0], simulate=True)
        self.forbidden = Mock(side_effect=AssertionError('Real provider/device forbidden'))
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'subprocess.Popen',
                       'bmo_ai.fish_tts.synthesize_wav', 'bmo_ai.cloud.create_client',
                       'bmo_ai.private_credentials.resolve_credentials'):
            self.enterContext(patch(target, self.forbidden))
        self.serial = 0

    def pcm(self, seconds=.4):
        self.serial += 1
        path = self.root/f'fixture-{self.serial}.wav'
        with wave.open(str(path),'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000)
            stream.writeframes(struct.pack('<h',4096)*round(16000*seconds))
        path.chmod(0o600)
        return path

    def approved_fixture(self, clip, model=None):
        row=self.bank.publish(clip,self.pcm(),model=model,simulated=True)
        return self.bank.approve(clip,row['sha256'],model=model,listening_approved=True)

    def pricing(self):
        return cli.PREVIEW_RATES, {'source':cli.PRICING_URL,'checked_utc':datetime.now(timezone.utc).isoformat(),
            'model_support':'DOCUMENTED_NOT_ACCOUNT_TESTED',
            'documents':{url:'0'*64 for url in (cli.PRICING_URL,cli.MODELS_URL,cli.DRAMA_URL,cli.EMOTIONS_URL)}}

    def test_catalog_has_32_original_ungenerated_candidates(self):
        rows=catalog()
        self.assertEqual(len(rows),32)
        self.assertEqual([sum(r['category']==c for r in rows.values()) for c in ('boot','idle','reaction')],[8,12,12])
        self.assertTrue(all(r['status']=='AUDIO_NOT_GENERATED' and not r['approved'] and r['audio_sha256'] is None for r in rows.values()))
        self.assertFalse((self.root/'bank').exists())

    def test_preview_eight_same_aaron_ab_samples_without_io_or_keys(self):
        with patch('bmo_ai.voice_bank._open_directory',self.forbidden):
            report=cli.preview(self.bank)
            output=io.StringIO()
            with redirect_stdout(output): self.assertEqual(cli.main(['--directory',str(self.root/'not-created')]),0)
        self.assertEqual(report['sample_count'],8)
        self.assertEqual({r['model'] for r in report['samples']},{'s2.1-pro-free','drama-3'})
        self.assertTrue(all(r['gain']==.25 and r['input_utf8_bytes']==len(r['spoken'].encode('utf8')) for r in report['samples']))
        self.assertFalse(report['credentials_accessed'] or report['api_calls'] or report['audio_generated'])
        self.assertFalse((self.root/'not-created').exists())

    def test_strong_acting_sample_has_no_fixed_gentle_prefix_conflict(self):
        ordinary=self.bank._spec('boot_01','s2.1-pro-free').spoken
        strong=self.bank._spec('reaction_11','drama-3').spoken
        self.assertIn('gentle, warm',ordinary)
        self.assertIn('Same familiar Aaron character voice',strong)
        self.assertNotIn('gentle, warm',strong)
        self.assertIn('Beemo',self.bank._spec('boot_05','drama-3').spoken)

    def test_missing_clips_never_synthesize_or_claim_approval(self):
        self.assertEqual(self.bank.snapshot()['approved'],0)
        self.assertEqual(self.bank.snapshot()['status'],'AUDIO_NOT_GENERATED')
        self.assertIsNone(self.bank.select('boot'))
        with self.assertRaises(VoiceBankError) as caught:self.bank.load('boot_01')
        self.assertEqual(caught.exception.code,'AUDIO_NOT_GENERATED')
        self.forbidden.assert_not_called()

    def test_gain_once_sha_approval_and_simulated_provenance(self):
        raw=self.pcm();original=raw.read_bytes()
        row=self.bank.publish('boot_01',raw,simulated=True)
        with self.assertRaises(VoiceBankError):self.bank.approve('boot_01',row['sha256'])
        with self.assertRaises(VoiceBankError):self.bank.approve('boot_01','0'*64,listening_approved=True)
        self.bank.approve('boot_01',row['sha256'],listening_approved=True)
        self.assertEqual(self.bank.inventory()[0]['status'],'SIMULATED_ONLY')
        with self.bank.load('boot_01',simulate=True) as asset:
            self.assertAlmostEqual(asset.info['duration_seconds'],.4)
            self.assertAlmostEqual(asset.public()['quality']['peak'],.03125)
            self.assertTrue(asset.cues)
            snapshot=asset.path
        self.assertFalse(snapshot.exists())
        self.assertEqual(raw.read_bytes(),original)
        live=VoiceBank(self.root/'bank',self.config)
        self.assertIsNone(live.select('boot'))
        with self.assertRaises(VoiceBankError) as caught:live.load('boot_01')
        self.assertEqual(caught.exception.code,'SIMULATED_ONLY')

    def test_publish_never_overwrites_existing_candidate(self):
        first=self.bank.publish('boot_01',self.pcm(),simulated=True)
        with self.assertRaises(VoiceBankError) as caught:self.bank.publish('boot_01',self.pcm(),simulated=True)
        self.assertEqual(caught.exception.code,'voice_bank_asset_exists')
        self.assertEqual(self.bank.inventory()[0]['variants']['s2.1-pro-free']['sha256'],first['sha256'])

    def test_corrupt_bundle_refuses_runtime_and_stays_invalid(self):
        self.approved_fixture('boot_01')
        cache=self.root/'bank/s2.1-pro-free/boot_01/greeting.cache'
        cache.write_bytes(cache.read_bytes()[:-1]+b'X')
        self.assertEqual(self.bank.inventory(refresh=True)[0]['status'],'ASSET_INVALID')
        with self.assertRaises(VoiceBankError):self.bank.load('boot_01',simulate=True)

    def test_duration_limit_applies_before_cache_publication(self):
        with self.assertRaises(VoiceBankError) as caught:self.bank.publish('reaction_01',self.pcm(3.1),simulated=True)
        self.assertEqual(caught.exception.code,'voice_bank_duration_invalid')
        self.assertFalse((self.root/'bank').exists())

    def test_recent_three_boots_persist_across_bank_instances(self):
        for clip in ('boot_01','boot_02','boot_03','boot_04'):self.approved_fixture(clip)
        for clip in ('boot_01','boot_02','boot_03'):
            self.assertEqual(self.bank.select('boot'),clip);self.assertTrue(self.bank.completed(clip))
        other=VoiceBank(self.root/'bank',self.config,clock=lambda:self.now,chooser=lambda v:v[0],simulate=True)
        self.assertEqual(other.select('boot'),'boot_04')
        self.assertEqual(len(list((self.root/'bank').glob('.selection.*'))),0)
        self.assertEqual((self.root/'bank/selection.json').stat().st_mode&0o777,0o600)

    def test_idle_emotion_context_cooldown_and_distress_silence(self):
        for clip in ('idle_01','idle_04','idle_10'):self.approved_fixture(clip)
        self.assertEqual(self.bank.select('idle',emotion='sad'),'idle_10')
        self.assertIsNone(self.bank.select('idle',emotion='happy',context='distress'))
        self.assertIsNone(self.bank.select('idle',emotion='crying'))
        self.assertIsNone(self.bank.select('idle',context='quiet'))
        self.bank.completed('idle_10');self.assertIsNone(self.bank.select('idle',emotion='sad'))
        self.now+=1801;self.assertEqual(self.bank.select('idle',emotion='sad'),'idle_10')

    def test_damaged_history_is_silent_not_overwritten(self):
        self.approved_fixture('boot_01')
        path=self.root/'bank/selection.json';path.write_text('{}');path.chmod(0o600)
        self.assertIsNone(self.bank.select('boot'))
        self.assertEqual(path.read_text(),'{}')

    def test_symlinked_bank_cannot_load_or_publish(self):
        target=self.root/'elsewhere';target.mkdir(mode=0o700)
        link=self.root/'linked';link.symlink_to(target,target_is_directory=True)
        bank=VoiceBank(link,self.config)
        with self.assertRaises(VoiceBankError):bank.publish('boot_01',self.pcm(),simulated=True)
        self.assertEqual(list(target.iterdir()),[])

    def test_import_rejects_source_symlink_fifo_and_nonprivate_file(self):
        raw=self.pcm();link=self.root/'source-link.wav';link.symlink_to(raw)
        fifo=self.root/'source-fifo.wav';os.mkfifo(fifo,0o600)
        insecure=self.pcm();insecure.chmod(0o644)
        for source in (link,fifo,insecure):
            with self.subTest(source=source.name),self.assertRaises(VoiceBankError):
                self.bank.publish('boot_01',source,simulated=True)
        self.assertFalse((self.root/'bank').exists())

    def test_player_uses_same_play_boundary_without_answer_cache(self):
        self.approved_fixture('idle_01');events=[];calls=[]
        def player(command,*,on_start,cancel_check,**kwargs):
            calls.append(command);cancel_check();on_start();cancel_check()
        voice=BankPlayer(self.config,self.bank,simulate=True,player=player)
        try:
            result=voice.play('idle_01',emit=lambda kind,**f:events.append((kind,f.get('source'))))
            self.assertTrue(result['completed'])
            self.assertEqual(len(calls),1)
            self.assertEqual([k for k,_ in events],['prepared','started','ended'])
            self.assertFalse(voice.voice.has_cache)
            self.assertEqual(events[1][1],'voice_bank_idle')
        finally:voice.close()

    def test_player_cancel_does_not_mark_heard_or_write_repeat_cache(self):
        self.approved_fixture('boot_01');voice=None
        def player(command,*,on_start,cancel_check,**kwargs):
            on_start();voice.cancel();cancel_check()
        voice=BankPlayer(self.config,self.bank,simulate=True,player=player)
        try:
            with self.assertRaises(TurnCancelled):voice.play('boot_01',emit=lambda *_a,**_kw:None)
            self.assertFalse(voice.voice.has_cache)
            self.assertEqual(list(self.bank._recent),[])
        finally:voice.close()

    def test_official_markdown_price_parser_and_public_document_receipt(self):
        rates=cli.pricing_from_official_text(PRICES)
        self.assertEqual(rates['drama-3'],Decimal('50'))
        for invalid in ('',PRICES.replace('drama-3','other-model'),PRICES+'\n| drama-3 | $60 / M UTF-8 bytes |'):
            with self.assertRaises(VoiceBankError):cli.pricing_from_official_text(invalid)
        texts={cli.PRICING_MARKDOWN_URL:PRICES,
            cli.MODELS_URL+'.md':'s2.1-pro-free s2.1-pro same model [bracket]',
            cli.DRAMA_URL+'.md':'model: drama-3 POST /v1/tts square brackets',
            cli.EMOTIONS_URL+'.md':'S2 [angry] [sobbing] natural language'}
        fetcher=Mock(side_effect=lambda url,**_kw:io.BytesIO(texts[url].encode()))
        prices,receipt=cli.verify_current_pricing(fetcher=fetcher)
        self.assertEqual(prices['s2.1-pro'],15)
        self.assertEqual(receipt['source'],cli.PRICING_URL)
        self.assertEqual(set(receipt['documents']),{cli.PRICING_URL,cli.MODELS_URL,cli.DRAMA_URL,cli.EMOTIONS_URL})
        self.assertEqual(fetcher.call_count,4)
        texts[cli.DRAMA_URL+'.md']='changed support documentation'
        with self.assertRaises(VoiceBankError) as caught:cli.verify_current_pricing(fetcher=fetcher)
        self.assertEqual(caught.exception.code,'voice_bank_model_support_unverified')

    def test_generate_requires_explicit_intent_budget_and_fresh_prices_before_keys(self):
        credentials=Mock(side_effect=AssertionError('Keys must remain unread'))
        for settings,code in (({'explicit_generate':False,'spend_cap_usd':'1'},'voice_bank_generation_not_authorized'),
                              ({'explicit_generate':True,'spend_cap_usd':'nan'},'voice_bank_spend_cap_invalid'),
                              ({'explicit_generate':True,'spend_cap_usd':'.000001'},'voice_bank_spend_cap_exceeded')):
            with self.subTest(settings=settings),self.assertRaises(VoiceBankError) as caught:
                cli.generate(self.bank,credential_resolver=credentials,pricing_verifier=self.pricing,**settings)
            self.assertEqual(caught.exception.code,code)
        stale=lambda:(cli.PREVIEW_RATES,{'source':cli.PRICING_URL,'checked_utc':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()})
        with self.assertRaises(VoiceBankError):cli.generate(self.bank,explicit_generate=True,spend_cap_usd='1',credential_resolver=credentials,pricing_verifier=stale)
        credentials.assert_not_called();self.assertFalse((self.root/'bank').exists())

    def test_generation_calls_exact_model_once_each_and_keeps_unapproved(self):
        calls=[]
        def synthesize(text,**kwargs):calls.append((text,kwargs));return self.pcm()
        report=cli.generate(self.bank,clip_ids=('reaction_11',),models=('s2.1-pro-free','drama-3'),
            explicit_generate=True,spend_cap_usd='1',pricing_verifier=self.pricing,
            credential_resolver=lambda:SimpleNamespace(fish_api_key='test-only-not-a-real-key'),synthesizer=synthesize)
        self.assertEqual([c[1]['model'] for c in calls],['s2.1-pro-free','drama-3'])
        self.assertEqual(report['status'],'GENERATED_UNAPPROVED')
        self.assertFalse(report['listening_approved'])
        self.assertTrue(all(r['status']=='LISTENING_NOT_APPROVED' for r in report['results']))
        self.assertIsNone(self.bank.select('reaction',emotion='crying',context='acting'))
        self.assertFalse(any(self.root.glob('fixture-*.wav')))

    def test_failed_generation_never_retries_or_leaks_provider_exception(self):
        synth=Mock(side_effect=RuntimeError('private provider payload'))
        with self.assertRaises(VoiceBankError) as caught:
            cli.generate(self.bank,clip_ids=('boot_01',),models=('drama-3',),explicit_generate=True,
                spend_cap_usd='1',pricing_verifier=self.pricing,
                credential_resolver=lambda:SimpleNamespace(fish_api_key='test-only'),synthesizer=synth)
        self.assertEqual(synth.call_count,1)
        self.assertNotIn('private',str(caught.exception))


if __name__=='__main__':unittest.main()
