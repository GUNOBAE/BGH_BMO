"""Bounded PCM delivery to one owned ALSA process during a Fish WAV download.

Only validated RIFF/PCM headers and whole sample frames reach the player. The
caller still validates the complete downloaded WAV before declaring success or
promoting repeat. A truncated response can therefore end an audible prefix but
can never become a completed response. No provider retry or second player.
"""
from __future__ import annotations

import os
import math
from queue import Empty, Full, Queue
import select
import struct
import subprocess
from threading import Event, Thread
import time

from . import audio
from .conversation import TurnCancelled
from .integration_face import LADDER, bridge
from .safe_playback import _reduce_chunk


def _invalid():
    return audio.AudioValidationError('The speech PCM stream is invalid.', 'invalid_wav')


class PCMParser:
    """Incremental canonical PCM parser; never treats JSON/error bytes as audio."""
    def __init__(self, accept):
        self.accept, self.buffer = accept, bytearray()
        self.total = self.offset = self.received = 0
        self.riff_size = self.format = self.data_size = None
        self.in_data = False
        self.pad_done = False
        self.trailer_bytes = 0

    def feed(self, block):
        if type(block) is not bytes or self.total + len(block) > audio.MAX_AUDIO_BYTES:
            raise _invalid()
        self.total += len(block)
        self.buffer.extend(block)
        if self.riff_size is None:
            if len(self.buffer) < 12:
                return
            if self.buffer[:4] != b'RIFF' or self.buffer[8:12] != b'WAVE':
                raise _invalid()
            self.riff_size = struct.unpack_from('<I', self.buffer, 4)[0]
            if self.riff_size not in (0xffffffff, 4294967076) and not 36 <= self.riff_size <= audio.MAX_AUDIO_BYTES-8:
                raise _invalid()
            del self.buffer[:12]
            self.offset = 12
        while not self.in_data:
            if len(self.buffer) < 8:
                return
            tag, size = struct.unpack_from('<4sI', self.buffer)
            if tag == b'data':
                if self.format is None or (size not in (0xffffffff, 4294967040)
                        and not 0 < size <= audio.MAX_AUDIO_BYTES):
                    raise _invalid()
                self.data_size = None if size in (0xffffffff, 4294967040) else size
                if self.data_size is not None:
                    if (size % self.format[3] or size/(self.format[1]*self.format[3])>180
                            or self.riff_size not in (0xffffffff,4294967076)
                            and self.offset+8+size+size%2>self.riff_size+8):
                        raise _invalid()
                del self.buffer[:8]
                self.offset += 8
                self.in_data = True
                break
            # A speech header cannot consume arbitrary provider data/memory.
            if size > 65536 or self.offset + 8 + size > 65536:
                raise _invalid()
            end = 8 + size + size % 2
            if len(self.buffer) < end:
                return
            if tag == b'fmt ':
                if self.format is not None or size < 16:
                    raise _invalid()
                encoding, channels, rate, byte_rate, align, bits = struct.unpack_from('<HHIIHH', self.buffer, 8)
                if encoding == 0xfffe:
                    if (size < 40 or not 22 <= struct.unpack_from('<H', self.buffer, 24)[0] <= size-18
                            or not 1 <= struct.unpack_from('<H', self.buffer, 26)[0] <= bits
                            or self.buffer[32:48] != audio._PCM_GUID):
                        raise _invalid()
                    encoding = 1
                if (encoding != 1 or channels not in (1, 2) or bits not in (8,16,24,32)
                        or not 8000 <= rate <= 192000 or align != channels*(bits//8)
                        or byte_rate != rate*align):
                    raise _invalid()
                self.format = (channels, rate, bits//8, align)
            del self.buffer[:end]
            self.offset += end
        channels, rate, width, align = self.format
        available = len(self.buffer)
        if self.data_size is not None:
            available = min(available, self.data_size-self.received)
        size = available - available % align
        if size:
            self.received += size
            if self.received / (rate*align) > 180:
                raise audio.AudioValidationError('Speech exceeds the face playback limit.', 'speech_audio_too_long')
            payload = bytes(self.buffer[:size])
            del self.buffer[:size]
            self.accept(self.format, payload)
        if self.data_size is not None and self.received == self.data_size:
            if not self.pad_done:
                if self.data_size % 2:
                    if not self.buffer:
                        return
                    del self.buffer[:1]
                self.pad_done = True
            # Ordinary metadata after finite PCM is legal WAV, but is never
            # sent to aplay as raw sound. Keep ancillary chunks bounded and
            # reject a second fmt/data exactly like complete WAV validation.
            while len(self.buffer) >= 8:
                tag,size=struct.unpack_from('<4sI',self.buffer)
                end=8+size+size%2
                if tag in (b'fmt ',b'data') or self.trailer_bytes+end>65536:
                    raise _invalid()
                if len(self.buffer)<end:
                    return
                del self.buffer[:end]
                self.trailer_bytes+=end

    def finish(self):
        if (not self.in_data or not self.received or self.received % self.format[3]
                or self.data_size is not None and self.received != self.data_size
                or len(self.buffer) != (self.received % 2 if self.data_size is None else 0)
                or self.data_size is not None and not self.pad_done
                or self.riff_size not in (0xffffffff,4294967076) and self.riff_size != self.total-8):
            raise _invalid()


class StreamingPCMPlayer:
    """One bounded PCM queue/player, with cancellation independent of HTTP read.

    Player-start means the first validated PCM has been handed to the owned
    process, consistent with the existing spawn-clock contract; it does not
    measure the speaker's acoustic output. Mouth cues use that same clock.
    """
    def __init__(self, *, device, level, check, prepare, started, progress, env,
                 process_factory=None, clock=time.monotonic, prefix_factory=None, pcm_started=None,
                 prefix_baked_gain=1.):
        self.device, self.level, self.check = device, level, check
        self.prepare, self.started_callback, self.progress = prepare, started, progress
        self.env, self.clock = env, clock
        self.process_factory = process_factory or subprocess.Popen
        self.prefix_factory, self.pcm_started = prefix_factory, pcm_started
        self.prefix_baked_gain = prefix_baked_gain
        self.leading = b''
        self.prefix_frames = 0
        # The total input bound remains 24 MiB; at most 512 KiB waits ahead of
        # the player, rather than accumulating an unbounded streaming response.
        self.queue = Queue(maxsize=128)
        self.abort, self.done, self.started = Event(), Event(), Event()
        self.error = None
        self.parser = PCMParser(self._accept)
        self.format = None
        self.prefix = bytearray()
        self.thread = None
        self.cues = []
        self._mouth = None
        self._closed = False
        self._input_finished = False
        self._deadline = None

    def _check(self):
        if self.error is not None:
            raise self.error
        self.check()
        if self.abort.is_set():
            raise TurnCancelled()
        if self._deadline is not None and self.clock() >= self._deadline:
            raise audio.AudioPlaybackError('The speech player timed out.', 'audio_timeout')

    def _put(self, payload):
        while True:
            self._check()
            try:
                self.queue.put(payload, timeout=.02)
                return
            except Full:
                pass

    def _accept(self, fmt, block):
        self.format = fmt
        # Split every accepted frame block, even an injected HTTP response
        # which ignores requested read size, into at most 4096-byte packets.
        size = 4096 - 4096 % fmt[3]
        for offset in range(0, len(block), size):
            part = block[offset:offset+size]
            if self.thread is None:
                self.prefix.extend(part)
                if len(self.prefix) < round(.24*fmt[1])*fmt[3]:
                    continue
                self._launch()
            else:
                self._put(part)

    def _launch(self):
        initial = bytes(self.prefix)
        self.prefix.clear()
        if self.prefix_factory is not None:
            self.leading = self.prefix_factory(self.format) or b''
            channels, rate, width, align = self.format
            if (type(self.leading) is not bytes or len(self.leading) % align
                    or len(self.leading) > rate*align):
                raise _invalid()
            self.prefix_frames = len(self.leading)//align
        self.thread = Thread(target=self._run, args=(initial,), name='bmo-stream-player', daemon=True)
        self.thread.start()

    def feed(self, block):
        self._check()
        self.parser.feed(block)
        self._check()

    def _mouth_cues(self, block, offset):
        if self._mouth is None:
            self._mouth = _StreamingMouth(self.format,block)
        return self._mouth.feed(block,offset)

    def _run(self, initial):
        process = None
        try:
            self._check()
            channels, rate, width, align = self.format
            formats = {1:'U8',2:'S16_LE',3:'S24_3LE',4:'S32_LE'}
            initial = _reduce_chunk(initial,width,self.level)
            leading = (_reduce_chunk(self.leading,width,self.level/self.prefix_baked_gain)
                       if self.leading else b'')
            self.cues = self._mouth_cues(leading or initial,0.)
            self.prepare(list(self.cues))
            self._check()
            command = ['aplay','-q',*audio._device_args(self.device),'-t','raw',
                       '-f',formats[width],'-r',str(rate),'-c',str(channels)]
            process = self.process_factory(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL,env=self.env,bufsize=0)
            descriptor = process.stdin.fileno()
            os.set_blocking(descriptor,False)
            offset, clock_start = 0., None
            block = leading or initial
            next_initial = initial if leading else None
            prefix_block = bool(leading)
            answer_reported = False
            deadline = self._deadline = self.clock()+190.
            while True:
                self._check()
                if self.clock() >= deadline:
                    raise audio.AudioPlaybackError('The speech player timed out.', 'audio_timeout')
                if block is None:
                    break
                # Keep each block on the same start clock. A producer stall
                # leaves the preceding closed-mouth tail in place; new cues
                # resume at the later delivery position, never reset the turn.
                if clock_start is not None:
                    offset = max(offset,self.clock()-clock_start)
                    if offset+len(block)/(rate*align)>180:
                        raise audio.AudioPlaybackError('The speech timeline exceeded its limit.', 'audio_timeout')
                    new = self._mouth_cues(block,offset)
                    self.cues.extend(new)
                    if len(self.cues) > 10000:
                        raise _invalid()
                    # A provisional tail closes the mouth only if no further
                    # audio arrives. It is replaced before its time during a
                    # continuous stream, rather than resetting every packet.
                    tail = offset+len(block)/(rate*align)+.08
                    self.progress([*self.cues,dict(time=round(min(180.,tail),6),mouth='closed')])
                position = 0
                while position < len(block):
                    self._check()
                    if process.poll() is not None:
                        raise audio.AudioPlaybackError('The speech player stopped early.', 'device_unavailable')
                    if not select.select([], [descriptor], [], .02)[1]:
                        continue
                    wrote = os.write(descriptor,block[position:])
                    position += wrote
                    if wrote and self.pcm_started is not None:
                        if clock_start is None:
                            self.pcm_started('first_pcm', self.prefix_frames)
                            if prefix_block:
                                self.pcm_started('reaction_started', self.prefix_frames)
                        if not prefix_block and not answer_reported:
                            answer_reported = True
                            self.pcm_started('answer_pcm', self.prefix_frames)
                            if not self.prefix_frames:
                                self.pcm_started('answer_started', 0)
                    if clock_start is None:
                        clock_start = self.clock()
                        self.started.set()
                        self.started_callback()
                offset += len(block)/(rate*align)
                if next_initial is not None:
                    block, next_initial, prefix_block = next_initial, None, False
                    continue
                while True:
                    self._check()
                    try:
                        value = self.queue.get(timeout=.02)
                        break
                    except Empty:
                        if process.poll() is not None:
                            raise audio.AudioPlaybackError('The speech player stopped early.', 'device_unavailable')
                block = None if value is None else _reduce_chunk(value,width,self.level)
            process.stdin.close()
            process.stdin = None
            while process.poll() is None:
                self._check()
                if self.clock() >= deadline:
                    raise audio.AudioPlaybackError('The speech player timed out.', 'audio_timeout')
                self.abort.wait(.02)
            if process.returncode:
                raise audio.AudioPlaybackError('The speech device rejected playback.', 'device_unavailable')
        except FileNotFoundError:
            self.error = audio.AudioPlaybackError('ALSA tools are unavailable.', 'tool_unavailable')
        except OSError:
            self.error = audio.AudioPlaybackError('The speech player could not continue.', 'audio_process_failed')
        except BaseException as error:
            self.error = error
        finally:
            if process is not None:
                try:
                    if process.stdin is not None:
                        try:
                            process.stdin.close()
                        except OSError:
                            pass
                        process.stdin = None
                    if process.poll() is None:
                        audio._stop_owned(process)
                    else:
                        process.wait()
                finally:
                    self.done.set()
            else:
                self.done.set()

    def finish_input(self):
        self._check()
        if self._input_finished:
            return
        self.parser.finish()
        if self.thread is None:
            self._launch()
        self._put(None)
        self._input_finished = True

    def finish(self):
        self.finish_input()
        while not self.done.wait(.02):
            self._check()
        self._check()

    def close(self):
        self.abort.set()
        if self.thread is not None:
            self.thread.join(timeout=.5)
        self._closed = True


class _StreamingMouth:
    """Approved RMS ladder/holds with continuous state across PCM packets.

    A complete WAV's global p90 is unknowable before EOF. Streaming fixes the
    normalization from the validated 240 ms prefill and retains the existing
    smoothing, thresholds and bridge; repeat uses the original whole-WAV path.
    Music's established timeline is untouched. This is amplitude animation,
    never a claim of phoneme or measured speaker synchronization.
    """
    def __init__(self,fmt,initial):
        self.channels,self.rate,self.width,self.align=fmt
        self.step=max(1,math.floor(self.rate/120+.5))*self.align
        levels=[self._rms(initial[i:i+self.step]) for i in range(0,len(initial),self.step)]
        nonzero=sorted(value for value in levels if value>.0004)
        self.scale=max(.012,nonzero[math.floor(len(nonzero)*.9)] if nonzero else .05)
        self.floor=max(.001,self.scale*.035)
        self.env=0.;self.active=0;self.last_switch=-1.
        self.pending=b'';self.pending_at=0.;self.previous='closed'
        self.last_mouth='closed'
        self.last_audio_end=None

    def _rms(self,block):
        total=0.
        for index in range(0,len(block),self.width):
            raw=int.from_bytes(block[index:index+self.width],'little',signed=self.width!=1)
            value=(raw-128)/128 if self.width==1 else raw/(1 << (self.width*8-1))
            total+=value*value
        return math.sqrt(total/max(1,len(block)//self.width))

    def feed(self,block,offset):
        if self.last_audio_end is not None and offset-self.last_audio_end>.08:
            # A network starvation gap is on the player's wall clock, not the
            # compressed PCM timeline. Never time resumed cues in the past.
            self.pending=b''
            self.env=0.;self.active=0;self.last_switch=-1.;self.previous='closed';self.last_mouth='closed'
        start=self.pending_at if self.pending else offset
        self.last_audio_end=offset+len(block)/(self.rate*self.align)
        data=self.pending+block
        cues=[]
        for index in range(0,len(data)-self.step+1,self.step):
            stamp=start+index/(self.rate*self.align)
            raw=max(0.,min(1.,(self._rms(data[index:index+self.step])-self.floor)/(self.scale-self.floor)))
            self.env+=(raw-self.env)*(.42 if raw>self.env else .24)
            level=next((i for i,limit in enumerate((.06,.12,.21,.33,.47,.62,.78,.91)) if self.env<limit),8)
            if level!=self.active and (stamp-self.last_switch>=.05 or level==0):
                if abs(level-self.active)==1 and stamp-self.last_switch<.075:
                    continue
                previous,target=LADDER[self.active],LADDER[level]
                self.active,self.last_switch=level,stamp
                # Existing bridge's first step; subsequent RMS windows deliver
                # the target after its 1/30-second transition without a reset.
                mouth=bridge(previous,target,0.,.105)
                if mouth!=self.last_mouth:
                    cues.append(dict(time=round(stamp,6),mouth=mouth))
                    self.last_mouth=mouth
                self.previous=previous
            elif self.last_switch>=0 and stamp-self.last_switch<.075:
                mouth=bridge(self.previous,LADDER[self.active],stamp-self.last_switch,.105)
                if self.last_mouth!=mouth:
                    cues.append(dict(time=round(stamp,6),mouth=mouth))
                    self.last_mouth=mouth
        consumed=len(data)//self.step*self.step
        self.pending=data[consumed:]
        self.pending_at=start+consumed/(self.rate*self.align)
        return cues
