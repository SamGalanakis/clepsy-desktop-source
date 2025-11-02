import asyncio
import time
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from typing import Dict

from PIL import Image

from clepsy_desktop_source.entities import DesktopCheck, WindowInfo, AfkStart
from clepsy_desktop_source.get_window_info import get_active_window_if_relevant
from clepsy_desktop_source.idle_detector import create_idle_detector
from clepsy_desktop_source.config import config
from loguru import logger
from clepsy_desktop_source.screenshotter import create_screenshotter


def window_to_hash(window: WindowInfo) -> str:
    return f"{window.app_name}_{window.title}"


def same_window(a: WindowInfo, b: WindowInfo | None) -> bool:
    return b is not None and window_to_hash(a) == window_to_hash(b)


async def screenshot_and_publish(
    window: WindowInfo,
    publish_queue: asyncio.Queue[DesktopCheck | AfkStart],
    capture_image,
) -> None:
    try:
        img: Image.Image = await capture_image(window)
    except Exception as exc:
        logger.error(
            "Failed to grab screenshot for window {}: {}",
            window.title,
            exc,
            exc_info=True,
        )
        raise
    img.thumbnail(config.screenshot_size)
    logger.debug(
        "Captured screenshot of window {},app {} of size {}x{}",
        window.title,
        window.app_name,
        img.width,
        img.height,
    )
    await publish_queue.put(
        DesktopCheck(
            screenshot=img,
            active_window=window,
            bbox=window.bbox,
            time_since_last_user_activity=timedelta(
                seconds=0
            ),  # filled later if needed
            timestamp=datetime.now(timezone.utc),
        )
    )


async def data_generator_worker(
    publish_queue: asyncio.Queue[DesktopCheck | AfkStart],
) -> None:
    while True:
        # Wait until paired/configured and active
        if not (
            config.user.clepsy_backend_url
            and config.user.device_token
            and config.user.active
        ):
            await asyncio.sleep(2.0)
            continue

        # Find first active window (with early exit if pairing lost)
        first: WindowInfo | None = None
        while first is None:
            if not (
                config.user.clepsy_backend_url
                and config.user.device_token
                and config.user.active
            ):
                break  # pairing or active status lost; go back to waiting
            first = get_active_window_if_relevant(
                retry_cooldown=config.active_window_poll_interval.total_seconds(),
                retries=3,
            )
            if first is None:
                logger.warning("No active window detected – retrying…")
                await asyncio.sleep(config.active_window_poll_interval.total_seconds())
        if first is None:
            # pairing lost or still no window; retry outer loop
            continue

        # Create idle backend now that we're paired (context manages lifecycle)
        idle = create_idle_detector(config.platform, config.display_server)

        prev_window: WindowInfo = first
        last_shot_ts: float = time.monotonic()  # last screenshot taken
        last_change_ts: float = time.monotonic()  # last time foreground window changed
        prev_hash: str = window_to_hash(prev_window)
        window_hash_last_seen: Dict[str, float] = OrderedDict({prev_hash: last_shot_ts})
        is_afk = False

        # Create screenshotter based on platform/display
        try:
            screenshotter = create_screenshotter(config.platform, config.display_server)
        except NotImplementedError as exc:
            logger.warning(
                "Screenshots not supported on this platform/display: {}",
                exc,
            )
            # Back off a bit before trying again (still allow AFK/window polling next loop)
            await asyncio.sleep(config.active_window_poll_interval.total_seconds())
            continue

        # Manage screenshotter lifecycle across the loop
        async with screenshotter:

            async def capture(win: WindowInfo) -> Image.Image:
                return await screenshotter.capture_window(win)

            # Initial shot (don't crash the worker if it fails)
            try:
                await screenshot_and_publish(prev_window, publish_queue, capture)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("Initial screenshot failed: {}", exc, exc_info=True)

            async with idle:
                while True:
                    # Stop capture immediately if pairing is cleared or user paused (inactive)
                    if not (
                        config.user.clepsy_backend_url
                        and config.user.device_token
                        and config.user.active
                    ):
                        break
                    try:
                        now = time.monotonic()
                        idle_secs = await idle.get_idle_seconds()

                        # -------- AFK detection ------------------------------------
                        if idle_secs > config.afk_timeout.total_seconds():
                            if not is_afk:
                                await publish_queue.put(
                                    AfkStart(
                                        timestamp=datetime.now(timezone.utc),
                                        time_since_last_user_activity=timedelta(
                                            seconds=idle_secs
                                        ),
                                    )
                                )
                                is_afk = True
                            await asyncio.sleep(
                                config.active_window_poll_interval.total_seconds()
                            )
                            continue
                        else:
                            is_afk = False

                        # -------- Global cooldown -----------------------------------
                        if now - last_shot_ts < config.global_cd.total_seconds():
                            await asyncio.sleep(
                                config.active_window_poll_interval.total_seconds()
                            )
                            continue

                        # -------- Active window -------------------------------------
                        cur_window = get_active_window_if_relevant(
                            retry_cooldown=0.02, retries=3
                        )
                        if cur_window is None:
                            logger.warning(
                                "No active window found – skipping iteration"
                            )
                            await asyncio.sleep(
                                config.active_window_poll_interval.total_seconds()
                            )
                            continue

                        cur_hash = window_to_hash(cur_window)

                        # update last_change_ts whenever the foreground window hash changes
                        if cur_hash != prev_hash:
                            last_change_ts = now
                            prev_hash = cur_hash

                        elapsed_since_shot = now - last_shot_ts
                        elapsed_constant = now - last_change_ts

                        # -------- Same‑window cooldown ------------------------------
                        last_seen = window_hash_last_seen.get(cur_hash)
                        if (
                            last_seen
                            and now - last_seen < config.same_window_cd.total_seconds()
                        ):
                            await asyncio.sleep(
                                config.active_window_poll_interval.total_seconds()
                            )
                            continue

                        # -------- Rule A: focus change shot -------------------------
                        if elapsed_constant < config.global_cd.total_seconds():
                            pass  # fallthrough → shoot
                        # -------- Rule B: constant‑window heartbeat -----------------
                        elif (
                            elapsed_since_shot
                            < config.constant_window_cd.total_seconds()
                        ):
                            await asyncio.sleep(
                                config.active_window_poll_interval.total_seconds()
                            )
                            continue

                        # ------------------- TAKE SCREENSHOT ------------------------
                        await screenshot_and_publish(cur_window, publish_queue, capture)

                        last_shot_ts = now
                        window_hash_last_seen[cur_hash] = now

                        # prune LRU dict to avoid unbounded growth
                        if len(window_hash_last_seen) > 1000:
                            window_hash_last_seen.popitem(last=False)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.error("Capture loop error: {}", exc, exc_info=True)
                    finally:
                        await asyncio.sleep(
                            config.active_window_poll_interval.total_seconds()
                        )
        # idle and screenshotter contexts auto-clean up here
