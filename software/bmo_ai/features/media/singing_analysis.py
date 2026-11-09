"""Offline, hash-bound whole-mix features for TWO MOODS 06.

This is the numerical preparation half of supplied BMOMusicAnalysis 1.0.0.
It owns no audio output. Runtime only reads this immutable cache; neither the
PCM producer nor a playback request analyses music. These are amplitude/onset
approximations, never vocals, lyrics, phonemes or inferred song emotion.
"""
from array import array
import hashlib
import json
import math
import os
import stat
import sys
import time
import wave

from ..contracts import FeatureError
from .assets import _verified_fd
from .library import _private_root, _same, _write_stage

SCHEMA = 'bmo.music-profile.v1'
VERSION = '1.0.0'
STEP = .02
MAX_SECONDS = 600
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_CACHE_BYTES = 4 * 1024 * 1024
MAX_PREPARE_SECONDS = 30
ARRAYS = ('level', 'onset', 'energy', 'tempo', 'confidence', 'beats')
METHOD = 'whole-mix three-band energy-onset autocorrelation; no vocals or lyrics'


def _clamp(value, minimum=0., maximum=1.):
    return max(minimum, min(maximum, value))


def _quantile(values, fraction):
    values = sorted(values)
    return values[min(len(values)-1, math.floor(len(values)*fraction))] if values else 0.


def _float32(values):
    return array('f', values)


def _estimate(onsets, start, end, dt):
    if end-start < 200:
        return {'bpm': None, 'confidence': 0.}
    values = onsets[start:end]
    threshold = max(.025, _quantile(values, .90)*.6, _quantile(values, .98)*.28)
    peaks = []
    for i in range(start+1, end-1):
        if onsets[i] >= threshold and onsets[i] >= onsets[i-1] and onsets[i] > onsets[i+1]:
            if not peaks or i-peaks[-1] > 5:
                peaks.append(i)
            elif onsets[i] > onsets[peaks[-1]]:
                peaks[-1] = i
    if len(peaks) < 5:
        return {'bpm': None, 'confidence': 0.}
    ac = {}
    for lag in range(17, 56):
        ab = aa = bb = 0.
        for i in range(start+lag, end):
            a, b = onsets[i], onsets[i-lag]
            ab += a*b; aa += a*a; bb += b*b
        ac[lag] = ab/max(1e-9, math.sqrt(aa*bb))
    best = None
    for lag in range(17, 56):
        corr = .6*ac[lag]+.2*(ac.get(lag-1) or ac[lag])+.2*(ac.get(lag+1) or ac[lag])
        interval = count = 0
        for i in range(1, len(peaks)):
            gap = peaks[i]-peaks[i-1]
            if gap > 100:
                continue
            interval += math.exp(-abs(gap-lag)**2/8); count += 1
        interval /= max(1, count)
        score = .65*corr+.35*interval
        if best is None or score > best[0]:
            best = (score, lag, corr, interval)
    _, lag, corr, interval = best
    confidence = _clamp((corr*.65+interval*.35-.10)/.5)
    if (confidence < .28 or corr < .12
            or corr-_quantile(ac.values(), .5) < .035 and interval < .3):
        return {'bpm': None, 'confidence': confidence}
    left, right, center = ac.get(lag-1, ac[lag]), ac.get(lag+1, ac[lag]), ac[lag]
    delta = _clamp(.5*(left-right)/(left-2*center+right or 1), -.5, .5)
    return {'bpm': _clamp(60/((lag+delta)*dt), 55, 180), 'confidence': confidence}


