import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from app.activities import (
    create_approval_activity,
    emit_run_event_activity,
    invoke_graph_activity,
    notify_frontend_activity,
    resume_graph_activity,
    set_run_status_activity,
)
from app.db import init_db
from app.commerce.service import expire_unused_cards
from app.settings import get_settings
from app.workflows import RelayFlowWorkflow


async def main() -> None:
    settings = get_settings()
    await init_db()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[RelayFlowWorkflow],
        activities=[
            create_approval_activity,
            emit_run_event_activity,
            invoke_graph_activity,
            notify_frontend_activity,
            resume_graph_activity,
            set_run_status_activity,
        ],
    )
    async def cleanup_expired_cards() -> None:
        while True:
            try:
                await expire_unused_cards()
            except Exception:
                logging.exception("Expired-card cleanup failed")
            await asyncio.sleep(60)

    await asyncio.gather(worker.run(), cleanup_expired_cards())


if __name__ == "__main__":
    asyncio.run(main())

