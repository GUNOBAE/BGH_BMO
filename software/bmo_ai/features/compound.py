"""At most three current-turn allowlisted actions, owned by the existing Host.

This is a finite reply contract, not a workflow runtime. Only verified terminal
receipts advance it; cancelled, stale or uncertain actions never start a tail.
"""
from uuid import uuid5


class CompoundActions:
    def __init__(self, host):
        self.host = host
        self.active = None
        self.last = None
        self.seen = set()

    def begin(self, turn, steps, *, generation, photo_success=None, preparation=None):
        from .reply import HostIntent
        if (turn in self.seen or len(self.seen) >= 512 or self.active is not None
                or not 1 <= len(steps) <= 3
                or any(not isinstance(step, HostIntent) for step in steps)):
            return False
        self.seen.add(turn)
        self.active = dict(parent_turn=turn, parent_generation=generation,
            generation=generation, epoch=self.host._command_epoch,
            parent_request='plan_'+uuid5(self.host._namespace, turn).hex,
            steps=tuple(steps), index=0, request_id=None, results=[],
            deadline=self.host.clock()+180., status='running', code='plan_started',
            photo_success=photo_success, preparation=preparation)
        return True

    def cancel(self, code='plan_cancelled'):
        if self.active is not None:
            self.active.update(status='cancelled', code=code)
            self.last, self.active = self.active, None

    def tick(self):
        plan, host = self.active, self.host
        if plan is None:
            return
        if (host._closed or plan['epoch'] != host._command_epoch
                or plan['generation'] != host.modes.generation
                or host._safety_pending or host.clock() > plan['deadline']):
            self.cancel('plan_stale' if host.clock() <= plan['deadline'] else 'plan_deadline')
            return
        if plan['request_id'] is not None:
            receipt = host._result_history.get(plan['request_id'])
            if receipt is None:
                return
            if receipt.status in ('failed', 'rejected', 'cancelled', 'unknown', 'unsupported'):
                plan['results'].append(receipt.public())
                plan.update(status='failed', code=receipt.code)
                self.last, self.active = plan, None
                return
            if receipt.status not in ('succeeded', 'simulated') or not receipt.completed:
                return
            plan['results'].append(receipt.public())
            plan['index'] += 1
            plan['request_id'] = None
            if plan['index'] == len(plan['steps']):
                plan.update(status='succeeded', code='plan_completed')
                self.last, self.active = plan, None
                return
        # Exactly one dispatch per owner tick. No retries, even if journal or
        # device completion is uncertain. IDs originate here, never in JSON.
        if host.session.busy or host.audio.owner is not None:
            return
        step = plan['steps'][plan['index']]
        request = 'spoken_device_'+uuid5(host._namespace,
            plan['parent_request']+':'+str(plan['index'])).hex
        plan['request_id'] = request
        host._next_photo_success = plan['photo_success'] if step.name == 'take_photo' else None
        host._next_photo_preparation = plan['preparation'] if step.name == 'take_photo' else None
        try:
            host.execute(step, request_id=request, expected_generation=plan['generation'])
        finally:
            host._next_photo_success = host._next_photo_preparation = None
        plan['generation'] = host.modes.generation

    def snapshot(self):
        plan = self.active or self.last
        if plan is None:
            return None
        return {key: value for key, value in plan.items()
                if key not in ('steps', 'photo_success', 'preparation', 'epoch', 'deadline')} | {
                    'steps': [step.public() for step in plan['steps']]}