def from_envelopes(rms, low, high, *, step=STEP, check=lambda: None):
    """Mirror the supplied analyzer, including Float32 envelope boundaries."""
    n = len(rms)
    p95 = _quantile((x for x in rms if x > .00005), .95) or .1
    floor = max(.0001, p95*.025)
    levels = _float32(_clamp((x-floor)/max(.0001, p95-floor)) for x in rms)
    onset_raw = array('f', [0.])*n
    for i in range(1, n):
        positive = lambda values: max(0., math.log1p(values[i]*4/p95)-math.log1p(values[i-1]*4/p95))
        onset_raw[i] = .25*positive(rms)+.45*positive(low)+.30*positive(high)
    op = max(.03, _quantile(onset_raw, .96))
    onsets = _float32(_clamp((x-.02)/op) for x in onset_raw)
    peaks = [1. if v > .45 and v > (onsets[i-1] if i else 0.)
             and v >= (onsets[i+1] if i+1 < n else 0.) else 0. for i,v in enumerate(onsets)]
    level = _float32(.65*v+.25*(levels[i-1] if i else v)+.1*(levels[i-2] if i > 1 else v)
                     for i,v in enumerate(levels))
    check(); overall = _estimate(onsets, 0, n, step)
    windows = []
    t = 0.
    while t < n*step:
        check()
        windows.append(dict(time=t, **_estimate(onsets, max(0, math.floor((t-6)/step)),
                                               min(n, math.ceil((t+6)/step)), step)))
        t += 4.
    energy = array('f'); tempo = array('f'); confidence = array('f'); beats = array('f')
    smooth_energy, smooth_tempo, beat = .25, overall['bpm'] or 84., 0.
    # Prefix sums preserve the analyzer's exact windows with bounded linear IO.
    prefixes = []
    for values in (onsets, level, peaks):
        prefix = [0.]
        for value in values:
            prefix.append(prefix[-1]+value)
        prefixes.append(prefix)
    for i in range(n):
        if i % 1000 == 0: check()
        a,b = max(0,i-50),min(n,i+50)
        activity,loud,density = [(p[b]-p[a])/max(1,b-a) for p in prefixes]
        density /= step
        target = _clamp(.12+.22*math.sqrt(max(0.,activity))+.6*_clamp(density/3.2)+.15*(loud-.4))
        smooth_energy += (target-smooth_energy)*.018; energy.append(smooth_energy)
        window = windows[min(len(windows)-1, math.floor(i*step/4))]
        desired = window['bpm'] if window['bpm'] is not None else overall['bpm'] if overall['confidence'] > .40 else None
        conf = window['confidence'] if window['bpm'] else overall['confidence']*.65 if overall['confidence'] > .40 else 0.
        smooth_tempo += ((desired or 84.)-smooth_tempo)*.012
        tempo.append(smooth_tempo); confidence.append(conf); beat += smooth_tempo/60*step; beats.append(beat)
    return dict(schema=SCHEMA, step=step, duration=n*step, level=list(level), onset=list(onsets),
                energy=list(energy), tempo=list(tempo), confidence=list(confidence), beats=list(beats),
                summary=dict(bpm=math.floor(overall['bpm']+.5) if overall['bpm'] else None,
                    confidence=overall['confidence'], energy=sum(energy)/max(1,n), rawOnsetP96=op,
                    method=METHOD),
                windows=windows)


def _filename(asset):
    return '.singing-' + asset.sha256 + '-v1.json'


def _validate(value, asset):
    if (type(value) is not dict
            or set(value) != {'schema','step','duration','sample_duration','audio_sha256','analyzer_version',
                              'level','onset','energy','tempo','confidence','beats','summary','windows'}
            or value.get('schema') != SCHEMA
            or value.get('analyzer_version') != VERSION or value.get('audio_sha256') != asset.sha256
            or value.get('step') != STEP or type(value.get('duration')) not in (int,float)
            or not math.isfinite(value['duration']) or not 0 < value['duration'] <= MAX_SECONDS+STEP
            or type(value.get('sample_duration')) not in (int,float)
            or not math.isfinite(value['sample_duration']) or not 0 < value['sample_duration'] <= MAX_SECONDS
            or abs(value['duration']-value['sample_duration']) > STEP+.00001
            or asset.duration is not None and abs(value['sample_duration']-asset.duration) > .02):
        raise FeatureError('singing_profile_invalid')
    n = math.ceil(value['sample_duration']/STEP-1e-9)
    if any(type(value.get(key)) is not list or len(value[key]) != n for key in ARRAYS):
        raise FeatureError('singing_profile_invalid')
    for key in ARRAYS:
        high = 180 if key == 'tempo' else 1801 if key == 'beats' else 1
        if any(type(x) not in (int,float) or not math.isfinite(x) or not 0 <= x <= high for x in value[key]):
            raise FeatureError('singing_profile_invalid')
    summary,windows=value['summary'],value['windows']
    if (type(summary) is not dict or set(summary) != {'bpm','confidence','energy','rawOnsetP96','method'}
            or summary['method'] != METHOD or type(windows) is not list or len(windows)>151):
        raise FeatureError('singing_profile_invalid')
    def number(x, lo=0., hi=1.):
        return type(x) in (int,float) and math.isfinite(x) and lo<=x<=hi
    if (summary['bpm'] is not None and not number(summary['bpm'],55,180)
            or not number(summary['confidence']) or not number(summary['energy'])
            or not number(summary['rawOnsetP96'],0,100)):
        raise FeatureError('singing_profile_invalid')
    for i,window in enumerate(windows):
        if (type(window) is not dict or set(window) != {'time','bpm','confidence'}
                or window['time'] != i*4 or not number(window['confidence'])
                or window['bpm'] is not None and not number(window['bpm'],55,180)):
            raise FeatureError('singing_profile_invalid')
    return value


def _unique(pairs):
    value={}
    for key,item in pairs:
        if key in value: raise ValueError('duplicate profile field')
        value[key]=item
    return value


