"""Explicit USD-zero preparation gates and durable, global request accounting.

No IO on import. Only authored catalog text is eligible for synthesis. Budget
reservation precedes a request, so failures and interrupted requests still count.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from . import fish_tts
from .private_credentials import _open_directory, _private_file, _close, CredentialError
from .voice_bank import VoiceBankError

FREE_MODEL = "s2.1-pro-free"
AUTHORIZATION = "pr40-comment-6476906241"
MAX_REQUESTS = 8
OPENAPI_URL = "https://docs.fish.audio/api-reference/openapi.json"
_SAFE_CODES = frozenset(("fish_authentication", "fish_balance", "fish_permission", "fish_model",
    "fish_request", "fish_rate_limit", "fish_server", "fish_connection", "fish_redirect",
    "audio_download_timeout", "voice_bank_generation_failed", "voice_bank_account_unverified",
    "voice_bank_duration_invalid", "voice_bank_audio_invalid", "cancelled"))


def _now():
    return datetime.now(timezone.utc).isoformat()


def default_budget_directory():
    # Independent of checkout and voice-bank destination; changing either does
    # not restart this authorization's eight-request ceiling.
    return Path.home()/".local/state/bmo/voice-bank-free-v1"/AUTHORIZATION


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise VoiceBankError("fish_redirect")


def verify_free_contract(*, fetcher=None):
    """Public official schema only; proves supported exact header, not account access."""
    try:
        with (fetcher or urlopen)(OPENAPI_URL, timeout=15.) as response:
            raw = response.read(2_000_001)
        if not 0 < len(raw) <= 2_000_000:
            raise ValueError()
        schema = json.loads(raw)
        paths = schema["paths"]
        parameters = paths["/v1/tts"]["post"]["parameters"]
        models = [p for p in parameters if p["in"] == "header" and p["name"] == "model"]
        if (len(models) != 1 or FREE_MODEL not in models[0]["schema"]["enum"]
                or models[0]["schema"]["default"] != "s2.1-pro"
                or "get" not in paths["/wallet/{user_id}/api-credit"]
                or "get" not in paths["/model/{id}"]):
            raise ValueError()
        return dict(source=OPENAPI_URL, sha256=hashlib.sha256(raw).hexdigest(), checked_utc=_now(),
                    exact_model=FREE_MODEL, paid_default="s2.1-pro", metadata_gets_documented=True)
    except Exception:
        raise VoiceBankError("voice_bank_free_contract_unverified") from None


def verify_free_account(api_key, *, opener=None, budget=None):
    """Two documented GETs, no synthesis/upload/balance change or body logging.

    There is no documented metadata field proving this key's free-inference
    entitlement. The first bounded free-only synthesis is that explicit probe.
    """
    if budget is None:
        raise VoiceBankError("voice_bank_free_budget_required")
    opener = opener or build_opener(NoRedirect()).open
    try:
        values = []
        for key,endpoint in (("account_wallet","/wallet/self/api-credit"),
                             ("account_aaron","/model/" + fish_tts.AARON_BMO_VOICE_ID)):
            if budget is not None: budget.reserve(key,kind="METADATA_GET")
            request = Request("https://api.fish.audio"+endpoint,
                headers={"Authorization":"Bearer "+api_key}, method="GET")
            with opener(request, timeout=15.) as response:
                raw = response.read(1_000_001)
            if not 0 < len(raw) <= 1_000_000:
                raise ValueError()
            value=json.loads(raw)
            if (type(value) is not dict
                    or key == "account_wallet" and "credit" not in value
                    or key == "account_aaron" and (value.get("_id") != fish_tts.AARON_BMO_VOICE_ID
                        or value.get("type") != "tts" or value.get("state") != "trained"
                        or value.get("dmca_taken_down") is True)):
                raise ValueError()
            values.append(value)
            if budget is not None: budget.finish(success=True)
        return dict(status="AUTHENTICATED_AARON_VISIBLE", checked_utc=_now(), metadata_gets=2,
                    free_inference_permission="PENDING_FIRST_BOUNDED_PROBE", synthesis_requests=0)
    except Exception as error:
        code = ({401:"fish_authentication",402:"fish_balance",403:"fish_permission",
                404:"fish_model",429:"fish_rate_limit"}.get(error.code,"fish_request") if isinstance(error,HTTPError)
                else error.code if isinstance(error,VoiceBankError)
                else "fish_connection" if isinstance(error,URLError) else "voice_bank_account_unverified")
        if budget is not None and budget.value["attempts"] and budget.value["attempts"][-1]["outcome"] == "reserved":
            budget.finish(success=False,code=code)
        raise VoiceBankError(code) from None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError()
        result[key] = value
    return result


class FreeRequestBudget:
    """Private flock + atomic journal; never retry or erase a reserved request."""
    def __init__(self, directory=None):
        self.path = default_budget_directory() if directory is None else Path(directory)
        if not self.path.is_absolute() or ".." in self.path.parts:
            raise VoiceBankError("voice_bank_free_budget_unsafe")
        self.directory = None
        self.value = None

    def _read(self):
        descriptor = None
        try:
            descriptor = os.open("requests.json",os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=self.directory)
            _private_file(os.fstat(descriptor),size_limit=32768)
            with os.fdopen(descriptor,"rb") as stream:
                descriptor = None
                value = json.loads(stream.read(32769),object_pairs_hook=_unique)
            if (type(value) is not dict or set(value) != {"version","authorization","model","usd_cap","max_requests","blocked","attempts"}
                    or type(value["version"]) is not int or value["version"] != 1
                    or value["authorization"] != AUTHORIZATION or value["model"] != FREE_MODEL
                    or value["usd_cap"] != "0" or value["max_requests"] != MAX_REQUESTS
                    or type(value["blocked"]) is not bool or type(value["attempts"]) is not list
                    or len(value["attempts"]) > MAX_REQUESTS): raise ValueError()
            for number,row in enumerate(value["attempts"],1):
                if (type(row) is not dict or set(row) != {"request","kind","clip_id","reserved_utc","outcome","code"}
                        or type(row["request"]) is not int or row["request"] != number
                        or row["kind"] not in ("TTS","METADATA_GET")
                        or type(row["clip_id"]) is not str
                        or (row["clip_id"] not in ("account_wallet","account_aaron") if row["kind"] == "METADATA_GET"
                            else not re.fullmatch(r"(?:boot|idle|reaction)_[0-9]{2}",row["clip_id"]))
                        or type(row["reserved_utc"]) is not str or len(row["reserved_utc"]) > 50
                        or row["outcome"] not in ("reserved","generated_unapproved","metadata_verified","error")
                        or row["code"] not in ("reserved","ok",*_SAFE_CODES)): raise ValueError()
            return value
        except FileNotFoundError:
            return dict(version=1,authorization=AUTHORIZATION,model=FREE_MODEL,usd_cap="0",
                        max_requests=MAX_REQUESTS,blocked=False,attempts=[])
        finally: _close(descriptor)

    def _write(self):
        name = ".requests."+secrets.token_hex(12)
        descriptor = None
        try:
            raw = json.dumps(self.value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
            descriptor = os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=self.directory)
            with os.fdopen(descriptor,"wb") as stream:
                descriptor = None
                stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.replace(name,"requests.json",src_dir_fd=self.directory,dst_dir_fd=self.directory)
            name = None;os.fsync(self.directory)
        finally:
            _close(descriptor)
            if name is not None:
                try: os.unlink(name,dir_fd=self.directory)
                except OSError: pass

    @contextmanager
    def session(self, planned_requests):
        lock = None
        try:
            self.directory = _open_directory(self.path,create=True)
            lock = os.open(".requests.lock",os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,0o600,dir_fd=self.directory)
            _private_file(os.fstat(lock),size_limit=0)
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise VoiceBankError("voice_bank_free_budget_busy") from None
            self.value = self._read()
            if self.value["blocked"] or any(r["outcome"] not in ("generated_unapproved","metadata_verified") for r in self.value["attempts"]):
                raise VoiceBankError("voice_bank_free_budget_blocked")
            if type(planned_requests) is not int or not 1 <= planned_requests <= MAX_REQUESTS-len(self.value["attempts"]):
                raise VoiceBankError("voice_bank_free_budget_exhausted")
            yield self
        except (CredentialError,OSError,ValueError,TypeError,KeyError):
            raise VoiceBankError("voice_bank_free_budget_unsafe") from None
        finally:
            _close(lock);_close(self.directory)
            self.directory = None

    def reserve(self, clip_id, *, kind="TTS"):
        if (self.value is None or self.directory is None or self.value["blocked"]
                or self.value["attempts"] and self.value["attempts"][-1]["outcome"] == "reserved"
                or len(self.value["attempts"]) >= MAX_REQUESTS):
            raise VoiceBankError("voice_bank_free_budget_exhausted")
        self.value["attempts"].append(dict(request=len(self.value["attempts"])+1,kind=kind,clip_id=clip_id,
            reserved_utc=_now(),outcome="reserved",code="reserved"))
        self._write()  # Durable before any network transmission.

    def finish(self, *, success, code="voice_bank_generation_failed"):
        row = self.value["attempts"][-1]
        row.update(outcome=("metadata_verified" if row["kind"] == "METADATA_GET" else "generated_unapproved") if success else "error",
                   code="ok" if success else code if code in _SAFE_CODES else "voice_bank_generation_failed")
        if not success: self.value["blocked"] = True
        self._write()

    def snapshot(self):
        return dict(authorization=AUTHORIZATION,model=FREE_MODEL,usd_cap="0",max_requests=MAX_REQUESTS,
                    requests_reserved=len(self.value["attempts"]),blocked=self.value["blocked"],
                    requests_remaining=MAX_REQUESTS-len(self.value["attempts"]),
                    exhausted=len(self.value["attempts"]) >= MAX_REQUESTS,
                    attempts=[dict(row) for row in self.value["attempts"]])
