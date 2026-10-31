"""Bounded FFmpeg scopes and real synthetic decode; no Pi/provider/playback IO."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch
import wave

from bmo_ai import media_assets
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.media import seed


class MediaDecodeCommandTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='bmo-decode-command-')))
        self.source = self.root/'private-original'
        self.source.write_bytes(b'synthetic input boundary; process is injected')

    def test_all_three_decode_branches_limit_input_global_filters_and_output_separately(self):
        for video, output, stage in ((True, None, 'decode_video'),
                                     (False, None, 'decode_mp3'),
                                     (True, self.root/'private-output.wav', 'extract_video_audio')):
            with self.subTest(stage=stage):
                seen = []
                def process(command, **kwargs):
                    descriptor, = kwargs['pass_fds']
                    self.assertEqual(os.pread(descriptor, 1000, 0), self.source.read_bytes())
                    seen.append((list(command), kwargs, descriptor))
                    return SimpleNamespace(returncode=0, stdout=b'')
                with patch.object(seed.subprocess, 'run', side_effect=process):
                    seed._decode(self.source, output, video=video)
                self.assertEqual(len(seen), 1)
                args, settings, descriptor = seen[0]
                source_position = args.index('-i')
                thread_positions = [index for index, value in enumerate(args) if value == '-threads']
                self.assertEqual(len(thread_positions), 2, args)
                self.assertLess(thread_positions[0], source_position)
                self.assertGreater(thread_positions[1], source_position+1)
                self.assertEqual([args[index+1] for index in thread_positions], ['1', '1'])
                for option in ('-filter_threads', '-filter_complex_threads'):
                    self.assertEqual(args.count(option), 1)
                    index = args.index(option)
                    self.assertLess(index, source_position)
                    self.assertEqual(args[index+1], '1')
                self.assertEqual(args.count('-xerror'), 1)
                self.assertLess(args.index('-xerror'), source_position)
                self.assertEqual(args[source_position+1], '/proc/self/fd/'+str(descriptor))
                self.assertEqual(args[args.index('-protocol_whitelist')+1], 'file,pipe')
                self.assertEqual(args[args.index('-f')+1], 'mov' if video else 'mp3')
                self.assertEqual(args[args.index('-t')+1], '120')
                self.assertEqual(settings['timeout'], 20)
                self.assertIs(settings['stdin'], subprocess.DEVNULL)
                self.assertIs(settings['stdout'], subprocess.PIPE)
                self.assertIs(settings['stderr'], subprocess.DEVNULL)
                self.assertIs(settings['preexec_fn'], seed._limits)
                self.assertEqual(settings['env'], {'PATH': os.defpath, 'LC_ALL': 'C.UTF-8'})
                self.assertFalse(settings['check'])
                if output is None:
                    self.assertEqual(args[-3:], ['-f', 'null', '-'])
                else:
                    self.assertEqual(args[args.index('-map')+1], '0:a:0')
                    self.assertIn('-vn', args)
                    self.assertEqual(args[args.index('-c:a')+1], 'pcm_s16le')
                    self.assertEqual(args[-4:], ['-f', 'wav', '-n', str(output)])
                with self.assertRaises(OSError):
                    os.fstat(descriptor)
                self.assertFalse(self.root.joinpath('private-output.wav').exists())

    def test_existing_address_space_cpu_file_size_limits_are_unchanged(self):
        with patch.object(seed.resource, 'setrlimit') as setlimit:
            seed._limits()
        self.assertEqual(setlimit.call_args_list, [
            call(resource.RLIMIT_FSIZE, (48*1024*1024, 48*1024*1024)),
            call(resource.RLIMIT_CPU, (15, 15)),
            call(resource.RLIMIT_AS, (768*1024*1024, 768*1024*1024)),
        ])

    def test_nonzero_signal_timeout_and_unavailable_are_safe_distinct_diagnostics(self):
        cases = [
            (SimpleNamespace(returncode=245, stdout=b''), None,
             'media_decode_failed', {'stage':'decode_video', 'outcome':'nonzero_exit', 'returncode':245, 'signal':None}),
            (SimpleNamespace(returncode=-9, stdout=b''), None,
             'media_decode_failed', {'stage':'decode_video', 'outcome':'nonzero_exit', 'returncode':-9, 'signal':9}),
            (None, subprocess.TimeoutExpired(['/private/secret-command'], 20,
                                            output=b'private output', stderr=b'private stderr'),
             'media_decode_timeout', {'stage':'decode_video', 'outcome':'timeout', 'returncode':None, 'signal':None}),
            (None, OSError('private executable path'),
             'media_decode_unavailable', {'stage':'decode_video', 'outcome':'unavailable', 'returncode':None, 'signal':None}),
        ]
        for result, error, code, diagnostic in cases:
            with self.subTest(code=code, diagnostic=diagnostic):
                with patch.object(seed.subprocess, 'run', return_value=result, side_effect=error):
                    with self.assertRaises(seed.MediaDecodeError) as caught:
                        seed._decode(self.source, video=True)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.diagnostic, diagnostic)
                self.assertNotIn('private', str(caught.exception))
                self.assertNotIn(str(self.root), str(caught.exception.diagnostic))

    def test_stdout_bound_is_not_silently_removed(self):
        with patch.object(seed.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=b'x'*65537)):
            with self.assertRaises(seed.MediaDecodeError) as caught:
                seed._decode(self.source, video=False)
        self.assertEqual(caught.exception.code, 'media_decode_failed')
        self.assertEqual(caught.exception.diagnostic,
            {'stage':'decode_mp3', 'outcome':'stdout_limit', 'returncode':0, 'signal':None})

    def test_cli_exports_only_stage_exit_facts_not_child_stderr_or_private_paths(self):
        error = seed.MediaDecodeError('media_decode_failed', stage='decode_video',
                                     outcome='nonzero_exit', returncode=245)
        with patch.object(seed, 'review_media_seed', side_effect=error):
            with redirect_stdout(io.StringIO()) as output:
                result = media_assets.main(['--media-seed', '/private/supplied-song.zip',
                                            '--sha256', 'f'*64, '--dry-run'])
        self.assertEqual(result, 2)
        public = json.loads(output.getvalue())
        self.assertEqual(public['code'], 'media_decode_failed')
        self.assertEqual(public['playback'], 'NOT_RUN')
        self.assertEqual(public['diagnostic'],
            {'stage':'decode_video', 'outcome':'nonzero_exit', 'returncode':245, 'signal':None})
        self.assertNotIn('/private', output.getvalue())
        self.assertNotIn('stderr', output.getvalue())


@unittest.skipUnless(shutil.which('ffmpeg'), 'installed local FFmpeg required')
class MediaDecodeFFmpegTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='bmo-decode-synthetic-')
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.video, cls.mp3 = cls.root/'synthetic.mp4', cls.root/'synthetic.mp3'
        # These are public-test synthetic tones/pixels, never supplied user media.
        common = ['ffmpeg', '-nostdin', '-v', 'error', '-xerror',
                  '-filter_threads', '1', '-filter_complex_threads', '1']
        commands = [
            [*common, '-threads', '1', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=25:duration=1.2',
             '-threads', '1', '-f', 'lavfi', '-i', 'sine=frequency=660:sample_rate=48000:duration=1.2',
             '-map', '0:v:0', '-map', '1:a:0', '-threads', '1', '-c:v', 'libx264', '-c:a', 'aac',
             '-ac', '2', '-movflags', '+faststart', '-shortest', '-n', str(cls.video)],
            [*common, '-threads', '1', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1.2',
             '-threads', '1', '-c:a', 'libmp3lame', '-ac', '2', '-n', str(cls.mp3)],
        ]
        for command in commands:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, timeout=20)
            if result.returncode:
                raise AssertionError('Synthetic media encoder unavailable: '+result.stderr.decode(errors='replace')[:160])
        cls.original = {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in (cls.video, cls.mp3)}

    def setUp(self):
        self.output_root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='bmo-decode-output-')))
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')))

    def test_real_full_mp4_and_mp3_verification_and_embedded_pcm_extraction(self):
        seed._decode(self.video, video=True)
        seed._decode(self.mp3, video=False)
        output = self.output_root/'embedded-pcm.wav'
        seed._decode(self.video, output, video=True)
        with wave.open(str(output), 'rb') as source:
            self.assertEqual((source.getnchannels(), source.getsampwidth(), source.getframerate()), (2,2,48000))
            self.assertGreater(source.getnframes(), 48000)
            self.assertEqual(len(source.readframes(source.getnframes())), source.getnframes()*4)
        self.assertEqual({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.video,self.mp3)}, self.original)

    def test_truncated_mp4_and_corrupt_mp3_are_rejected_instead_of_registered(self):
        truncated = self.output_root/'truncated.mp4'
        raw = self.video.read_bytes()
        truncated.write_bytes(raw[:len(raw)//2])
        corrupt = self.output_root/'corrupt.mp3'
        corrupt.write_bytes(b'\0'*4096)
        for path, video, stage in ((truncated, True, 'decode_video'), (corrupt, False, 'decode_mp3')):
            with self.subTest(stage=stage), self.assertRaises(seed.MediaDecodeError) as caught:
                seed._decode(path, video=video)
            self.assertEqual(caught.exception.code, 'media_decode_failed')
            self.assertEqual(caught.exception.diagnostic['stage'], stage)
            self.assertEqual(caught.exception.diagnostic['outcome'], 'nonzero_exit')
            self.assertNotEqual(caught.exception.diagnostic['returncode'], 0)

    def test_existing_pcm_output_is_preserved_by_no_overwrite_option(self):
        output = self.output_root/'retained-original.wav'
        original = b'private existing destination sentinel'
        output.write_bytes(original)
        try:
            seed._decode(self.video, output, video=True)
        except FeatureError as error:
            self.assertEqual(error.code, 'media_decode_failed')
        self.assertEqual(output.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
