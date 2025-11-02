import io
import json
import os
import shutil
import subprocess
from abc import ABC, abstractmethod

from PIL import Image
from loguru import logger

from clepsy_desktop_source.config import DisplayServerType, PlatformType
from clepsy_desktop_source.entities import WindowInfo, Bbox


class ScreenshotterBase(ABC):
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def cleanup(self) -> None:
        pass

    async def __aenter__(self):
        self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            self.stop()
        finally:
            self.cleanup()
        return False

    @abstractmethod
    async def capture_window(self, window: WindowInfo) -> Image.Image:
        raise NotImplementedError


class MssScreenshotter(ScreenshotterBase):
    def __init__(self) -> None:
        # Cache the import so we don't import on each start/capture
        import mss as mss_module  # type: ignore

        self.mss = mss_module
        self.sct = None

    def start(self) -> None:
        if self.sct is None:
            # Use cached module from __init__
            self.sct = self.mss.mss()

    def stop(self) -> None:
        if self.sct is not None:
            try:
                self.sct.close()
            except OSError:
                pass
            self.sct = None

    def cleanup(self) -> None:
        self.stop()

    async def capture_window(self, window: WindowInfo) -> Image.Image:
        if self.sct is None:
            self.start()
        bbox: Bbox = window.bbox
        region = {
            "top": bbox.top,
            "left": bbox.left,
            "width": bbox.width,
            "height": bbox.height,
        }
        assert self.sct is not None
        raw = self.sct.grab(region)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        return img


class GrimScreenshotter(ScreenshotterBase):
    def __init__(self) -> None:
        if not shutil.which("grim"):
            raise RuntimeError(
                "grim is required on Wayland (wlroots) for screenshots; please install it."
            )

    async def capture_window(self, window: WindowInfo) -> Image.Image:
        bbox: Bbox = window.bbox
        if bbox.width <= 0 or bbox.height <= 0:
            raise ValueError(f"Invalid bbox size for grim: {bbox}")

        # Clamp to Wayland layout bounds (union of outputs) to avoid OOB
        left, top, width, height = bbox.left, bbox.top, bbox.width, bbox.height
        layout = get_wl_layout_bounds()
        if layout is not None:
            lx, ly, lw, lh = layout
            new_left = max(left, lx)
            new_top = max(top, ly)
            new_right = min(left + width, lx + lw)
            new_bottom = min(top + height, ly + lh)
            new_w = max(0, new_right - new_left)
            new_h = max(0, new_bottom - new_top)
            if (new_left, new_top, new_w, new_h) != (left, top, width, height):
                logger.debug(
                    "Grim clamp: bbox=({}, {}, {}x{}) layout=({}, {}, {}x{}) -> ({} , {}) {}x{}",
                    left,
                    top,
                    width,
                    height,
                    lx,
                    ly,
                    lw,
                    lh,
                    new_left,
                    new_top,
                    new_w,
                    new_h,
                )
            left, top, width, height = new_left, new_top, new_w, new_h
        if width <= 0 or height <= 0:
            raise ValueError(
                f"Clamped bbox has non-positive size: left={left} top={top} w={width} h={height}"
            )

        geometry = f"{left},{top} {width}x{height}"
        # grim -g "x,y WxH" -  -> PNG on stdout
        try:
            logger.debug("Running grim with geometry: {}", geometry)
            res = subprocess.run(
                ["grim", "-g", geometry, "-"],
                check=True,
                capture_output=True,
                timeout=5,
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning("grim timed out for geometry {}: {}", geometry, exc)
            raise
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "grim failed with code {} for geometry {}: {}",
                exc.returncode,
                geometry,
                exc.stderr.decode(errors="ignore") if exc.stderr else exc,
            )
            raise
        logger.debug("Grim command completed successfully.")
        buf = io.BytesIO(res.stdout)
        image = Image.open(buf)
        image.load()  # ensure data fully read
        image = image.convert("RGB")

        logger.debug("Captured image size: {}x{}", image.width, image.height)
        return image


