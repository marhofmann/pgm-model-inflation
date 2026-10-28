"""Idle guard for the benchmark runner: wait until the machine is idle before a run starts.

Idle = mean CPU load below ``LIMIT`` % over ``WINDOW`` seconds (measured with psutil). Every wait is
appended to ``logs/idle_log.txt`` next to this file (the ``logs`` directory is not committed), so the
log shows how often a run had to wait for other work.

Limits, stated plainly:
- The guard only checks BEFORE each run. It does not stop other work from starting while a run is
  going, and it does not detect that load appeared during a run. A run that overlapped with other
  work looks the same in the result file as one that did not.
- Even when the machine is idle, the check itself takes ``WINDOW`` seconds, so every run waits at
  least that long.
"""

import time
from pathlib import Path

import psutil

LIMIT: float = 10.0  # percent CPU, averaged over WINDOW
WINDOW: int = 3  # seconds
LOG: Path = Path(__file__).resolve().parent / "logs" / "idle_log.txt"


def wait_idle(tag: str = "", log: Path = LOG) -> int:
    """Block until the mean CPU load over ``WINDOW`` seconds is below ``LIMIT`` percent.

    Each busy window is appended to ``log`` as one line (time, load, ``tag``).

    Args:
        tag: Free text for the log line, e.g. the experiment, round and build about to run.
        log: Log file; its directory is created if needed.

    Returns:
        The number of seconds spent waiting because the machine was busy (0 if it was idle at once).
    """
    waited = 0
    while True:
        cpu = psutil.cpu_percent(interval=WINDOW)
        if cpu < LIMIT:
            break
        waited += WINDOW
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} busy {cpu:.1f} %, waiting {tag}".rstrip() + "\n")
    return waited
