"""Run the scheduler as a standalone daemon:  python -m app.scheduler

Useful when you chat through the CLI and don't keep the API running. Don't run this *and* the API with
SCHEDULER_ENABLED=true against the same database: you would get every check-in twice (dedupe would catch most,
but there's no reason to take the chance).
"""
import logging
import time

from app.container import build_container

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

if __name__ == "__main__":
    c = build_container()
    c.scheduler.start()
    print("Scheduler running. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        c.scheduler.stop()
