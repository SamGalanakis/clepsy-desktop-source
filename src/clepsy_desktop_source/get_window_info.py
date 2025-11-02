import time
import json
import subprocess
import shutil
import os
from functools import lru_cache
from typing import Tuple, List, Optional

from loguru import logger

from clepsy_desktop_source.entities import WindowInfo, Bbox
from clepsy_desktop_source.config import (
    PlatformType,
    DisplayServerType,
    config,
)


def intersection(a: Bbox, b: Bbox) -> int:
    x1 = max(a.left, b.left)
    y1 = max(a.top, b.top)
    x2 = min(a.left + a.width, b.left + b.width)
    y2 = min(a.top + a.height, b.top + b.height)
    return max(0, x2 - x1) * max(0, y2 - y1)


def active_window_likely_relevant(
    window: WindowInfo, monitor_boxes: List[Bbox]
) -> bool:
    window_width = window.bbox.width
    window_height = window.bbox.height
    MIN_SIDE_PX = 200
    ASPECT_MIN, ASPECT_MAX = 0.25, 4.0
    AREA_RATIO_MIN = 0.10
    window_bbox = Bbox(
        left=window.bbox.left,
        top=window.bbox.top,
        width=window_width,
        height=window_height,
    )

    if window_width <= 0 or window_height <= 0:
        logger.debug(
            "Active window '{}' has non-positive dimensions: %dx%d",
            window.title,
            window_width,
            window_height,
        )
        return False

    if not (ASPECT_MIN <= window_bbox.width / window_bbox.height <= ASPECT_MAX):
        logger.debug(
            "Active window '{}' has aspect ratio {}, not in range [{}, {}]",
            window.title,
            window_bbox.width / window_bbox.height,
            ASPECT_MIN,
            ASPECT_MAX,
        )
        return False

    if min(window_bbox.width, window_bbox.height) < MIN_SIDE_PX:
        logger.debug(
            "Active window '{}' is too small: {}x{} < {}x{}",
            window.title,
            window_bbox.width,
            window_bbox.height,
            MIN_SIDE_PX,
            MIN_SIDE_PX,
        )
        return False

    for mon_box in monitor_boxes:
        if (
            intersection(mon_box, window_bbox) / (mon_box.width * mon_box.height)
            >= AREA_RATIO_MIN
        ):
            logger.debug(
                "Active window '{}' is sufficiently visible on monitor {}",
                window.title,
                mon_box,
            )
            return True

    visible = sum(intersection(window_bbox, m) for m in monitor_boxes)
    ref_area = max(m.width * m.height for m in monitor_boxes)  # normalise

    if not (visible / ref_area >= AREA_RATIO_MIN):
        logger.debug(
            "Active window {}' is not sufficiently visible on monitors: {} < {}",
            window.title,
            visible / ref_area,
            AREA_RATIO_MIN,
        )
        return False

    return True


@lru_cache(maxsize=100)
def get_monitor_box(monitor_name: str) -> Bbox:
    # Lazy import to avoid triggering X11/xrandr probes on Wayland
    from pymonctl import findMonitorWithName as _findMonitorWithName  # type: ignore

    monitor = _findMonitorWithName(monitor_name)
    assert monitor, f"Monitor '{monitor_name}' not found"

    assert monitor.box, f"Monitor '{monitor_name}' has no box"
    return Bbox(
        left=monitor.box.left,
        top=monitor.box.top,
        width=monitor.box.width,
        height=monitor.box.height,
    )


class WindowInfoProviderBase:
    def get_active_window_and_monitor_boxes(
        self, retries: int, retry_cooldown: float
    ) -> Tuple[Optional[WindowInfo], List[Bbox]]:
        raise NotImplementedError


