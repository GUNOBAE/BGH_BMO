"""Bounded child-process PCM owner and latest-value IPC; no provider/network IO.

The parent remains the AudioFocus owner. Only this child opens the PCM sink;
its producer/scaling thread has an interpreter independent from Desktop work.
Status publication never blocks audio when the parent stops polling.
"""
import argparse
import ctypes
import json
import mmap
import os
import signal
import struct
import time
import zlib
from types import SimpleNamespace

from ..contracts import FeatureError

PAGE = 4096
SIZE = PAGE*2


class LatestRecord:
    """Single writer seqlock record with a fixed memory bound, no backlog."""
    def __init__(self, memory, offset=0):
        self.memory,self.offset,self.counter=memory,offset,0

    def publish(self, value):
        raw=json.dumps(value,allow_nan=False,separators=(',',':')).encode('ascii')
        if len(raw)>PAGE-16:raise FeatureError('media_output_failed')
        self.counter+=2
        struct.pack_into('<Q',self.memory,self.offset,self.counter-1)
        self.memory[self.offset+16:self.offset+16+len(raw)]=raw
        struct.pack_into('<II',self.memory,self.offset+8,len(raw),zlib.crc32(raw))
        struct.pack_into('<Q',self.memory,self.offset,self.counter)

    def read(self):
        for _ in range(3):
            before=struct.unpack_from('<Q',self.memory,self.offset)[0]
            if not before or before%2:continue
            size,digest=struct.unpack_from('<II',self.memory,self.offset+8)
            if not 0 < size <= PAGE-16:return None
            raw=self.memory[self.offset+16:self.offset+16+size]
            after=struct.unpack_from('<Q',self.memory,self.offset)[0]
            if before!=after or zlib.crc32(raw)!=digest:continue
            try:
                value=json.loads(raw)
                return value if type(value) is dict else None
            except (ValueError,UnicodeError):return None
        return None


class SharedVolume:
    def __init__(self, control):self.control=control;self.last=1.
    def snapshot(self):
        value=self.control.read()
        if value is not None:self.last=float(value['level'])
        return {'effective_level':self.last}


class SyntheticClockSink:
    """Explicit test IO: 100ms consumption queue, real monotonic starvation.

    This does not emulate ALSA accuracy or prove physical playback. Its small
    queue exposes interpreter scheduling starvation without opening a device.
    """
    latest=None
    def __init__(self, device, rate, channels):
        self.rate,self.channels=rate,channels
        self.capacity=rate//10
        self.submitted=0;self.started=None;self.closed=False;self.finished=False
        self.last_amplitude=None
        type(self).latest=self

    def _consumed(self):
        return 0 if self.started is None else int((time.monotonic()-self.started)*self.rate)

    def write(self, block):
        consumed=self._consumed()
        if self.started is not None and consumed>self.submitted and not self.finished:
            raise FeatureError('media_output_underrun')
        accepted=min(len(block)//(self.channels*2),max(0,self.capacity-(self.submitted-consumed)))
        self.submitted+=accepted
        if accepted:self.last_amplitude=int.from_bytes(block[:2],'little',signed=True)
        if self.started is None and self.submitted>=self.capacity:self.started=time.monotonic()
        return accepted

    def delay(self):return max(0,self.submitted-self._consumed())
    def finish_input(self):
        self.finished=True
        if self.started is None:self.started=time.monotonic()
    def close(self):self.closed=True


def _snapshot(engine, *, ready, cleaned=False):
    value=engine.diagnostics()
    with engine._guard:
        value.update(frame=engine._frame,rate=engine._rate,
            submitted=engine._submitted_frame,queued=engine._queued_frames)
    if SyntheticClockSink.latest is not None:
        value['synthetic_last_amplitude']=SyntheticClockSink.latest.last_amplitude
    return dict(value,version=1,ready=ready,resources_cleaned=cleaned,
        provider_env_absent=not any(name in os.environ for name in ('FISH_API_KEY','OPENAI_API_KEY','GH_TOKEN')),
        worker_pid=os.getpid(),producer='isolated_process',status_at=time.monotonic())


def _parent_guard(expected):
    # No daemon can outlive Desktop intentionally. Linux also delivers TERM
    # during a parent crash; check the race before/after installing the guard.
    if os.getppid()!=expected:raise FeatureError('media_output_unavailable')
    try:
        libc=ctypes.CDLL(None)
        if libc.prctl(1,signal.SIGTERM,0,0,0)!=0:raise OSError()
    except (AttributeError,OSError):
        raise FeatureError('media_output_unavailable') from None
    if os.getppid()!=expected:raise FeatureError('media_output_unavailable')


def run(args):
    from .pcm_clock import SampleClockWavBackend,AlsaPcmSink
    memory=mmap.mmap(args.state_fd,SIZE)
    status,control=LatestRecord(memory),LatestRecord(memory,PAGE)
    engine=None
    try:
        _parent_guard(args.parent)
        sink=AlsaPcmSink if args.sink=='alsa' else SyntheticClockSink
        # Child input is an inherited verified fd; no asset path or credential
        # arrives in a command payload. Explicit sink injection prevents nested
        # process construction and reuses the tested PCM engine unchanged.
        engine=SampleClockWavBackend(device=args.device,enabled=True,volume_reviewed=True,
            volume=SharedVolume(control),sink_factory=sink)
        engine._handle=SimpleNamespace(fd=args.wave_fd,duration=args.duration)
        engine._frame=args.frame
        engine._launch()
        last_level=control.read()['level']
        while True:
            command=control.read()
            if command is not None and command['stop']:break
            if command is not None and command['level']!=last_level:
                last_level=command['level']
                if last_level==0:engine.volume_changed()
            status.publish(_snapshot(engine,ready=True))
            if engine.poll() is not None:break
            time.sleep(.01)
        engine._halt()
        status.publish(_snapshot(engine,ready=True,cleaned=True))
        return 0
    except BaseException as error:
        value=_snapshot(engine,ready=False) if engine is not None else dict(
            frame=args.frame,rate=0,submitted=args.frame,queued=0,output_started=False)
        original=value.get('error_code')
        code=getattr(error,'code',None)
        value.update(version=1,ready=False,terminal='failed',error_code=original or
            (code if type(code) is str and code.startswith('media_') else 'media_output_failed'),
            producer='isolated_process',worker_pid=os.getpid(),resources_cleaned=False)
        if engine is not None:
            try:engine._halt()
            except BaseException:value['cleanup_error']='media_output_cleanup_failed'
        # Process exit closes remaining descriptors even when a library-level
        # close failed. The parent still requires wait/reap before releasing.
        value['resources_cleaned']=True
        status.publish(value)
        return 1
    finally:memory.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--wave-fd',type=int,required=True)
    parser.add_argument('--state-fd',type=int,required=True)
    parser.add_argument('--parent',type=int,required=True)
    parser.add_argument('--duration',type=float,required=True)
    parser.add_argument('--frame',type=int,required=True)
    parser.add_argument('--device')
    parser.add_argument('--sink',choices=('alsa','synthetic'),default='alsa')
    raise SystemExit(run(parser.parse_args()))
