"""Local admission for decorative gestures, separate from explicit commands."""
from collections import deque


class ConversationGesturePolicy:
    INTERVAL = 45.
    WINDOW = 300.
    LIMIT = 2
    GREETING_GAP = 600.

    def __init__(self):
        self.starts = deque(maxlen=self.LIMIT)
        self.last_conversation = None
        self._turns = deque(maxlen=64)

    def greeting_eligible(self, now):
        return self.last_conversation is None or now - self.last_conversation >= self.GREETING_GAP

    def reason(self, plan, *, now, turn, greeting_eligible, has_action=False):
        if plan is None or plan.gesture_id == 'none':
            return 'current_turn_no_gesture'
        if turn in self._turns:
            return 'automatic_gesture_duplicate'
        if has_action:
            return 'automatic_gesture_action_turn'
        if plan.sensitive_context or plan.explicit_acting or plan.source != 'dialogue':
            return 'automatic_gesture_context_suppressed'
        context = getattr(plan, 'gesture_context', 'none')
        if (plan.gesture_id, context) not in {
                ('wave', 'greeting'), ('celebrate', 'achievement'),
                ('shrug', 'playful'), ('shrug', 'hesitation')}:
            return 'automatic_gesture_context_required'
        if plan.gesture_id == 'wave' and not greeting_eligible:
            return 'automatic_greeting_recent_conversation'
        if self.starts and now - self.starts[-1] < self.INTERVAL:
            return 'automatic_gesture_cooldown'
        if sum(now - at < self.WINDOW for at in self.starts) >= self.LIMIT:
            return 'automatic_gesture_window_limit'
        return 'eligible'

    def started(self, turn, now):
        if turn not in self._turns:
            self._turns.append(turn)
            self.starts.append(now)

    def snapshot(self, now):
        return dict(min_interval_seconds=self.INTERVAL, window_seconds=self.WINDOW,
                    max_per_window=self.LIMIT, started_in_window=sum(now-at < self.WINDOW for at in self.starts),
                    greeting_gap_seconds=self.GREETING_GAP)
