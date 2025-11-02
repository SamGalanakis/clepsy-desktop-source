import io
import json
from loguru import logger
from datetime import datetime
from urllib.parse import urljoin

import httpx
from asyncio import Queue

from clepsy_desktop_source.entities import DesktopCheck, AfkStart, AppState
from clepsy_desktop_source.config import config


async def send_desktop_check(
    event: DesktopCheck,
    client: httpx.AsyncClient,
    headers: dict,
    buffer: io.BytesIO,
    url: str,
):
    buffer.seek(0)
    buffer.truncate(0)
    event.screenshot.save(buffer, format="PNG")

    model_data = {
        "active_window": event.active_window.model_dump(),
        "timestamp": event.timestamp.isoformat(),
        "time_since_last_user_activity": event.time_since_last_user_activity.total_seconds(),
    }

    multipart_data = {
        "screenshot": ("screenshot.png", buffer, "image/png"),
        "data": (None, json.dumps(model_data), "application/json"),
    }

    response = await client.post(url, files=multipart_data, headers=headers)

    return response


async def send_afk_start(
    event: AfkStart, client: httpx.AsyncClient, headers: dict, url: str
):
    model_data = event.model_dump_json()
    response = await client.post(url, json=model_data, headers=headers)
    return response


async def request_sender_worker(
    queue: Queue[DesktopCheck | AfkStart],
    app_state: AppState,
):
    async with httpx.AsyncClient() as client:
        with io.BytesIO() as image_buffer:
            while True:
                event = await queue.get()

                # Skip sending if not paired/configured or not active
                if not (
                    config.user.clepsy_backend_url
                    and config.user.device_token
                    and config.user.active
                ):
                    queue.task_done()
                    continue

                headers = {}
                if config.user.device_token:
                    headers["Authorization"] = f"Bearer {config.user.device_token}"
                screenshot_url = urljoin(
                    config.user.clepsy_backend_url,
                    "/sources/aggregator/desktop/screenshot-input",
                )
                afk_notice_url = urljoin(
                    config.user.clepsy_backend_url,
                    "/sources/aggregator/desktop/afk-input",
                )

                response = None
                try:
                    if isinstance(event, DesktopCheck):
                        response = await send_desktop_check(
                            event, client, headers, image_buffer, screenshot_url
                        )
                    elif isinstance(event, AfkStart):
                        response = await send_afk_start(
                            event, client, headers, afk_notice_url
                        )
                    else:
                        logger.error(f"Unknown event type: {type(event)}")
                        raise ValueError(f"Unknown event type: {type(event)}")

                    response.raise_for_status()
                    logger.info(f"Response: {response.status_code}")
                    app_state.last_data_sent_timestamp = datetime.now()
                    app_state.last_data_sent_status = "Success"

                except httpx.HTTPStatusError as e:
                    logger.error(
                        f"Failed to send data: {e.response.status_code} - {e.response.text}"
                    )
                    app_state.last_data_sent_timestamp = datetime.now()
                    app_state.last_data_sent_status = "Fail"
                except Exception as e:
                    logger.error(f"Failed to send data: {e}")
                    app_state.last_data_sent_timestamp = datetime.now()
                    app_state.last_data_sent_status = "Fail"
                finally:
                    queue.task_done()
