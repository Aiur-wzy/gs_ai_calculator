"""Collect elapsed setup phases without coupling their work to the installer UI."""

from time import perf_counter


class InstallationTiming:
    def __init__(self, progress=None):
        self._progress = progress
        self._started = self._phase_started = perf_counter()
        self._phase = "preparing"
        self._seconds = {}

    def _record(self, now):
        self._seconds[self._phase] = self._seconds.get(self._phase, 0) + now - self._phase_started

    def __call__(self, event):
        phase = event.get("phase")
        if phase and phase != self._phase:
            now = perf_counter()
            self._record(now)
            self._phase = phase
            self._phase_started = now
        if self._progress is not None:
            self._progress(event)

    def finish(self):
        now = perf_counter()
        self._record(now)
        self._phase_started = now
        return {
            "elapsed_seconds": round(now - self._started, 3),
            "phase_seconds": {phase: round(seconds, 3) for phase, seconds in self._seconds.items()},
        }
