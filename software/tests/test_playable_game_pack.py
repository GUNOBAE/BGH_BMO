"""Bundle integrity and real web-game software admission, without any devices."""
import hashlib
import http.client
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from bmo_ai.features.contracts import CatalogItem, FeatureError
from bmo_ai.features.games import GamesFeature, OwnedProcessRunner
from bmo_ai.features.games import packaged
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.features.server import FeatureServer

REAL_CONNECT = socket.socket.connect


class PlayableGamePackTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="bmo-pack-test-")))
        self.now = 10.
        self.hosts = []
        for target in ("subprocess.Popen", "socket.socket.connect", "socket.socket.connect_ex",
                       "bmo_ai.cloud.create_client", "bmo_ai.fish_tts.synthesize_wav",
                       "bmo_ai.private_credentials.resolve_credentials"):
            self.enterContext(patch(target, side_effect=AssertionError("Device/provider forbidden")))
        self.payloads = {"engines.js": b"window.BMOOfflineGames={create:function(){}};\n",
                         "LICENSE.txt": b"MIT test-owned integrity fixture.\n"}
        self.manifest = {"version": 1, "pack_id": packaged.PACK_ID, "license": "MIT",
            "files": {key: hashlib.sha256(raw).hexdigest() for key, raw in self.payloads.items()},
            "games": [{"id": key, "title": key} for key in packaged.GAME_IDS]}
        for name, raw in self.payloads.items():
            (self.root / name).write_bytes(raw)
            (self.root / name).chmod(0o600)
        self.pin = self.write_manifest()
        self.bundle = packaged.load_bundle(root=self.root, manifest_sha256=self.pin)

    def tearDown(self):
        for host in self.hosts:
            host.close()

    def write_manifest(self, raw=None):
        raw = json.dumps(self.manifest, separators=(",", ":")).encode() if raw is None else raw
        (self.root / "manifest.json").write_bytes(raw)
        (self.root / "manifest.json").chmod(0o600)
        return hashlib.sha256(raw).hexdigest()

    def load(self, *, pin=None):
        return packaged.load_bundle(root=self.root, manifest_sha256=self.pin if pin is None else pin)

    def host(self, **kwargs):
        with patch.object(packaged, "load_bundle", return_value=self.bundle):
            host = FeatureHost(playable_games=True, clock=lambda: self.now,
                               client=FeatureSimulatedClient(), **kwargs)
        self.hosts.append(host)
        return host

    def launch(self, host, game="bmo_blocks", request="launch_pack"):
        return host.execute(HostIntent("launch_game", game), request_id=request)

    def ready(self, host, *, launch_id=None, generation=None):
        web = host.games.snapshot()["web"]
        result = host.control(HostControl("game_ready", {"launch_id": launch_id or web["launch_id"],
            "generation": host.modes.generation if generation is None else generation}), "ready_pack")
        host.poll()
        return result

    def key(self, host, key, pressed=True, *, seq=None, generation=None, queued=False):
        self.seq = getattr(self, "seq", -1) + 1 if seq is None else seq
        command = HostControl("game_key", {"key": key, "pressed": pressed, "sequence": self.seq,
            "generation": host.modes.generation if generation is None else generation})
        if queued:
            return host.queue_command("key_" + str(self.seq), command)
        return host.control(command, "key_pack")

    def test_original_builtin_pack_has_exact_manifest_and_three_real_games(self):
        bundle = packaged.load_bundle()
        self.assertEqual(tuple(item.id for item in bundle.catalog), packaged.GAME_IDS)
        self.assertTrue(all(item.readiness == "READY" and not item.simulated for item in bundle.catalog))
        self.assertIn(b"BMOOfflineGames", bundle.resources["/games/engines.js"][1])
        self.assertEqual(set(bundle.resources), {"/games/engines.js", "/games/LICENSE.txt"})

    def test_manifest_and_each_asset_sha_are_required(self):
        (self.root / "engines.js").write_bytes(b"tampered script")
        with self.assertRaises(FeatureError) as caught:
            self.load()
        self.assertEqual(caught.exception.code, "game_asset_hash_mismatch")
        self.write_manifest(b"{}");
        with self.assertRaises(FeatureError) as caught:
            self.load()
        self.assertEqual(caught.exception.code, "game_manifest_hash_mismatch")

    def test_unknown_files_paths_game_ids_and_duplicate_json_are_rejected(self):
        variants = []
        original = json.dumps(self.manifest)
        bad = json.loads(original); bad["files"]["../credentials.json"] = "a" * 64; variants.append(json.dumps(bad).encode())
        bad = json.loads(original); bad["games"][0]["id"] = "shell_game"; variants.append(json.dumps(bad).encode())
        bad = json.loads(original); bad["games"][0]["command"] = "unsafe"; variants.append(json.dumps(bad).encode())
        variants.append(original.replace('"version": 1', '"version": 1, "version": 1').encode())
        for raw in variants:
            with self.subTest(raw_size=len(raw)), self.assertRaises(FeatureError):
                self.load(pin=self.write_manifest(raw))

    def test_symlink_hardlink_and_writable_assets_fail_closed(self):
        source = self.root / "engines.js"
        data = source.read_bytes(); source.unlink()
        external = self.root / "external.js"; external.write_bytes(data)
        source.symlink_to(external)
        with self.assertRaises(FeatureError): self.load()
        source.unlink(); source.hardlink_to(external)
        with self.assertRaises(FeatureError): self.load()
        source.unlink(); external.unlink(); source.write_bytes(data); source.chmod(0o666)
        with self.assertRaises(FeatureError): self.load()

    def test_bundle_serving_uses_immutable_verified_bytes(self):
        host = self.host()
        before = host.game_resources()
        (self.root / "engines.js").write_bytes(b"changed after verification")
        self.assertEqual(host.game_resources(), before)
        self.assertEqual(before["/games/engines.js"][1], self.payloads["engines.js"])
        before.clear()
        self.assertEqual(len(host.game_resources()), 2)

    def test_optin_is_narrow_and_default_still_has_no_games_or_resources(self):
        host = FeatureHost(clock=lambda: self.now)
        self.hosts.append(host)
        self.assertEqual(host.game_resources(), {})
        self.assertEqual(host.games.catalog(), ())
        with self.assertRaises(FeatureError):
            FeatureHost(playable_games=True, games=GamesFeature())
        item = CatalogItem("arbitrary_native", "Untrusted", "READY", "unverified", (), False)
        arbitrary = FeatureHost(games=GamesFeature((item,)), client=FeatureSimulatedClient(),
                                runtime=RuntimePolicy(simulate=False))
        self.hosts.append(arbitrary)
        self.assertEqual(arbitrary.execute(HostIntent("launch_game", item.id)).code, "capability_denied")

    def test_both_simulate_and_live_admit_only_actual_web_bundle_without_physical_permission(self):
        for simulate in (False, True):
            with self.subTest(simulate=simulate):
                host = self.host(runtime=RuntimePolicy(simulate=simulate))
                result = self.launch(host)
                self.assertEqual((result.status, result.simulated, result.executed), ("accepted", False, False))
                self.assertEqual(host.modes.mode, "game")
                self.assertEqual(host.audio.owner, "game")
                self.assertFalse(host.games.snapshot()["web"]["ready"])
                self.ready(host)
                receipt = host._result_history["launch_pack"]
                self.assertEqual((receipt.status, receipt.simulated, receipt.executed, receipt.completed),
                                 ("running", False, True, False))
                self.assertFalse(host.runtime.camera_supervised or host.runtime.arms_supervised)
                self.assertEqual(host.client.requests, [])

    def test_stale_readiness_cannot_start_another_launch_or_mode(self):
        host = self.host(); self.launch(host)
        web = host.games.snapshot()["web"]
        self.assertEqual(self.ready(host, launch_id="webgame_wrong").code, "stale_game_launch")
        self.assertFalse(host.games.snapshot()["web"]["ready"])
        host.execute(HostIntent("return_to_face"))
        self.launch(host, request="second_launch")
        self.assertNotEqual(host.games.snapshot()["web"]["launch_id"], web["launch_id"])
        self.assertEqual(self.ready(host, launch_id=web["launch_id"]).code, "stale_game_launch")

    def test_reload_ready_releases_old_held_keys_without_another_launch(self):
        host = self.host(); self.launch(host); self.ready(host)
        launch = host.games.snapshot()["web"]["launch_id"]
        self.key(host, "LEFT")
        self.assertEqual(host.games.snapshot()["web"]["held"], ["LEFT"])
        self.ready(host)
        web = host.games.snapshot()["web"]
        self.assertEqual(web["launch_id"], launch)
        self.assertEqual(web["held"], [])
        self.assertEqual(web["inputs"][-1]["key"], "LEFT")
        self.assertFalse(web["inputs"][-1]["pressed"])

    def test_keyboard_stream_plays_many_edges_without_exhausting_action_ledger(self):
        host = self.host(); self.launch(host); self.ready(host)
        for index in range(700):
            self.key(host, "LEFT", index % 2 == 0, queued=True)
            host.poll()
        self.assertEqual(len(host._records), 0)
        web = host.games.snapshot()["web"]
        self.assertEqual(web["input_sequence"], 700)
        self.assertEqual(len(web["inputs"]), 128)
        self.assertEqual(web["inputs"][0]["sequence"], 573)
        self.assertEqual(web["held"], [])
        self.assertEqual(host.client.requests, [])

    def test_keyboard_sequence_generation_and_queue_ttl_reject_old_edges(self):
        host = self.host(); self.launch(host); self.ready(host)
        self.key(host, "RIGHT", seq=50)
        self.assertEqual(self.key(host, "RIGHT", False, seq=49).code, "repeated_gamepad_input")
        generation = host.modes.generation
        self.assertEqual(self.key(host, "A", seq=51, generation=generation - 1).code, "gamepad_mode_blocked")
        self.assertEqual(host.games.snapshot()["web"]["held"], [])
        self.key(host, "A", seq=52, queued=True)
        self.now += .501; host.poll()
        web = host.games.snapshot()["web"]
        self.assertNotIn("A", web["held"] if web else [])

    def test_keyboard_and_pad_share_key_until_both_sources_release_it(self):
        host = self.host(usb_gamepad=True); self.launch(host); self.ready(host)
        pad_seq = 0
        for key in ("LEFT", "START", "SELECT"):
            with self.subTest(key=key):
                self.key(host, key)
                pad_seq += 1
                host.control(HostControl("gamepad", {"key": key, "pressed": True, "sequence": pad_seq,
                    "generation": host.modes.generation}), "pad_shared")
                before = host.games.snapshot()["web"]["input_sequence"]
                self.key(host, key, False)
                web = host.games.snapshot()["web"]
                self.assertIn(key, web["held"])
                self.assertEqual(web["input_sequence"], before)
                pad_seq += 1
                host.control(HostControl("gamepad", {"key": key, "pressed": False, "sequence": pad_seq,
                    "generation": host.modes.generation}), "pad_shared")
                web = host.games.snapshot()["web"]
                self.assertNotIn(key, web["held"])
                self.assertEqual(web["input_sequence"], before + 1)
                self.assertFalse(web["inputs"][-1]["pressed"])

    def test_keyboard_watchdog_disconnect_and_stop_release_all_resources(self):
        for stop in ("watchdog", "PAD_LOST", "stop", "cancel"):
            with self.subTest(stop=stop):
                host = self.host(); self.launch(host); self.ready(host)
                self.key(host, "RIGHT")
                if stop == "watchdog": self.now += .81
                elif stop == "PAD_LOST": self.key(host, "PAD_LOST")
                else: host.control(HostControl(stop), "control_stop")
                host.poll()
                self.assertNotEqual(host.modes.mode, "game")
                self.assertIsNone(host.games.snapshot()["web"])
                self.assertIsNone(host.audio.owner)
                self.assertNotEqual(host._result_history["launch_pack"].status, "running")

    def test_only_game_inputs_are_accepted_no_gp8_servo_camera_or_provider_path(self):
        host = self.host(); self.launch(host); self.ready(host)
        for key in ("TALK", "STOP", "MENU", "GP8", "GP7", "SERVO", "CAMERA"):
            with self.subTest(key=key), self.assertRaises(FeatureError): self.key(host, key)
        self.assertFalse(host.ptt_button())
        self.assertEqual(host.client.requests, [])
        self.assertEqual(host.motion.snapshot()["queued"], 0)
        self.assertFalse(host.camera.snapshot()["on"])

    def test_missing_browser_readiness_times_out_and_returns_to_menu(self):
        host = self.host(); self.launch(host)
        self.now += packaged.PackagedWebRunner.READY_TIMEOUT + .01
        host.poll()
        self.assertEqual(host.modes.mode, "game_menu")
        self.assertIsNone(host.audio.owner)
        self.assertEqual(host._result_history["launch_pack"].status, "failed")

    def test_same_game_relaunch_has_new_identity_and_no_held_input(self):
        host = self.host(); self.launch(host); self.ready(host)
        first = host.games.snapshot()["web"]["launch_id"]
        self.key(host, "DOWN")
        host.execute(HostIntent("return_to_face"))
        self.launch(host, request="new_launch"); self.ready(host)
        web = host.games.snapshot()["web"]
        self.assertNotEqual(web["launch_id"], first)
        self.assertEqual((web["input_sequence"], web["held"], web["inputs"]), (0, [], []))

    def test_closing_host_retires_web_runner_and_keyboard_ownership(self):
        host = self.host(); self.launch(host); self.ready(host)
        runner = host.games._runner
        self.key(host, "DOWN")
        host.close()
        self.assertFalse(runner.active or runner.ready)
        self.assertEqual((list(runner.inputs), runner.held), ([], set()))
        self.assertTrue(host.cleanup_snapshot()["resources_cleaned"])

    def test_http_serves_only_cached_allowlisted_pack_files_and_never_local_paths(self):
        class Face:
            def __init__(self, *args, **kwargs):
                self._resources = {"/face.html": ("text/html", b"<p>synthetic face</p>")}
                self._screen_metadata = b"{}"
        def own_loopback(connection, address):
            if address[0] != "127.0.0.1":
                raise AssertionError("External network forbidden")
            return REAL_CONNECT(connection, address)
        host = self.host()
        with patch("bmo_ai.features.server.FaceServer", Face), patch("socket.socket.connect", own_loopback):
            with FeatureServer(host, self.root / "no-art-loaded.html") as server:
                parsed = urlsplit(server.url)
                def get(path, **headers):
                    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2)
                    try:
                        connection.request("GET", path, headers=headers)
                        response = connection.getresponse()
                        return response.status, response.read(), response.getheader("Content-Type")
                    finally:
                        connection.close()
                self.assertEqual(get("/games/engines.js")[:2], (200, self.payloads["engines.js"]))
                self.assertIn("javascript", get("/games/engines.js")[2])
                for path in ("/games/manifest.json", "/games/../manifest.json", "/games/%2e%2e/credentials.json",
                             "/games/unknown.js", "/games/engines.js/extra"):
                    with self.subTest(path=path): self.assertEqual(get(path)[0], 404)
                self.assertEqual(get("/games/engines.js", Origin="https://example.invalid")[0], 403)
                (self.root / "engines.js").write_bytes(b"tampered after server construction")
                self.assertEqual(get("/games/engines.js")[1], self.payloads["engines.js"])


if __name__ == "__main__":
    unittest.main()
