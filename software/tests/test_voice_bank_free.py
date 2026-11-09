"""USD-zero gates with temporary PCM, injected accounts and no real requests."""
from contextlib import redirect_stdout
from datetime import datetime,timezone
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from urllib.error import HTTPError

from bmo_ai import audio,fish_tts,voice_bank_cli as cli
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import synthetic_wav
from bmo_ai.voice_bank import VoiceBank,VoiceBankError
from bmo_ai.voice_bank_free import FreeRequestBudget,OPENAPI_URL,verify_free_account,verify_free_contract


class FreeVoiceBankTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory(prefix="bmo-free-bank-unit-")))
        self.bank=VoiceBank(self.root/"bank",integration_config())
        self.budget=self.root/"global-budget"
        self.calls=[]
        self.credentials=Mock(return_value=SimpleNamespace(fish_api_key="unit-not-real"))
        for target in ("socket.socket.connect","socket.socket.connect_ex","subprocess.Popen",
                       "bmo_ai.fish_tts.urlopen","bmo_ai.private_credentials.resolve_credentials"):
            self.enterContext(patch(target,side_effect=AssertionError("actual operation forbidden")))

    def prices(self):
        return cli.PREVIEW_RATES,dict(source=cli.PRICING_URL,checked_utc=datetime.now(timezone.utc).isoformat(),
            model_support="DOCUMENTED_NOT_ACCOUNT_TESTED",
            documents={url:"0"*64 for url in (cli.PRICING_URL,cli.MODELS_URL,cli.DRAMA_URL,cli.EMOTIONS_URL)})

    def contract(self):
        return dict(source=OPENAPI_URL,sha256="0"*64,checked_utc=datetime.now(timezone.utc).isoformat(),
                    exact_model="s2.1-pro-free",paid_default="s2.1-pro",metadata_gets_documented=True)

    def account(self,_key,*,budget):
        for key in ("account_wallet","account_aaron"):
            budget.reserve(key,kind="METADATA_GET");budget.finish(success=True)
        return dict(status="AUTHENTICATED_AARON_VISIBLE",metadata_gets=2)

    def synth(self,text,**kwargs):
        self.calls.append((text,kwargs));return synthetic_wav()

    def generate(self,**changes):
        options=dict(clip_ids=("boot_01",),models=("s2.1-pro-free",),spend_cap_usd="0",explicit_generate=True,
            free_only=True,credential_resolver=self.credentials,pricing_verifier=self.prices,
            free_contract_verifier=self.contract,account_verifier=self.account,
            budget_directory=self.budget,synthesizer=self.synth)
        options.update(changes)
        return cli.generate(self.bank,**options)

    def test_default_free_preview_has_six_authored_clips_zero_network_key_or_directory_io(self):
        with patch("bmo_ai.voice_bank._open_directory",side_effect=AssertionError("preview must not touch cache")),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["--free-only","--directory",str(self.root/"missing")]),0)
        report=json.loads(output.getvalue())
        self.assertEqual(report["sample_count"],6)
        self.assertEqual({s["model"] for s in report["samples"]},{"s2.1-pro-free"})
        self.assertEqual(Decimal(report["estimated_usd"]),0)
        self.assertFalse(report["credentials_accessed"] or report["api_calls"])
        self.assertFalse((self.root/"missing").exists())

    def test_zero_cap_is_exclusively_explicit_free_mode_and_positive_paid_mode_stays_separate(self):
        for settings in (dict(free_only=False),dict(models=("drama-3",)),dict(models=("s2.1-pro",)),dict(spend_cap_usd="1")):
            with self.subTest(settings=settings),self.assertRaises(VoiceBankError):self.generate(**settings)
        self.credentials.assert_not_called();self.assertEqual(self.calls,[])
        self.assertFalse(self.budget.exists())

    def test_nonzero_current_free_price_and_changed_contract_block_before_credentials(self):
        def changed_price():
            rates,receipt=self.prices();return dict(rates,**{"s2.1-pro-free":Decimal("1")}),receipt
        for setting in (dict(pricing_verifier=changed_price),dict(free_contract_verifier=lambda:dict(self.contract(),exact_model="drama-3"))):
            with self.subTest(setting=tuple(setting)),self.assertRaises(VoiceBankError):self.generate(**setting)
        self.credentials.assert_not_called();self.assertEqual(self.calls,[])

    def test_two_metadata_gets_and_six_tts_exhaust_one_global_eight_request_budget(self):
        seen=[]
        result=self.generate(clip_ids=cli.FREE_CLIPS,on_free_account_ready=lambda account,budget:seen.append(budget))
        self.assertEqual(seen[0]["requests_reserved"],2)
        self.assertEqual(len(self.calls),6)
        self.assertTrue(all(c[1]["model"]=="s2.1-pro-free" and c[1]["free_only"] is True for c in self.calls))
        self.assertEqual(result["request_budget"]["requests_reserved"],8)
        self.assertFalse(result["audio_played"] or result["listening_approved"])
        self.assertEqual(self.bank.snapshot()["approved"],0)
        self.assertTrue(all(r["status"]=="LISTENING_NOT_APPROVED" for r in result["results"]))
        self.bank=VoiceBank(self.root/"another-bank",integration_config())
        self.credentials.reset_mock()
        with self.assertRaises(VoiceBankError) as caught:self.generate(clip_ids=("idle_01",))
        self.assertEqual(caught.exception.code,"voice_bank_free_budget_exhausted")
        self.credentials.assert_not_called();self.assertEqual(len(self.calls),6)
        self.assertEqual((self.budget/"requests.json").stat().st_mode&0o777,0o600)

    def test_permission_or_rate_error_counts_once_and_blocks_every_later_batch(self):
        for code in ("fish_permission","fish_balance","fish_rate_limit"):
            with self.subTest(code=code):
                self.budget=self.root/code
                failed=Mock(side_effect=audio.AudioSynthesisError("private body not printable",code))
                with self.assertRaises(VoiceBankError) as caught:self.generate(synthesizer=failed)
                self.assertEqual(caught.exception.code,code)
                self.assertNotIn("private",str(caught.exception));self.assertEqual(failed.call_count,1)
                value=json.loads((self.budget/"requests.json").read_text())
                self.assertTrue(value["blocked"]);self.assertEqual(len(value["attempts"]),3)
                with self.assertRaises(VoiceBankError) as later:self.generate(synthesizer=failed)
                self.assertEqual(later.exception.code,"voice_bank_free_budget_blocked")
                self.assertEqual(failed.call_count,1)

    def test_interrupted_reservation_is_durable_and_never_reissued(self):
        with FreeRequestBudget(self.budget).session(1) as budget:
            budget.reserve("boot_01")
        with self.assertRaises(VoiceBankError) as caught:
            with FreeRequestBudget(self.budget).session(1):self.fail("must remain blocked")
        self.assertEqual(caught.exception.code,"voice_bank_free_budget_blocked")

    def test_first_metadata_permission_error_stops_before_second_get_or_tts(self):
        opener=Mock(side_effect=HTTPError("https://api.fish.audio/wallet/self/api-credit",403,"private",None,None))
        def verify(key,*,budget):return verify_free_account(key,budget=budget,opener=opener)
        with self.assertRaises(VoiceBankError) as caught:self.generate(account_verifier=verify)
        self.assertEqual(caught.exception.code,"fish_permission");self.assertEqual(opener.call_count,1)
        value=json.loads((self.budget/"requests.json").read_text())
        self.assertEqual(len(value["attempts"]),1);self.assertTrue(value["blocked"])
        self.assertEqual(self.calls,[])

    def test_account_get_results_are_sanitized_and_exact_aaron_is_required(self):
        rows=[dict(credit="PRIVATE_ACCOUNT_BALANCE",user_id="PRIVATE_ID"),
            dict(_id=fish_tts.AARON_BMO_VOICE_ID,type="tts",state="trained",author={"name":"PRIVATE_OWNER"})]
        opener=Mock(side_effect=lambda *_a,**_kw:io.BytesIO(json.dumps(rows.pop(0)).encode()))
        with FreeRequestBudget(self.budget).session(2) as budget:
            result=verify_free_account("UNIT_KEY",opener=opener,budget=budget)
            self.assertEqual(budget.snapshot()["requests_reserved"],2)
        self.assertNotIn("PRIVATE",json.dumps(result));self.assertNotIn("UNIT_KEY",json.dumps(result))
        self.assertEqual([c.args[0].method for c in opener.call_args_list],["GET","GET"])

    def test_official_contract_checks_exact_header_default_and_both_readonly_endpoints(self):
        data=dict(paths={"/v1/tts":{"post":{"parameters":[dict(name="model",**{"in":"header"},schema=dict(enum=["s2.1-pro-free"],default="s2.1-pro"))]}},
                         "/wallet/{user_id}/api-credit":{"get":{}},"/model/{id}":{"get":{}}})
        fetcher=Mock(side_effect=lambda *_a,**_kw:io.BytesIO(json.dumps(data).encode()))
        result=verify_free_contract(fetcher=fetcher)
        self.assertEqual(result["exact_model"],"s2.1-pro-free")
        data["paths"]["/v1/tts"]["post"]["parameters"][0]["schema"]["enum"]=["s2.1-pro"]
        with self.assertRaises(VoiceBankError):verify_free_contract(fetcher=fetcher)

    def test_transport_exact_free_header_and_redirect_refusal_without_paid_fallback(self):
        for model in ("s2.1-pro","drama-3"):
            with self.assertRaises(audio.AudioValidationError):fish_tts.synthesize_wav("Authored text.",model=model,api_key="UNIT_KEY",free_only=True)
        raw=synthetic_wav()
        try:payload=raw.read_bytes()
        finally:audio._discard(raw,suppress=True)
        opener=Mock();opener.open.return_value=io.BytesIO(payload)
        with patch("bmo_ai.fish_tts.build_opener",return_value=opener):
            path=fish_tts.synthesize_wav("Authored text.",api_key="UNIT_KEY",free_only=True)
        self.addCleanup(audio._discard,path,suppress=True)
        request=opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Model"),"s2.1-pro-free")
        self.assertEqual(json.loads(request.data)["reference_id"],fish_tts.AARON_BMO_VOICE_ID)
        with self.assertRaises(audio.AudioSynthesisError) as error:fish_tts._NoFreeRedirect().redirect_request(None,None,None,None,None,None)
        self.assertEqual(error.exception.code,"fish_redirect")


if __name__=="__main__":unittest.main()