def is_wlroots_env() -> bool:
    # Heuristics similar to window info provider detection
    # Sway/Hyprland indicators
    return bool(
        shutil.which("grim")
        and (
            shutil.which("swaymsg")
            or shutil.which("hyprctl")
            or ("SWAYSOCK" in os.environ)
            or ("HYPRLAND_INSTANCE_SIGNATURE" in os.environ)
        )
    )


def get_wl_layout_bounds() -> tuple[int, int, int, int] | None:
    # Hyprland
    if shutil.which("hyprctl"):
        try:
            res = subprocess.run(
                ["hyprctl", "-j", "monitors"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            mons = json.loads(res.stdout)
            if isinstance(mons, list) and mons:
                xs: list[int] = []
                ys: list[int] = []
                xe: list[int] = []
                ye: list[int] = []
                for m in mons:
                    x = int(m.get("x", 0))
                    y = int(m.get("y", 0))
                    w = int(m.get("width", 0))
                    h = int(m.get("height", 0))
                    xs.append(x)
                    ys.append(y)
                    xe.append(x + w)
                    ye.append(y + h)
                lx, ly = min(xs), min(ys)
                rx, by = max(xe), max(ye)
                return lx, ly, max(0, rx - lx), max(0, by - ly)
        except Exception as exc:  # pragma: no cover
            logger.debug("Failed to get Hyprland layout bounds: {}", exc)
    # Sway
    if shutil.which("swaymsg"):
        try:
            res = subprocess.run(
                ["swaymsg", "-t", "get_outputs"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            outs = json.loads(res.stdout)
            if isinstance(outs, list) and outs:
                xs: list[int] = []
                ys: list[int] = []
                xe: list[int] = []
                ye: list[int] = []
                for o in outs:
                    r = o.get("rect") or {}
                    x = int(r.get("x", 0))
                    y = int(r.get("y", 0))
                    w = int(r.get("width", 0))
                    h = int(r.get("height", 0))
                    xs.append(x)
                    ys.append(y)
                    xe.append(x + w)
                    ye.append(y + h)
                lx, ly = min(xs), min(ys)
                rx, by = max(xe), max(ye)
                return lx, ly, max(0, rx - lx), max(0, by - ly)
        except Exception as exc:  # pragma: no cover
            logger.debug("Failed to get Sway layout bounds: {}", exc)
    return None


def create_screenshotter(
    platform_type: PlatformType, display_server: DisplayServerType
) -> ScreenshotterBase:
    match platform_type:
        case PlatformType.WINDOWS | PlatformType.MACOS:
            logger.debug("Using screenshotter: MSS ({} platform)", platform_type)
            return MssScreenshotter()
        case PlatformType.LINUX:
            match display_server:
                case DisplayServerType.X11:
                    logger.debug("Using screenshotter: MSS (Linux X11)")
                    return MssScreenshotter()
                case DisplayServerType.WAYLAND:
                    # wlroots (Sway/Hyprland) -> grim; GNOME/KDE -> unsupported
                    if is_wlroots_env():
                        try:
                            logger.debug("Using screenshotter: grim (Wayland wlroots)")
                            return GrimScreenshotter()
                        except RuntimeError as exc:
                            # grim missing or not runnable
                            raise NotImplementedError(
                                "grim is required on wlroots Wayland for unattended screenshots"
                            ) from exc
                    # Non-wlroots Wayland (e.g., GNOME/KDE) cannot capture unattended
                    raise NotImplementedError(
                        "Unattended screenshots on GNOME/KDE Wayland are not possible without portal consent"
                    )
                case _:
                    logger.debug("Using screenshotter: MSS (Linux unknown display)")
                    return MssScreenshotter()
        case _:
            logger.debug("Using screenshotter: MSS (unknown platform)")
            return MssScreenshotter()
