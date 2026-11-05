"""
Dedicated scheduler + WhatsApp queue worker for scaled deployments:

    RUN_SCHEDULER=false on API replicas
    python -m app.worker   (one or more; a Redis leader lock keeps scheduler jobs single-run,
                             and a per-contact Redis lock keeps WhatsApp replies ordered)
"""

import asyncio
import signal
import threading

from app.core.config import settings
from app.core.database import run_migrations
from app.core.logging import get_logger, setup_logging
from app.services.scheduler import scheduler_loop
from app.services.whatsapp_service import queue_worker_loop
from app.services.instagram_service import queue_worker_loop as ig_queue_worker_loop

setup_logging(settings.log_level, json_logs=settings.is_production)
log = get_logger("worker")


async def main():
    if settings.run_migrations:
        await asyncio.to_thread(run_migrations)
    task = asyncio.create_task(scheduler_loop())
    queue_stop = threading.Event()
    queue_task = asyncio.create_task(asyncio.to_thread(queue_worker_loop, queue_stop))
    ig_queue_task = asyncio.create_task(asyncio.to_thread(ig_queue_worker_loop, queue_stop))
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    await stop.wait()
    task.cancel()
    queue_stop.set()
    log.info("Worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
