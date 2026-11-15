"""Private adapter candidate; not a caller-accessible source permission API.

Only an executor which owns the queue callback may call begin/bind. A standalone
instance does not prove that its caller supplied a real game/queue occurrence.
The later adapter binds this instance to one actual executor and ledger. The
reserve callback is internal and must not mutate the queue, snapshot, or ledger.
"""

from genshin_engine.shared_pools import _canonical

NATIVE_EVENTS = {'action': 'action_start', 'mechanics_end': 'action_end',
                 'mechanics_hit': 'hit', 'elemental_application': 'application',
                 'mechanics_condition': 'condition'}


class _Permit:
    __slots__ = ()


class ObservationError(ValueError):
    pass


class ObservationAuthority:
    def __init__(self, ledger, *, encode=None):
        self._ledger = ledger
        self._encode = encode or _canonical
        self._active = None
        self._semantic = None
        self._permit = None
        self._record = None
        self._busy = False
        self._published = False

    def _encoded(self, value):
        try:
            return self._encode(value)
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise ObservationError('observation_json') from exc

    def _available(self):
        if self._busy:
            raise ObservationError('observation_publish_busy')

    def begin(self, event):
        self._available()
        if self._active is not None:
            raise ObservationError('observation_nested_event')
        if (type(event) is not dict
                or any(type(event.get(k)) is not str or not event[k] for k in ('id', 'run_id', 'kind'))
                or any(type(event.get(k)) is not int or not 0 <= event[k] <= 10**15 for k in ('origin', 'time'))):
            raise ObservationError('observation_queue_identity')
        native = event['kind'] in NATIVE_EVENTS or event['kind'] == 'mechanics_snapshot_capture'
        encoded = self._encoded(event) if native else None
        self._active = event
        self._queue_encoded = encoded
        self._context = {'origin': event['origin'], 'run_id': event['run_id'],
                         'event_id': event['id'], 'time_us': event['time']}
        self._semantic = None
        self._permit = None
        self._record = None
        self._published = False

    def bind(self, semantic):
        self._available()
        if self._active is None or self._active['kind'] not in NATIVE_EVENTS or self._semantic is not None:
            raise ObservationError('observation_native_binding')
        self._check_identity(semantic, require_type=False)
        self._semantic = semantic

    def _check_identity(self, event, *, require_type=True):
        if (type(event) is not dict
                or type(event.get('origin')) is not int
                or event.get('id') != self._context['event_id']
                or event.get('origin') != self._context['origin']
                or event.get('run_id') != self._context['run_id']):
            raise ObservationError('observation_semantic_identity')
        if require_type and event.get('event') != NATIVE_EVENTS.get(self._active['kind']):
            raise ObservationError('observation_semantic_kind')

    def issue(self, event):
        self._available()
        if self._active is None or self._permit is not None or self._published:
            raise ObservationError('observation_issue_state')
        if self._encoded(self._active) != self._queue_encoded:
            raise ObservationError('observation_queue_changed')
        if self._active['kind'] == 'mechanics_snapshot_capture':
            if event is not self._active:
                raise ObservationError('observation_queue_object')
        elif self._active['kind'] in NATIVE_EVENTS:
            if event is not self._semantic:
                raise ObservationError('observation_semantic_object')
            self._check_identity(event)
        else:
            raise ObservationError('observation_kind')
        record = self._encoded(event)
        permit = _Permit()
        self._permit = permit
        self._record = (event, record)
        return permit

    def inspect(self, permit, ledger):
        self._available()
        if (self._active is None or permit is not self._permit or self._published
                or ledger is not self._ledger or self._record is None):
            raise ObservationError('observation_permit')
        if self._encoded(self._active) != self._queue_encoded:
            raise ObservationError('observation_queue_changed')
        observed, encoded = self._record
        if self._encoded(observed) != encoded:
            raise ObservationError('observation_semantic_changed')
        return dict(self._context)

    def publish(self, permit, ledger, work, reserve):
        context = self.inspect(permit, ledger)
        if type(work) is not int or not 0 < work <= 40000 or not callable(reserve):
            raise ObservationError('observation_work')
        self._busy = True
        try:
            reserve(work)
            # All validation/copy/serialization occurred before the one reserve.
            self._published = True
            return context
        finally:
            self._busy = False

    def end(self, event):
        self._available()
        if self._active is not event:
            raise ObservationError('observation_end_object')
        self._active = self._semantic = self._permit = self._record = None