def load_profile(content_root, asset):
    """Read a prepared cache only; never decode or regenerate during playback."""
    root = descriptor = None
    try:
        root = _private_root(content_root)
        if root is None:
            raise FileNotFoundError()
        descriptor = os.open(_filename(asset), os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC, dir_fd=root)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600 or not 0 < before.st_size <= MAX_CACHE_BYTES):
            raise FeatureError('singing_profile_unsafe')
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(MAX_CACHE_BYTES+1)
        if len(raw) != before.st_size or not _same(before, os.fstat(descriptor)):
            raise FeatureError('singing_profile_changed')
        value = _validate(json.loads(raw,object_pairs_hook=_unique), asset)
        return value, raw, hashlib.sha256(raw).hexdigest()
    except FileNotFoundError:
        raise FeatureError('singing_profile_missing') from None
    except FeatureError:
        raise
    except (OSError,ValueError,TypeError,OverflowError,RecursionError):
        raise FeatureError('singing_profile_invalid') from None
    finally:
        for fd in (descriptor,root):
            if fd is not None: os.close(fd)


def prepare_profile(content_root, asset, *, clock=time.monotonic):
    """Explicit offline preparation; create-only, existing audio/cache preserved."""
    if asset.media_type != 'audio/wav' or asset.duration is not None and asset.duration > MAX_SECONDS:
        raise FeatureError('singing_analysis_unsupported')
    started = clock()
    def check():
        if clock()-started > MAX_PREPARE_SECONDS:
            raise FeatureError('singing_analysis_timeout')
    descriptor = root = None
    stage = None
    try:
        descriptor = _verified_fd(content_root, asset.path, asset.sha256, limit=MAX_INPUT_BYTES)
        before = os.fstat(descriptor)
        # A prepared file is reusable only after its audio has been reverified.
        try:
            value,raw,digest = load_profile(content_root, asset)
            return dict(status='READY', audio_sha256=asset.sha256, profile_sha256=digest,
                        reused=True, sample_duration=value['sample_duration'], playback='NOT_RUN')
        except FeatureError as error:
            if error.code != 'singing_profile_missing': raise
        rms,low,high = array('f'),array('f'),array('f')
        with os.fdopen(os.dup(descriptor),'rb') as stream, wave.open(stream,'rb') as wav:
            channels,rate,frames = wav.getnchannels(),wav.getframerate(),wav.getnframes()
            if (wav.getcomptype() != 'NONE' or wav.getsampwidth() != 2 or channels not in (1,2)
                    or not 8000 <= rate <= 96000 or rate % 50 or not 0 < frames/rate <= MAX_SECONDS):
                raise FeatureError('singing_analysis_unsupported')
            duration = frames/rate
            if asset.duration is not None and abs(duration-asset.duration) > .02:
                raise FeatureError('media_duration_mismatch')
            step = math.floor(rate*STEP+.5)
            alpha = 1-math.exp(-2*math.pi*220/(rate/4))
            lp = [0.]*channels
            for start in range(0, frames, step):
                check(); count_frames = min(step, frames-start)
                raw = wav.readframes(count_frames)
                if len(raw) != count_frames*channels*2:
                    raise FeatureError('invalid_media_wav')
                samples = array('h'); samples.frombytes(raw)
                if sys.byteorder != 'little': samples.byteswap()
                a=b=c=0.; count=0
                for channel in range(channels):
                    for i in range(0,count_frames,4):
                        x=samples[i*channels+channel]/32768.
                        lp[channel] += alpha*(x-lp[channel])
                        a += x*x; b += lp[channel]*lp[channel]; c += (x-lp[channel])**2; count += 1
                rms.append(math.sqrt(a/max(1,count))); low.append(math.sqrt(b/max(1,count))); high.append(math.sqrt(c/max(1,count)))
        if not _same(before,os.fstat(descriptor)):
            raise FeatureError('media_changed')
        profile = from_envelopes(rms,low,high,check=check)
        profile.update(audio_sha256=asset.sha256,analyzer_version=VERSION,sample_duration=duration)
        _validate(profile,asset)
        # Fixed precision bounds the cache and is substantially below drawing precision.
        for key in ARRAYS: profile[key] = [round(value,7) for value in profile[key]]
        raw = (json.dumps(profile,allow_nan=False,separators=(',',':'),sort_keys=True)+'\n').encode()
        if len(raw) > MAX_CACHE_BYTES: raise FeatureError('singing_profile_too_large')
        check(); root=_private_root(content_root)
        stage=_write_stage(root,raw)
        try:
            os.link(stage,_filename(asset),src_dir_fd=root,dst_dir_fd=root,follow_symlinks=False)
        except FileExistsError:
            # Never replace a concurrent result, unsafe file or manual content.
            value,current,digest=load_profile(content_root,asset)
            return dict(status='READY',audio_sha256=asset.sha256,profile_sha256=digest,
                        reused=True,sample_duration=value['sample_duration'],playback='NOT_RUN')
        os.fsync(root)
        return dict(status='READY',audio_sha256=asset.sha256,profile_sha256=hashlib.sha256(raw).hexdigest(),
                    reused=False,sample_duration=duration,playback='NOT_RUN')
    except FeatureError:
        raise
    except (OSError,ValueError,EOFError,wave.Error):
        raise FeatureError('singing_analysis_failed') from None
    finally:
        if stage is not None and root is not None:
            try: os.unlink(stage,dir_fd=root)
            except OSError: pass
        for fd in (descriptor,root):
            if fd is not None: os.close(fd)