class PyWinctlWindowInfoProvider(WindowInfoProviderBase):
    def get_active_window_and_monitor_boxes(
        self, retries: int, retry_cooldown: float
    ) -> Tuple[Optional[WindowInfo], List[Bbox]]:
        # Lazy import to avoid import-time side effects on Wayland
        import pywinctl  # type: ignore

        for attempt in range(retries):
            win = pywinctl.getActiveWindow()
            if not win or not win.isAlive:
                time.sleep(retry_cooldown)
                continue
            try:
                bbox = Bbox(
                    left=win.left,
                    top=win.top,
                    width=win.size.width,
                    height=win.size.height,
                )
                monitor_names = win.getMonitor()
                window = WindowInfo(
                    title=win.title,
                    is_active=True,
                    app_name=win.getAppName(),
                    bbox=bbox,
                    monitor_names=monitor_names,
                )
                boxes = [get_monitor_box(name) for name in monitor_names]
                return window, boxes
            except (KeyError, AttributeError, ValueError) as exc:
                logger.warning(
                    "Active window vanished on attempt {}/{}: {}",
                    attempt + 1,
                    retries,
                    exc,
                )
                time.sleep(retry_cooldown)
        logger.error("Failed to capture a live active window after {} tries", retries)
        return None, []


class HyprlandWindowInfoProvider(WindowInfoProviderBase):
    def run(self, args: List[str]) -> Optional[dict]:
        try:
            logger.debug("hyprctl exec: {}", " ".join(args))
            res = subprocess.run(
                args,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.debug(
                "hyprctl returned {} bytes for {}",
                len(res.stdout or ""),
                args[-1] if args else "?",
            )
            return json.loads(res.stdout)
        except (
            subprocess.CalledProcessError,
            json.JSONDecodeError,
            OSError,
            ValueError,
        ) as exc:  # pragma: no cover
            logger.debug("hyprctl call failed: {}", exc)
            return None

    def get_active_window_and_monitor_boxes(
        self, retries: int, retry_cooldown: float
    ) -> Tuple[Optional[WindowInfo], List[Bbox]]:
        if not shutil.which("hyprctl"):
            logger.debug("hyprctl not found in PATH; Hyprland provider unavailable")
            raise NotImplementedError("hyprctl not found in PATH")

        for attempt in range(retries):
            logger.debug("Hyprland poll attempt {}/{}", attempt + 1, retries)
            win_json = self.run(["hyprctl", "-j", "activewindow"]) or {}
            mons_json = self.run(["hyprctl", "-j", "monitors"]) or []
            if not win_json:
                logger.debug(
                    "Hyprland: no activewindow JSON; retrying after {}s", retry_cooldown
                )
                time.sleep(retry_cooldown)
                continue

            try:
                title = win_json.get("title") or ""
                app_name = win_json.get("class") or ""
                at = win_json.get("at") or [0, 0]
                size = win_json.get("size") or [0, 0]
                mon_ref = win_json.get("monitor")

                logger.debug(
                    "Hyprland activewindow: title='{}' class='{}' at={} size={} monitor_ref={}",
                    title,
                    app_name,
                    at,
                    size,
                    mon_ref,
                )

                # Resolve monitor
                monitor_name = None
                monitor_box = None
                if isinstance(mon_ref, str):
                    logger.debug("Hyprland: monitor ref is name '{}'", mon_ref)
                    monitor_name = mon_ref
                    for m in mons_json:
                        if m.get("name") == monitor_name:
                            monitor_box = Bbox(
                                left=int(m.get("x", 0)),
                                top=int(m.get("y", 0)),
                                width=int(m.get("width", 0)),
                                height=int(m.get("height", 0)),
                            )
                            logger.debug(
                                "Hyprland: resolved monitor '{}' -> {}",
                                monitor_name,
                                monitor_box,
                            )
                            break
                elif isinstance(mon_ref, int) and 0 <= mon_ref < len(mons_json):
                    logger.debug("Hyprland: monitor ref is index {}", mon_ref)
                    m = mons_json[mon_ref]
                    monitor_name = m.get("name") or f"monitor-{mon_ref}"
                    monitor_box = Bbox(
                        left=int(m.get("x", 0)),
                        top=int(m.get("y", 0)),
                        width=int(m.get("width", 0)),
                        height=int(m.get("height", 0)),
                    )
                    logger.debug(
                        "Hyprland: resolved monitor index {} -> '{}' / {}",
                        mon_ref,
                        monitor_name,
                        monitor_box,
                    )

                # Compute absolute bbox: monitor origin + window offset
                left = (monitor_box.left if monitor_box else 0) + int(at[0])
                top = (monitor_box.top if monitor_box else 0) + int(at[1])
                width = int(size[0])
                height = int(size[1])

                bbox = Bbox(left=left, top=top, width=width, height=height)
                logger.debug("Hyprland: computed window bbox {}", bbox)
                window = WindowInfo(
                    title=title,
                    is_active=True,
                    app_name=app_name,
                    bbox=bbox,
                    monitor_names=[monitor_name] if monitor_name else [],
                )

                boxes: List[Bbox] = []
                if monitor_box:
                    boxes = [monitor_box]
                else:
                    # Fallback: all monitors
                    boxes = [
                        Bbox(
                            left=int(m.get("x", 0)),
                            top=int(m.get("y", 0)),
                            width=int(m.get("width", 0)),
                            height=int(m.get("height", 0)),
                        )
                        for m in mons_json
                    ]
                logger.debug(
                    "Hyprland: returning window '{}' on monitors: {}", title, boxes
                )
                return window, boxes
            except (KeyError, AttributeError, ValueError) as exc:
                logger.warning(
                    "Hyprland window parse failed on attempt {}/{}: {}",
                    attempt + 1,
                    retries,
                    exc,
                )
                time.sleep(retry_cooldown)

        logger.error(
            "Hyprland: failed to capture active window after {} tries", retries
        )
        return None, []


class SwayWindowInfoProvider(WindowInfoProviderBase):
    def run(self, args: List[str]) -> Optional[dict]:
        try:
            res = subprocess.run(
                args,
                check=True,
                capture_output=True,
                text=True,
            )
            return json.loads(res.stdout)
        except (
            subprocess.CalledProcessError,
            json.JSONDecodeError,
            OSError,
            ValueError,
        ) as exc:  # pragma: no cover
            logger.debug("swaymsg call failed: {}", exc)
            return None

    def find_focused(self, node: dict) -> Optional[dict]:
        if not isinstance(node, dict):
            return None
        if node.get("focused") and node.get("rect"):
            return node
        for k in ("nodes", "floating_nodes"):
            for child in node.get(k, []) or []:
                found = self.find_focused(child)
                if found:
                    return found
        return None

    def get_active_window_and_monitor_boxes(
        self, retries: int, retry_cooldown: float
    ) -> Tuple[Optional[WindowInfo], List[Bbox]]:
        if not shutil.which("swaymsg"):
            raise NotImplementedError("swaymsg not found in PATH")

        for attempt in range(retries):
            tree = self.run(["swaymsg", "-t", "get_tree"]) or {}
            outputs = self.run(["swaymsg", "-t", "get_outputs"]) or []
            if not tree:
                time.sleep(retry_cooldown)
                continue
            try:
                focused = self.find_focused(tree)
                if not focused:
                    time.sleep(retry_cooldown)
                    continue
                rect = focused.get("rect") or {}
                left, top = int(rect.get("x", 0)), int(rect.get("y", 0))
                width, height = int(rect.get("width", 0)), int(rect.get("height", 0))

                title = focused.get("name") or ""
                app_name = (
                    focused.get("app_id")
                    or (focused.get("window_properties") or {}).get("class", "")
                    or ""
                )

                bbox = Bbox(left=left, top=top, width=width, height=height)

                # Determine monitor by window center
                cx, cy = left + width // 2, top + height // 2
                mon_names: List[str] = []
                mon_boxes: List[Bbox] = []
                for out in outputs:
                    n = out.get("name") or ""
                    r = out.get("rect") or {}
                    ox, oy = int(r.get("x", 0)), int(r.get("y", 0))
                    ow, oh = int(r.get("width", 0)), int(r.get("height", 0))
                    if ox <= cx < ox + ow and oy <= cy < oy + oh:
                        mon_names = [n]
                        mon_boxes = [Bbox(left=ox, top=oy, width=ow, height=oh)]
                        break
                window = WindowInfo(
                    title=title,
                    is_active=True,
                    app_name=app_name,
                    bbox=bbox,
                    monitor_names=mon_names,
                )
                return window, mon_boxes or [
                    Bbox(
                        left=int((out.get("rect") or {}).get("x", 0)),
                        top=int((out.get("rect") or {}).get("y", 0)),
                        width=int((out.get("rect") or {}).get("width", 0)),
                        height=int((out.get("rect") or {}).get("height", 0)),
                    )
                    for out in outputs
                ]
            except (KeyError, AttributeError, ValueError) as exc:
                logger.warning(
                    "Sway window parse failed on attempt {}/{}: {}",
                    attempt + 1,
                    retries,
                    exc,
                )
                time.sleep(retry_cooldown)

        logger.error("Sway: failed to capture active window after {} tries", retries)
        return None, []


class UnsupportedWaylandProvider(WindowInfoProviderBase):
    def get_active_window_and_monitor_boxes(
        self, retries: int, retry_cooldown: float
    ) -> Tuple[Optional[WindowInfo], List[Bbox]]:
        raise NotImplementedError(
            "Wayland compositor without window IPC (e.g., GNOME/KDE) is not supported yet"
        )


def detect_wayland_provider() -> WindowInfoProviderBase:
    # Hyprland indicator
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or shutil.which("hyprctl"):
        return HyprlandWindowInfoProvider()
    # Sway indicator
    if os.environ.get("SWAYSOCK") or shutil.which("swaymsg"):
        return SwayWindowInfoProvider()
    # KDE/GNOME hints
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    session = (os.environ.get("DESKTOP_SESSION") or "").lower()
    if "gnome" in desktop or "gnome" in session:
        return UnsupportedWaylandProvider()
    if "kde" in desktop or os.environ.get("KDE_FULL_SESSION"):
        return UnsupportedWaylandProvider()
    return UnsupportedWaylandProvider()


def create_window_info_provider(
    platform_type: PlatformType, display_server: DisplayServerType
) -> WindowInfoProviderBase:
    match platform_type:
        case PlatformType.WINDOWS | PlatformType.MACOS:
            return PyWinctlWindowInfoProvider()
        case PlatformType.LINUX:
            match display_server:
                case DisplayServerType.X11:
                    return PyWinctlWindowInfoProvider()
                case DisplayServerType.WAYLAND:
                    return detect_wayland_provider()
                case _:
                    return PyWinctlWindowInfoProvider()
        case _:
            return PyWinctlWindowInfoProvider()


def get_active_window_info(retries: int, retry_cooldown: float) -> Optional[WindowInfo]:
    provider = create_window_info_provider(config.platform, config.display_server)
    try:
        window, _boxes = provider.get_active_window_and_monitor_boxes(
            retries, retry_cooldown
        )
        return window
    except NotImplementedError as exc:
        logger.warning("Active window not supported on this compositor: {}", exc)
        return None


def get_active_window_if_relevant(
    retries: int = 3,
    retry_cooldown: float = 1.0,
) -> Optional[WindowInfo]:
    provider = create_window_info_provider(config.platform, config.display_server)
    try:
        window, monitor_boxes = provider.get_active_window_and_monitor_boxes(
            retries, retry_cooldown
        )
    except NotImplementedError as exc:
        logger.warning("Active window not supported on this compositor: {}", exc)
        return None
    if not window:
        return None
    is_relevant = active_window_likely_relevant(window, monitor_boxes)
    if is_relevant:
        return window
    logger.debug("Active window '{}' is not relevant", window.title)
    return None
