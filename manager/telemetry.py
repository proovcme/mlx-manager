"""Nonblocking, single-flight system metrics, independent of workload ownership."""
import copy
import threading
import time


class Telemetry:
    def __init__(self, interval=5):
        self.interval = interval
        self._mu = threading.Lock()
        self._collecting = False
        self._updated = 0
        self._value = {'system': {'free_bytes': None, 'swap_used_bytes': None,
                                  'pressure_free_percent': None},
                       'external_chat': {'pid': None, 'other_listener_pid': None},
                       'image': {'processes': []}, 'proxy': None}
        self._error = None

    def snapshot(self, collect):
        with self._mu:
            if not self._collecting and time.monotonic() - self._updated >= self.interval:
                self._collecting = True
                threading.Thread(target=self._refresh, args=(collect,), daemon=True).start()
            return {**copy.deepcopy(self._value), 'metrics': {
                'age_seconds': round(time.monotonic()-self._updated, 1) if self._updated else None,
                'refreshing': self._collecting, 'error': self._error}}

    def _refresh(self, collect):
        try:
            value = collect()
            with self._mu:
                self._value = value
                self._error = None
        except Exception as exc:
            with self._mu:
                self._error = type(exc).__name__
        finally:
            with self._mu:
                self._updated = time.monotonic()
                self._collecting = False
