"""Read-only evidence for the conversation-to-arm boundary.

Configuration, a validated current-turn plan, player-start and a B1 ACK are
different observations. This projection never opens the transport, calls a
permission callback, resets a latch or infers a physical servo position.
"""
import re

from .legacy_host import MODEL_ONLY


STOP_SEMANTICS = "board_latch_not_cleared_by_host_restart_not_power_cut"
_STAGES = frozenset(("idle", "planned", "player_started", "submitted", "ended",
                     "cancelled", "error", "stale"))
_RESULTS = frozenset(("accepted", "running", "simulated", "completed", "rejected",
                      "failed", "cancelled", "unknown"))
_EMOTIONS = frozenset(("neutral", "happy", "sad", "surprised", "angry", "crying"))
_CODE = re.compile(r"[a-z][a-z0-9_]{0,95}\Z", re.ASCII)
_TURN = re.compile(r"[A-Za-z0-9_:.-]{1,160}\Z", re.ASCII)


def _code(value):
    return value if type(value) is str and _CODE.fullmatch(value) else None


def conversation_arm_status(*, simulate, supervised_requested,
                            supervised_effective, emotion_arms_requested,
                            emotion_arms_effective, motion, turn_state=None,
                            setup_error=None, commissioned_requested=False,
                            commissioned_effective=False, usage_enabled=None):
    """Project bounded status only; do not use it as a motion authorization.

    The caller records ``turn_state`` at the existing reply/player-start/result
    callbacks. A protocol acknowledgement proves only the remote enable state
    at that exchange, never a static file SHA, wiring or actual movement.
    Lazy B1 HELLO remains valid: ``hello_required`` alone does not reject an
    otherwise supervised current request.
    """
    state = turn_state if type(turn_state) is dict else {}
    adapter = motion if type(motion) is dict else {}
    transport = adapter.get("transport")
    transport = transport if type(transport) is dict else {}
    response = transport.get("last_response_code")
    enabled_at_ack = (False if response == "DISABLED" else True if response in
                      ("READY", "ALIVE", "ACCEPTED", "HELD", "STOPPED", "PERFORMANCE_READY",
                       "PERFORM_ENTER", "PERFORM_HOLD", "PERFORM_RETURN", "PERFORM_IDLE") else None)
    setup_code = _code(setup_error)
    if setup_error is not None and setup_code is None:
        setup_code = "arm_setup_failed"
    launcher = dict(supervised_requested=supervised_requested is True,
                    supervised_effective=supervised_effective is True,
                    emotion_arms_requested=emotion_arms_requested is True,
                    emotion_arms_effective=emotion_arms_effective is True,
                    setup_code=setup_code)
    if commissioned_requested is True or commissioned_effective is True or type(usage_enabled) is bool:
        launcher.update(commissioned_requested=commissioned_requested is True,
                        commissioned_effective=commissioned_effective is True,
                        usage_enabled=usage_enabled is True, current_supervision_stored=False)
    turn = state.get("turn_id")
    gesture = state.get("gesture_id", "none")
    gesture = gesture if type(gesture) is str and gesture in MODEL_ONLY else "none"
    stage = state.get("stage", "idle")
    stage = stage if type(stage) is str and stage in _STAGES else "idle"
    started, fresh = state.get("player_start_seen") is True, state.get("player_start_fresh") is True
    result_status = state.get("result_status")
    result_status = result_status if type(result_status) is str and result_status in _RESULTS else None
    reasons = []
    if setup_code:
        reasons.append(setup_code)
    if simulate is not True and supervised_effective is not True and commissioned_effective is not True:
        reasons.append("arm_runtime_off")
    if usage_enabled is False:
        reasons.append("arm_usage_off")
    if emotion_arms_effective is not True:
        reasons.append("emotion_arms_off")
    if adapter.get('performance_supported') is False:
        reasons.append('arm_performance_firmware_required')
    if simulate is not True and adapter.get("real_enabled") is not True:
        reasons.append("arm_adapter_unavailable")
    blocked = adapter.get("blocked_reasons", ())
    for reason in blocked[:16] if type(blocked) in (tuple, list) else ():
        if _code(reason) and reason not in reasons:
            reasons.append(reason)
    if adapter.get("closed") is True and "arm_closed" not in reasons:
        reasons.append("arm_closed")
    if adapter.get("stopped") is True and "arm_stopped" not in reasons:
        reasons.append("arm_stopped")
    if not gesture or gesture == "none":
        reasons.append("current_turn_no_gesture")
    if stage in ("ended", "cancelled", "error", "idle"):
        reasons.append("turn_" + stage)
    elif not started:
        reasons.append("awaiting_player_start")
    elif not fresh or stage == "stale":
        reasons.append("stale_playback_start")
    automatic_reason = _code(state.get("automatic_reason"))
    if automatic_reason and automatic_reason != "eligible" and automatic_reason not in reasons:
        reasons.append(automatic_reason)
    result_code = _code(state.get("result_code"))
    if result_status in ("rejected", "failed", "cancelled", "unknown") and result_code:
        if result_code not in reasons:
            reasons.append(result_code)
    return dict(
        launcher=launcher,
        board=dict(remote_enabled_static=None,
                   remote_enabled_at_last_ack=enabled_at_ack,
                   evidence="protocol_ack" if enabled_at_ack is not None else "not_observed",
                   physical_verified=False),
        conversation=dict(turn_id=turn if type(turn) is str and _TURN.fullmatch(turn) else None,
                          gesture_id=gesture, stage=stage,
                          plan_emotion=state.get("plan_emotion") if type(state.get("plan_emotion")) is str and state["plan_emotion"] in _EMOTIONS else None,
                          playback_emotion=state.get("playback_emotion") if type(state.get("playback_emotion")) is str and state["playback_emotion"] in _EMOTIONS else None,
                          player_start_seen=started, player_start_fresh=started and fresh,
                          result_status=result_status, result_code=result_code,
                          automatic_reason=automatic_reason,
                          blocked_reasons=reasons,
                          eligible_at_player_start=not reasons),
        stop_semantics=STOP_SEMANTICS)
