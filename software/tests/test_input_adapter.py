"""Injected traces only: no real controller reader, mapping discovery or IO."""
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.contracts import FeatureError, MODES
from bmo_ai.features.games import GamesFeature
from bmo_ai.features.input import InputAdapter, RawInputEvent, SyntheticEventSource, VerifiedMapping
from bmo_ai.features.resources import InputRouter, ModeManager


class TraceSource:
    """A bounded poll fixture that can deliberately inject invalid timestamps."""
    def __init__(self, events=()):
        self.events, self.closed, self.polls = list(events), False, 0

    def poll(self, limit):
        self.polls += 1
        result, self.events = self.events[:limit], self.events[limit:]
        return tuple(result)

    def close(self):
        self.closed = True


class InputAdapterTests(unittest.TestCase):
    def setUp(self):
        self.now, self.calls = 10., []
        self.modes = ModeManager()
        for mode in MODES:
            self.modes.register(mode)
        self.router = InputRouter(self.modes, stop=lambda: self.calls.append("STOP"),
                                  talk=lambda: self.calls.append("TALK"))
        for mode in MODES:
            self.router.bind(mode, lambda key, pressed, now: self.calls.append((key, pressed, now)))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("Process forbidden")))

    def event(self, control, pressed=True, sequence=0, at=None, source="synthetic"):
        return RawInputEvent(source, control, pressed, sequence, self.now if at is None else at)

    def adapter(self, events=(), bindings=None, source=None, **settings):
        mapping = VerifiedMapping("synthetic", bindings or {"a": "A", "b": "B", "stop": "STOP",
                                  "talk": "TALK", "start": "START", "select": "SELECT"},
                                  verified=True, provenance="synthetic_test")
        result = InputAdapter(self.router, source or TraceSource(events), mapping=mapping,
                              clock=lambda: self.now, **settings)
        self.addCleanup(result.close)
        return result

    def test_unverified_defaults_do_not_poll_source_or_route_disconnect(self):
        source = Mock()
        adapter = InputAdapter(self.router, source, clock=lambda: self.now)
        self.assertEqual(adapter.poll(), ())
        self.assertEqual(adapter.disconnect(), ())
        source.poll.assert_not_called()
        self.assertFalse(adapter.snapshot()["enabled"])
        self.assertEqual(adapter.snapshot()["reason_code"], "input_unverified")
        self.assertEqual(self.calls, [])
        adapter.close()

    def test_mapping_requires_explicit_verification_and_unique_logical_bindings(self):
        invalid = (("synthetic", {"a": "A"}, False, "fixture"),
                   ("synthetic", {"a": "A"}, True, ""),
                   ("unknown", {"a": "A"}, True, "fixture"),
                   ("synthetic", {"a": "A", "b": "A"}, True, "fixture"),
                   ("synthetic", {"../device": "A"}, True, "fixture"),
                   ("synthetic", {"a": "SHELL"}, True, "fixture"))
        for source, mapping, verified, provenance in invalid:
            with self.subTest(mapping=mapping), self.assertRaises(FeatureError):
                VerifiedMapping(source, mapping, verified=verified, provenance=provenance)
        bindings = {"confirmed_button": "TALK"}
        mapping = VerifiedMapping("rp2040_hid", bindings, verified=True, provenance="fixture_only")
        bindings["confirmed_button"] = "STOP"
        self.assertEqual(mapping.bindings["confirmed_button"], "TALK")
        with self.assertRaises(TypeError):
            mapping.bindings["other"] = "A"

    def test_gp7_conflict_is_not_inferred_as_a_button_mapping(self):
        for source in ("rp2040_hid", "rp2040_cdc"):
            for key in ("GP7", "gpio7", "PIN_GP7"):
                with self.subTest(source=source, key=key), self.assertRaises(FeatureError) as caught:
                    VerifiedMapping(source, {key: "TALK"}, verified=True, provenance="fixture")
                self.assertEqual(caught.exception.code, "gp7_conflict")

    def test_raw_event_rejects_invalid_types_and_nonfinite_values(self):
        for fields in (("synthetic", "a", 1, 0, 1), ("synthetic", "a", True, True, 1),
                       ("synthetic", "a", True, -1, 1), ("synthetic", "a", True, 0, float("nan")),
                       ("synthetic", "a", True, 0, float("inf"))):
            with self.subTest(fields=fields), self.assertRaises(FeatureError):
                RawInputEvent(*fields)

    def test_synthetic_source_waits_without_sleep_and_is_bounded(self):
        source = SyntheticEventSource((self.event("a", at=10), self.event("b", sequence=1, at=11)),
                                      clock=lambda: self.now)
        self.assertEqual(len(source.poll(1)), 1)
        self.assertEqual(source.poll(), ())
        self.now = 11
        self.assertEqual(source.poll()[0].control, "b")
        source.close()
        self.assertEqual(source.poll(), ())
        with self.assertRaises(FeatureError):
            SyntheticEventSource([self.event("a")] * 4097)
        with self.assertRaises(FeatureError):
            SyntheticEventSource((self.event("a", at=2), self.event("a", at=1)))
        with self.assertRaises(FeatureError):
            source.poll(65)

    def test_duplicate_sequence_unmapped_and_wrong_source_never_dispatch(self):
        adapter = self.adapter((self.event("a"), self.event("b", sequence=0),
                                self.event("unknown", sequence=1),
                                self.event("b", sequence=2, source="usb_pad")))
        outcomes = adapter.poll()
        self.assertEqual([item.code for item in outcomes],
                         ["duplicate_input", "unmapped_input", "input_unverified", "delivered"])
        self.assertEqual(self.calls, [("A", True, 10.)])
        self.assertEqual(adapter.snapshot()["real_input"], "NOT_RUN")
        self.assertEqual(adapter.snapshot()["device_opens"], 0)

    def test_stale_future_and_reordered_events_are_discarded(self):
        adapter = self.adapter((self.event("a", sequence=0, at=8), self.event("a", sequence=1, at=10.1),
                                self.event("a", sequence=2, at=9.8), self.event("b", sequence=3, at=9.7)))
        outcomes = adapter.poll()
        self.assertEqual(sum(item.delivered for item in outcomes), 1)
        self.assertEqual([item.code for item in outcomes].count("stale_input"), 3)
        self.assertEqual(self.calls, [("A", True, 9.8)])

    def test_repeated_press_is_dropped_but_release_is_never_delayed(self):
        source = TraceSource((self.event("start"), self.event("start", sequence=1),
                              self.event("start", False, 2), self.event("start", True, 3, 10.01)))
        adapter = self.adapter(source=source)
        outcomes = adapter.poll()
        self.assertEqual([o.code for o in outcomes],
                         ["delivered", "repeated_input", "delivered", "debounced_input"])
        self.assertEqual(self.calls, [("START", True, 10.), ("START", False, 10.)])
        self.now = 10.04
        source.events.append(self.event("start", sequence=4))
        self.assertTrue(adapter.poll()[0].delivered)

    def test_talk_is_edge_only_and_does_not_leak_release_to_game(self):
        adapter = self.adapter((self.event("talk"), self.event("talk", sequence=1),
                                self.event("talk", False, 2)))
        self.modes.transition("game")
        self.assertEqual([o.code for o in adapter.poll()],
                         ["delivered", "repeated_input", "edge_only_release"])
        self.assertEqual(self.calls, ["TALK"])

    def test_stop_precedes_talk_and_game_input_in_the_same_batch(self):
        adapter = self.adapter((self.event("talk"), self.event("a", sequence=1),
                                self.event("stop", sequence=2)))
        outcomes = adapter.poll()
        self.assertEqual(self.calls, ["STOP"])
        self.assertEqual(sum(o.code == "stop_preempted" for o in outcomes), 2)
        self.assertEqual(outcomes[0].logical, "STOP")

    def test_stop_repeat_is_debounced_and_same_batch_release_allows_next_edge(self):
        source = TraceSource((self.event("stop"),))
        adapter = self.adapter(source=source)
        adapter.poll()
        source.events.append(self.event("stop", sequence=1))
        self.assertEqual(adapter.poll()[0].code, "repeated_input")
        source.events.append(self.event("stop", False, 2))
        adapter.poll()
        source.events.extend((self.event("stop", sequence=3), self.event("stop", False, 4)))
        adapter.poll()
        source.events.append(self.event("stop", sequence=5))
        adapter.poll()
        self.assertEqual(self.calls, ["STOP", "STOP", "STOP"])

    def test_stop_clears_held_controls_after_stop_callback(self):
        source = TraceSource((self.event("start"), self.event("select", sequence=1)))
        adapter = self.adapter(source=source)
        adapter.poll()
        self.calls.clear()
        source.events.append(self.event("stop", sequence=2))
        adapter.poll()
        self.assertEqual(self.calls, ["STOP", ("SELECT", False, 10.), ("START", False, 10.)])

    def test_mode_generation_change_blocks_remaining_batch_presses(self):
        def select(key, pressed, now):
            self.calls.append((key, pressed))
            self.modes.transition("game")
        self.router.bind("conversation", select)
        adapter = self.adapter((self.event("a"), self.event("b", sequence=1)))
        self.assertEqual([o.code for o in adapter.poll()], ["delivered", "stale_mode"])
        self.assertEqual(self.calls, [("A", True)])

    def test_source_failure_releases_keys_and_reports_pad_loss_once(self):
        source = TraceSource((self.event("start"),))
        adapter = self.adapter(source=source)
        adapter.poll()
        source.poll = Mock(side_effect=RuntimeError("private device path"))
        outcomes = adapter.poll()
        self.assertEqual([o.logical for o in outcomes], ["START", "PAD_LOST"])
        self.assertEqual(adapter.poll(), ())
        self.assertNotIn("private", repr(outcomes))
        self.assertEqual(adapter.snapshot()["reason_code"], "input_source_failed")

    def test_close_is_idempotent_releases_controls_and_sanitizes_source_cleanup(self):
        source = TraceSource((self.event("select"),))
        adapter = self.adapter(source=source)
        adapter.poll()
        source.close = Mock(side_effect=RuntimeError("private reader"))
        adapter.close(); adapter.close()
        self.assertEqual(self.calls, [("SELECT", True, 10.), ("SELECT", False, 10.)])
        source.close.assert_called_once()
        self.assertEqual(adapter.poll(), ())
        self.assertFalse(adapter.snapshot()["enabled"])

    def test_consumer_failure_is_fixed_and_invalid_clock_fails_before_poll(self):
        self.router.bind("conversation", Mock(side_effect=RuntimeError("private payload")))
        adapter = self.adapter((self.event("a"),))
        self.assertEqual(adapter.poll()[0].code, "input_consumer_failed")
        self.now = float("nan")
        with self.assertRaises(FeatureError) as caught:
            adapter.poll()
        self.assertEqual(caught.exception.code, "invalid_input_clock")

    def test_b_and_start_select_trace_use_existing_game_hold_and_exit_once(self):
        self.now = 0.
        game = GamesFeature(fixture=True, clock=lambda: self.now)
        self.addCleanup(game.close)
        game.start("fixture_game_a", request_id="trace:1", mode_generation=1)
        game.poll()
        self.modes.transition("game")
        self.router.bind("game", lambda key, pressed, now: game.game_input(
            "PAD_DISCONNECTED" if key == "PAD_LOST" else key, pressed, now=now))
        source = TraceSource((self.event("b"), self.event("b", False, 1),
                              self.event("start", sequence=2), self.event("select", sequence=3)))
        adapter = self.adapter(source=source)
        adapter.poll()
        self.assertIn(("B", True), game._runner.inputs)
        self.assertIn(("B", False), game._runner.inputs)
        self.assertEqual(self.modes.mode, "game")
        self.now = 1.199
        self.assertEqual(game.poll(), ())
        self.now = 1.2
        self.assertEqual([event.kind for event in game.poll()], ["game_exit_requested"])
        self.now = 20
        self.assertEqual(game.poll(), ())

    def test_release_before_hold_threshold_and_disconnect_cannot_trigger_exit(self):
        game = GamesFeature(fixture=True, clock=lambda: self.now)
        self.addCleanup(game.close)
        game.start("fixture_game_a", request_id="trace:1", mode_generation=1); game.poll()
        self.modes.transition("game")
        self.router.bind("game", lambda key, pressed, now: game.game_input(
            "PAD_DISCONNECTED" if key == "PAD_LOST" else key, pressed, now=now))
        source = TraceSource((self.event("start"), self.event("select", sequence=1)))
        adapter = self.adapter(source=source)
        adapter.poll()
        self.now = 11.199
        source.events.append(self.event("select", False, 2))
        adapter.poll()
        self.now = 12
        self.assertEqual(game.poll(), ())
        runner = game._runner
        adapter.disconnect()
        self.assertFalse(runner.running)
        self.assertEqual([event.kind for event in game.poll()], ["game_ended"])

    def test_finite_trace_drain_and_ledgers_remain_bounded(self):
        events = tuple(self.event("a", bool(n % 2 == 0), n) for n in range(4096))
        adapter = self.adapter(source=SyntheticEventSource(events, clock=lambda: self.now), debounce_seconds=0)
        for _ in range(128):
            self.assertEqual(len(adapter.poll()), 32)
        self.assertEqual(adapter.poll(), ())
        self.assertEqual(adapter.snapshot()["delivered"], 4096)
        self.assertLessEqual(len(adapter._pressed), 1)
        self.assertLessEqual(len(adapter._released_at), 1)


if __name__ == "__main__":
    unittest.main()
