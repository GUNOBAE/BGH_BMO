"""Preview bounded same-Aaron voice samples; generating/playing are explicit.

Default preview does not read keys, create cache directories or contact Fish.
Explicit generation rechecks the official pricing document before key access,
checks a user spend ceiling, generates each sample once, and never approves it.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import re
from urllib.request import urlopen

from . import audio, fish_tts
from .conversation import TurnCancelled
from .integration_profile import integration_config
from .voice_bank import VoiceBank, VoiceBankError, BankPlayer, MODELS, catalog, default_voice_bank_directory

PRICING_URL = "https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits"
PRICING_MARKDOWN_URL = PRICING_URL + ".md"
MODELS_URL = "https://docs.fish.audio/developer-guide/models-pricing/models-overview"
DRAMA_URL = "https://docs.fish.audio/features/text-to-speech/drama-3"
EMOTIONS_URL = "https://docs.fish.audio/developer-guide/core-features/emotions"
VERIFIED_UTC = "2026-10-28T00:00:00+00:00"
# List-price ceiling for a preview, not a promise about a later invoice. The
# documented temporary Drama3 promotion never reduces the generation ceiling.
PREVIEW_RATES = {"s2.1-pro-free": Decimal("0"), "s2.1-pro": Decimal("15"), "drama-3": Decimal("50")}
DEFAULT_CLIPS = ("boot_01", "boot_05", "reaction_07", "reaction_11")
DEFAULT_MODELS = ("s2.1-pro-free", "drama-3")
MAX_SAMPLES = 8
FREE_CLIPS = ("boot_01", "boot_05", "reaction_03", "reaction_07", "reaction_08", "reaction_11")


def pricing_from_official_text(text):
    """Read exact model table rows, refusing ambiguous/missing current prices."""
    if type(text) is not str or not 0 < len(text) <= 2_000_000:
        raise VoiceBankError("voice_bank_pricing_unverified")
    text = text.replace("\\$", "$")  # Mintlify's official Markdown escapes dollars.
    result = {}
    for model in MODELS:
        pattern = r"\|\s*`?" + re.escape(model) + r"`?\s*\|\s*\$([0-9]+(?:\.[0-9]+)?)\s*/\s*M\s+UTF-8\s+bytes\s*\|?"
        values = {Decimal(m) for m in re.findall(pattern, text, re.IGNORECASE)}
        if len(values) != 1:
            raise VoiceBankError("voice_bank_pricing_unverified")
        price, = values
        if not price.is_finite() or not 0 <= price <= 1000:
            raise VoiceBankError("voice_bank_pricing_unverified")
        result[model] = price
    return result


def verify_current_pricing(*, fetcher=None, now=None):
    """Recheck public model/control/pricing docs before reading any keys.

    A changed or unreachable contract requires review, rather than silently
    requesting a more expensive fallback model. Audio examples are not fetched.
    """
    fetcher = fetcher or urlopen
    try:
        documents = {}
        for source in (PRICING_URL, MODELS_URL, DRAMA_URL, EMOTIONS_URL):
            with fetcher(source + ".md", timeout=10.) as response:
                raw = response.read(2_000_001)
            if not 0 < len(raw) <= 2_000_000:
                raise ValueError()
            documents[source] = (raw.decode("utf-8"), hashlib.sha256(raw).hexdigest())
        rates = pricing_from_official_text(documents[PRICING_URL][0])
        required = {
            MODELS_URL: ("s2.1-pro-free", "s2.1-pro", "same model", "[bracket]"),
            DRAMA_URL: ("model: drama-3", "post /v1/tts", "square brackets"),
            EMOTIONS_URL: ("s2", "[angry]", "[sobbing]", "natural language"),
        }
        if any(token not in documents[source][0].lower()
               for source, tokens in required.items() for token in tokens):
            raise VoiceBankError("voice_bank_model_support_unverified")
        checked = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        return rates, dict(source=PRICING_URL, checked_utc=checked,
            rates_basis="published_list_price_ceiling", model_support="DOCUMENTED_NOT_ACCOUNT_TESTED",
            documents={source: sha for source, (_, sha) in documents.items()})
    except VoiceBankError:
        raise
    except Exception:
        raise VoiceBankError("voice_bank_pricing_unverified") from None


def preview(bank, *, clip_ids=DEFAULT_CLIPS, models=DEFAULT_MODELS, rates=None, pricing=None):
    rates = PREVIEW_RATES if rates is None else rates
    if (not clip_ids or not models or len(clip_ids) != len(set(clip_ids))
            or len(models) != len(set(models)) or any(m not in MODELS for m in models)
            or len(clip_ids) * len(models) > MAX_SAMPLES):
        raise VoiceBankError("voice_bank_batch_invalid")
    rows = []
    total = Decimal("0")
    for clip_id in clip_ids:
        for model in models:
            spec = bank._spec(clip_id, model)
            size = len(spec.spoken.encode("utf-8"))
            cost = (Decimal(size) * rates[model] / Decimal(1_000_000)).quantize(Decimal("0.000001"), rounding=ROUND_CEILING)
            total += cost
            rows.append(dict(clip_id=clip_id, model=model, voice_id=spec.fish_voice_id,
                text=spec.text, direction=spec.direction, spoken=spec.spoken,
                input_utf8_bytes=size, estimated_usd=str(cost), gain=.25,
                status="AUDIO_NOT_GENERATED", approved=False))
    return dict(status="DRY_RUN", samples=rows, sample_count=len(rows),
        input_utf8_bytes=sum(r["input_utf8_bytes"] for r in rows), estimated_usd=str(total),
        pricing=pricing or dict(source=PRICING_URL, documented_utc=VERIFIED_UTC,
            status="REVERIFY_REQUIRED_BEFORE_GENERATION", rates_basis="published_list_price_ceiling"),
        max_samples=MAX_SAMPLES, retries=0, credentials_accessed=False, api_calls=0,
        audio_generated=False, listening_approved=False)


def generate(bank, *, clip_ids=DEFAULT_CLIPS, models=DEFAULT_MODELS, spend_cap_usd,
             explicit_generate=False, credential_resolver=None, synthesizer=None,
             pricing_verifier=None, cancel_check=None, free_only=False,
             free_contract_verifier=None, account_verifier=None, budget_directory=None,
             on_free_account_ready=None):
    """Finite explicit generation. All metadata/budget checks precede keys/API."""
    if explicit_generate is not True:
        raise VoiceBankError("voice_bank_generation_not_authorized")
    try: cap = Decimal(str(spend_cap_usd))
    except Exception: raise VoiceBankError("voice_bank_spend_cap_invalid") from None
    if (type(free_only) is not bool or not cap.is_finite()
            or (cap != 0 if free_only else cap <= 0)):
        raise VoiceBankError("voice_bank_spend_cap_invalid")
    if free_only and tuple(models) != ("s2.1-pro-free",):
        raise VoiceBankError("voice_bank_free_model_required")
    if free_only and len(clip_ids)+2 > MAX_SAMPLES:
        raise VoiceBankError("voice_bank_free_budget_exhausted")
    # Validate the complete small batch without creating any private directory.
    preview(bank, clip_ids=clip_ids, models=models)
    rates, receipt = (pricing_verifier or verify_current_pricing)()
    try:
        checked = datetime.fromisoformat(receipt["checked_utc"])
        age = (datetime.now(timezone.utc) - checked).total_seconds()
        if (receipt["source"] != PRICING_URL or checked.tzinfo is None or not 0 <= age <= 3600
                or receipt["model_support"] != "DOCUMENTED_NOT_ACCOUNT_TESTED"
                or set(receipt["documents"]) != {PRICING_URL, MODELS_URL, DRAMA_URL, EMOTIONS_URL}
                or any(not re.fullmatch("[a-f0-9]{64}", sha) for sha in receipt["documents"].values())):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise VoiceBankError("voice_bank_pricing_unverified") from None
    contract = None
    if free_only:
        from .voice_bank_free import verify_free_contract, FreeRequestBudget, OPENAPI_URL
        if rates["s2.1-pro-free"] != 0 or checked.date() != datetime.now(timezone.utc).date():
            raise VoiceBankError("voice_bank_free_price_unverified")
        contract = (free_contract_verifier or verify_free_contract)()
        try:
            verified = datetime.fromisoformat(contract["checked_utc"])
            if (contract["source"] != OPENAPI_URL or contract["exact_model"] != "s2.1-pro-free"
                    or contract["paid_default"] != "s2.1-pro"
                    or contract["metadata_gets_documented"] is not True
                    or not re.fullmatch("[a-f0-9]{64}",contract["sha256"])
                    or verified.tzinfo is None or verified.date() != datetime.now(timezone.utc).date()
                    or not 0 <= (datetime.now(timezone.utc)-verified).total_seconds() <= 3600):
                raise ValueError()
        except (KeyError,TypeError,ValueError):
            raise VoiceBankError("voice_bank_free_contract_unverified") from None
    planned = preview(bank, clip_ids=clip_ids, models=models, rates=rates, pricing=receipt)
    if Decimal(planned["estimated_usd"]) > cap:
        raise VoiceBankError("voice_bank_spend_cap_exceeded")
    for row in planned["samples"]:
        if bank.exists(row["clip_id"], row["model"]):
            raise VoiceBankError("voice_bank_asset_exists")
    def check():
        if cancel_check is not None: cancel_check()
    results = []
    budget = FreeRequestBudget(budget_directory) if free_only else None
    account = None
    with (budget.session(2+len(planned["samples"])) if budget is not None else nullcontext()), bank.generation_lock():
        # Another explicit process may have published while pricing was read.
        for row in planned["samples"]:
            if bank.exists(row["clip_id"], row["model"]):
                raise VoiceBankError("voice_bank_asset_exists")
        check()
        if credential_resolver is None:
            from .private_credentials import resolve_credentials
            credentials = resolve_credentials(interactive=False)
        else:
            credentials = credential_resolver()
        api_key = credentials.fish_api_key
        if free_only:
            from .voice_bank_free import verify_free_account
            try:
                account = (account_verifier or verify_free_account)(api_key,budget=budget)
                if type(account) is not dict or account.get("status") != "AUTHENTICATED_AARON_VISIBLE":
                    raise VoiceBankError("voice_bank_account_unverified")
                if on_free_account_ready is not None:
                    on_free_account_ready(account,budget.snapshot())
            except BaseException:
                budget.value["blocked"] = True
                budget._write()
                raise
        for row in planned["samples"]:
            check(); raw = None
            if budget is not None: budget.reserve(row["clip_id"])
            try:
                raw = Path((synthesizer or fish_tts.synthesize_wav)(row["spoken"], model=row["model"],
                    reference_id=fish_tts.AARON_BMO_VOICE_ID, api_key=api_key, cancel_check=check,
                    **({"free_only":True} if free_only else {})))
                check()
                results.append(bank.publish(row["clip_id"], raw, model=row["model"]))
                if budget is not None: budget.finish(success=True)
            except (TurnCancelled, KeyboardInterrupt):
                if budget is not None: budget.finish(success=False,code="cancelled")
                raise
            except Exception as error:
                # Stop at the first failure; never retry paid synthesis. Already
                # completed candidates remain available for separate listening.
                if budget is not None:
                    budget.finish(success=False,code=getattr(error,"code","voice_bank_generation_failed"))
                    raise VoiceBankError(budget.value["attempts"][-1]["code"]) from None
                raise VoiceBankError("voice_bank_generation_failed") from None
            finally:
                if raw is not None: audio._discard(raw, suppress=True)
    report = dict(status="GENERATED_UNAPPROVED", results=results, sample_count=len(results),
                estimated_max_usd=planned["estimated_usd"], spend_cap_usd=str(cap),
                pricing=receipt, api_calls=len(results), retries=0, listening_approved=False)
    if free_only:
        report.update(free_only=True, contract=contract,
                      account=dict(account,free_inference_permission="FREE_TTS_REQUEST_SUCCEEDED",synthesis_requests=len(results)),
                      request_budget=budget.snapshot(),
                      free_inference_permission="FREE_TTS_REQUEST_SUCCEEDED", audio_played=False)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--clip", action="append", dest="clips")
    parser.add_argument("--model", action="append", choices=MODELS, dest="models")
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--free-only", action="store_true", help="explicit USD-zero, exact free model, persistent eight-request ceiling")
    parser.add_argument("--spend-cap-usd")
    parser.add_argument("--approve", metavar="CLIP_ID")
    parser.add_argument("--sha256")
    parser.add_argument("--listening-approved", action="store_true")
    parser.add_argument("--audition", metavar="CLIP_ID")
    parser.add_argument("--supervised-listening", action="store_true")
    parser.add_argument("--speaker-device", default="plughw:CARD=MAX98357A,DEV=0")
    args = parser.parse_args(argv)
    if sum(bool(v) for v in (args.inventory, args.generate, args.approve, args.audition)) > 1:
        parser.error("select one operation")
    if args.free_only and (args.inventory or args.approve or args.audition):
        parser.error("free-only applies to preview or explicit generation only")
    if args.free_only and args.models and tuple(args.models) != ("s2.1-pro-free",):
        parser.error("free-only requires exactly s2.1-pro-free")
    config = integration_config(speaker_device=args.speaker_device)
    bank = VoiceBank(args.directory or default_voice_bank_directory(), config)
    try:
        if args.inventory:
            report = dict(bank.snapshot(), clips=bank.inventory())
        elif args.approve:
            if args.models and len(args.models) != 1: parser.error("approval requires one model")
            report = bank.approve(args.approve, args.sha256, model=args.models[0] if args.models else None,
                                  listening_approved=args.listening_approved)
        elif args.audition:
            if not args.supervised_listening: parser.error("audition requires --supervised-listening")
            if args.models and len(args.models) != 1: parser.error("audition requires one model")
            player = BankPlayer(config, bank)
            try:
                report = player.play(args.audition, emit=lambda *_a, **_kw: None,
                    model=args.models[0] if args.models else None, allow_unapproved=True)
            finally: player.close()
        elif args.generate:
            report = generate(bank, clip_ids=tuple(args.clips or (FREE_CLIPS if args.free_only else DEFAULT_CLIPS)),
                models=tuple(args.models or (("s2.1-pro-free",) if args.free_only else DEFAULT_MODELS)), spend_cap_usd=args.spend_cap_usd,
                explicit_generate=True, free_only=args.free_only)
        else:
            report = preview(bank, clip_ids=tuple(args.clips or (FREE_CLIPS if args.free_only else DEFAULT_CLIPS)),
                models=tuple(args.models or (("s2.1-pro-free",) if args.free_only else DEFAULT_MODELS)))
            if args.free_only:
                report.update(free_only=True,max_tts_samples=6,authenticated_request_limit=8,
                              metadata_gets_reserved_in_limit=2,usd_cap="0")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except VoiceBankError as error:
        print(json.dumps(dict(status="REJECTED", code=error.code, retries=0, automatic_generation=False)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
