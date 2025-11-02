import asyncio
import os
import subprocess
import time
import uuid
from abc import ABC, abstractmethod
from typing import Optional, Any

from clepsy_desktop_source.config import DisplayServerType, PlatformType


class IdleDetectorBase(ABC):
    @property
    @abstractmethod
    def is_async(self) -> bool:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def cleanup(self) -> None:
        pass

    @abstractmethod
    async def get_idle_seconds(self) -> float:
        raise NotImplementedError

    async def __aenter__(self):
        self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            self.stop()
        finally:
            self.cleanup()
        return False


class PynputIdleDetector(IdleDetectorBase):
    def __init__(self) -> None:
        self.last_activity_monotonic: float = time.monotonic()
        self.listeners_started = False
        self.kb_listener = None
        self.mouse_listener = None

    @property
    def is_async(self) -> bool:
        return False

    def start(self) -> None:
        if self.listeners_started:
            return
        try:
            from pynput import keyboard, mouse  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "pynput is required for the Windows/macOS idle backend"
            ) from e

        def bump(_=None):
            self.last_activity_monotonic = time.monotonic()

        self.kb_listener = keyboard.Listener(on_press=bump, on_release=bump)
        self.mouse_listener = mouse.Listener(
            on_move=lambda *a: bump(),
            on_click=lambda *a: bump(),
            on_scroll=lambda *a: bump(),
        )
        self.kb_listener.start()
        self.mouse_listener.start()
        self.listeners_started = True

    def stop(self) -> None:
        if self.kb_listener:
            try:
                self.kb_listener.stop()
            except Exception:
                pass
        if self.mouse_listener:
            try:
                self.mouse_listener.stop()
            except Exception:
                pass
        self.kb_listener = None
        self.mouse_listener = None
        self.listeners_started = False

    def cleanup(self) -> None:
        self.stop()

    async def get_idle_seconds(self) -> float:
        if not self.listeners_started:
            raise RuntimeError("Call start() first on macOS/Windows (pynput backend).")
        return max(0.0, time.monotonic() - self.last_activity_monotonic)


class X11IdleDetector(IdleDetectorBase):
    @property
    def is_async(self) -> bool:
        return False

    @staticmethod
    def x11_idle_seconds_blocking() -> float:
        try:
            from Xlib import display  # type: ignore
            import Xlib.ext.xscreensaver  # type: ignore  # noqa: F401
        except Exception as e:
            raise RuntimeError(
                "X11 backend needs 'python-xlib'. Install with: pip install python-xlib"
            ) from e
        d = display.Display()
        root = d.screen().root
        info = Xlib.ext.xscreensaver.query_info(d, root)
        return float(info.idle) / 1000.0

    async def get_idle_seconds(self) -> float:
        return await asyncio.to_thread(self.x11_idle_seconds_blocking)


class WaylandIdleDetector(IdleDetectorBase):
    def __init__(self) -> None:
        self.portal_bus: Any | None = None
        self.portal_handler = None
        self.portal_idle_since_monotonic: float | None = None
        self.portal_failed = False
        self.logind_bus: Any | None = None
        self.logind_handler = None
        self.logind_session_path: str | None = None
        self.logind_idle_since_monotonic: float | None = None
        self.logind_failed = False

    @property
    def is_async(self) -> bool:
        return True

    def cleanup(self) -> None:
        if self.portal_bus:
            try:
                if self.portal_handler:
                    try:
                        self.portal_bus.remove_message_handler(self.portal_handler)
                    except Exception:
                        pass
                self.portal_bus.disconnect()
            except Exception:
                pass
            finally:
                self.portal_bus = None
                self.portal_handler = None
                self.portal_idle_since_monotonic = None
        if self.logind_bus:
            try:
                if self.logind_handler:
                    try:
                        self.logind_bus.remove_message_handler(self.logind_handler)
                    except Exception:
                        pass
                self.logind_bus.disconnect()
            except Exception:
                pass
            finally:
                self.logind_bus = None
                self.logind_handler = None
                self.logind_session_path = None
                self.logind_idle_since_monotonic = None

    async def ensure_portal_monitor(self) -> bool:
        if self.portal_failed:
            return False
        if self.portal_bus:
            return True

        try:
            from dbus_next.aio import MessageBus  # type: ignore
            from dbus_next import Message, MessageType  # type: ignore
            from dbus_next.signature import Variant  # type: ignore

            bus = await MessageBus().connect()
            token = f"clepsy{uuid.uuid4().hex}"
            options = {"session_handle_token": Variant("s", token)}
            msg = Message(
                destination="org.freedesktop.portal.Desktop",
                path="/org/freedesktop/portal/desktop",
                interface="org.freedesktop.portal.Inhibit",
                member="CreateMonitor",
                signature="sa{sv}",
                body=["", options],
            )
            reply: Any = await asyncio.wait_for(bus.call(msg), timeout=2.0)
            if getattr(reply, "message_type", None) != MessageType.METHOD_RETURN:
                raise RuntimeError("CreateMonitor failed")

            def handler(message: Any) -> None:
                if (
                    getattr(message, "message_type", None) != MessageType.SIGNAL
                    or getattr(message, "interface", None)
                    != "org.freedesktop.portal.Inhibit"
                    or getattr(message, "member", None) != "StateChanged"
                ):
                    return
                try:
                    _handle = message.body[0]
                    state = dict(message.body[1])
                except Exception:
                    return
                idle = bool(state.get("idle", False))
                since_ms = state.get("since", 0) or 0
                if idle:
                    try:
                        since_seconds = max(0.0, float(since_ms) / 1000.0)
                        self.portal_idle_since_monotonic = max(
                            0.0, time.monotonic() - since_seconds
                        )
                    except Exception:
                        self.portal_idle_since_monotonic = time.monotonic()
                else:
                    self.portal_idle_since_monotonic = None

            bus.add_message_handler(handler)
            self.portal_bus = bus
            self.portal_handler = handler
            self.portal_idle_since_monotonic = None
            return True
        except Exception:
            self.portal_failed = True
            if self.portal_bus:
                try:
                    self.portal_bus.disconnect()
                except Exception:
                    pass
                self.portal_bus = None
            return False

    async def ensure_logind_monitor(self) -> bool:
        if self.logind_failed:
            return False
        if self.logind_bus:
            return True

        bus: Any | None = None
        try:
            from dbus_next.aio import MessageBus  # type: ignore
            from dbus_next import Message, MessageType  # type: ignore
            from dbus_next.constants import BusType  # type: ignore

            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            list_msg = Message(
                destination="org.freedesktop.login1",
                path="/org/freedesktop/login1",
                interface="org.freedesktop.login1.Manager",
                member="ListSessions",
            )
            reply: Any = await asyncio.wait_for(bus.call(list_msg), timeout=2.0)
            sessions = reply.body[0] if reply is not None else []
            uid = os.getuid()
            username = os.getenv("USER", "")
            session_path: str | None = None
            for _sid, _uid_val, _user, _seat, objpath in sessions:
                if str(_uid_val) == str(uid) or _user == username:
                    session_path = objpath
                    break
            if not session_path:
                raise RuntimeError("logind session not found")

            match_rule = (
                "type='signal',sender='org.freedesktop.login1',"
                f"path='{session_path}',"
                "interface='org.freedesktop.DBus.Properties',"
                "member='PropertiesChanged'"
            )
            add_match = Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=[match_rule],
            )
            await asyncio.wait_for(bus.call(add_match), timeout=2.0)

            def handler(message: Any) -> None:
                if (
                    getattr(message, "message_type", None) != MessageType.SIGNAL
                    or getattr(message, "path", None) != session_path
                    or getattr(message, "interface", None)
                    != "org.freedesktop.DBus.Properties"
                    or getattr(message, "member", None) != "PropertiesChanged"
                ):
                    return
                try:
                    interface_name = message.body[0]
                    if interface_name != "org.freedesktop.login1.Session":
                        return
                    changed = dict(message.body[1])
                except Exception:  # pylint: disable=broad-except
                    return

                idle_hint_var = changed.get("IdleHint")
                if idle_hint_var is None:
                    return
                idle = (
                    idle_hint_var.value
                    if hasattr(idle_hint_var, "value")
                    else idle_hint_var
                )
                if idle:
                    since_var = changed.get("IdleSinceHintMonotonic")
                    if since_var is not None:
                        try:
                            since_val = (
                                since_var.value
                                if hasattr(since_var, "value")
                                else since_var
                            )
                            self.logind_idle_since_monotonic = max(
                                0.0, float(since_val) / 1_000_000.0
                            )
                            return
                        except Exception:  # pylint: disable=broad-except
                            pass
                    self.logind_idle_since_monotonic = time.monotonic()
                else:
                    self.logind_idle_since_monotonic = None

            bus.add_message_handler(handler)

            props = Message(
                destination="org.freedesktop.login1",
                path=session_path,
                interface="org.freedesktop.DBus.Properties",
                member="GetAll",
                signature="s",
                body=["org.freedesktop.login1.Session"],
            )
            try:
                props_reply: Any = await asyncio.wait_for(bus.call(props), timeout=2.0)
                props_dict = (
                    dict(props_reply.body[0]) if props_reply is not None else {}
                )
                idle_hint_var = props_dict.get("IdleHint")
                since_var = props_dict.get("IdleSinceHintMonotonic")
                idle = (
                    idle_hint_var.value
                    if idle_hint_var is not None and hasattr(idle_hint_var, "value")
                    else idle_hint_var
                )
                since_val = (
                    since_var.value
                    if since_var is not None and hasattr(since_var, "value")
                    else since_var
                )
                if idle and since_val:
                    try:
                        self.logind_idle_since_monotonic = max(
                            0.0, float(since_val) / 1_000_000.0
                        )
                    except Exception:  # pylint: disable=broad-except
                        self.logind_idle_since_monotonic = time.monotonic()
                else:
                    self.logind_idle_since_monotonic = None
            except Exception:  # pylint: disable=broad-except
                self.logind_idle_since_monotonic = None

            self.logind_bus = bus
            self.logind_handler = handler
            self.logind_session_path = session_path
            return True
        except Exception:  # pylint: disable=broad-except
            self.logind_failed = True
            if bus:
                try:
                    bus.disconnect()
                except Exception:  # pylint: disable=broad-except
                    pass
            self.logind_bus = None
            self.logind_handler = None
            self.logind_session_path = None
            self.logind_idle_since_monotonic = None
            return False

    @staticmethod
    def loginctl_idle_seconds_blocking() -> Optional[float]:
        session_id = os.environ.get("XDG_SESSION_ID")
        if not session_id:
            return None

        try:
            output = subprocess.check_output(
                [
                    "loginctl",
                    "show-session",
                    session_id,
                    "-p",
                    "IdleHint",
                    "-p",
                    "IdleSinceHintMonotonic",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except Exception:  # pylint: disable=broad-except
            return None

        kv = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        idle_hint = kv.get("IdleHint")
        if idle_hint != "yes":
            return 0.0

        try:
            since_us = int(kv.get("IdleSinceHintMonotonic", "0"))
        except (TypeError, ValueError):
            return 0.0

        if since_us <= 0:
            return 0.0

        try:
            with open("/proc/uptime", "r", encoding="utf-8") as uptime_file:
                up_s = float(uptime_file.read().split()[0])
        except (OSError, ValueError):
            return None

        idle_us = max(0, int(up_s * 1_000_000) - since_us)
        return idle_us / 1_000_000.0

    async def loginctl_idle_seconds(self) -> Optional[float]:
        return await asyncio.to_thread(self.loginctl_idle_seconds_blocking)

    async def get_idle_seconds(self) -> float:
        # Try GNOME Mutter IdleMonitor
        try:
            from dbus_next.aio import MessageBus  # type: ignore
            from dbus_next import Message, MessageType  # type: ignore

            bus = await MessageBus().connect()
            try:
                msg = Message(
                    destination="org.gnome.Mutter.IdleMonitor",
                    path="/org/gnome/Mutter/IdleMonitor/Core",
                    interface="org.gnome.Mutter.IdleMonitor",
                    member="GetIdletime",
                )
                reply: Any = await asyncio.wait_for(bus.call(msg), timeout=2.0)
                if getattr(reply, "message_type", None) == MessageType.METHOD_RETURN:
                    try:
                        return float(reply.body[0]) / 1000.0
                    except (IndexError, TypeError, ValueError):
                        return 0.0
            finally:
                # Always close the bus to terminate its reader task cleanly
                try:
                    bus.disconnect()
                except Exception:
                    pass
        except Exception:  # pylint: disable=broad-except
            pass

        # Try freedesktop ScreenSaver (KDE & some DEs)
        try:
            from dbus_next.aio import MessageBus  # type: ignore
            from dbus_next import Message, MessageType  # type: ignore

            bus = await MessageBus().connect()
            try:
                msg = Message(
                    destination="org.freedesktop.ScreenSaver",
                    path="/ScreenSaver",
                    interface="org.freedesktop.ScreenSaver",
                    member="GetSessionIdleTime",
                )
                reply: Any = await asyncio.wait_for(bus.call(msg), timeout=2.0)
                if getattr(reply, "message_type", None) == MessageType.METHOD_RETURN:
                    try:
                        return float(reply.body[0])
                    except (IndexError, TypeError, ValueError):
                        return 0.0
            finally:
                try:
                    bus.disconnect()
                except Exception:
                    pass
        except Exception:  # pylint: disable=broad-except
            pass

        # Try XDG-Desktop-Portal idle monitor (Hyprland, sway, etc.)
        try:
            if await self.ensure_portal_monitor():
                if self.portal_idle_since_monotonic is not None:
                    return max(0.0, time.monotonic() - self.portal_idle_since_monotonic)
                # If the portal is connected but has not produced an idle timestamp,
                # continue to other fallbacks (e.g. logind) rather than forcing 0.0.
        except Exception:  # pylint: disable=broad-except
            pass

        # Try listening to systemd-logind idle signals (Hyprland, sway, etc.)
        try:
            if await self.ensure_logind_monitor():
                if self.logind_idle_since_monotonic is not None:
                    return max(0.0, time.monotonic() - self.logind_idle_since_monotonic)
                # If no timestamp yet, continue to the legacy polling fallback below.
        except Exception:  # pylint: disable=broad-except
            pass

        # Try loginctl CLI fallback (works with swayidle/hypridle idlehint updates)
        try:
            loginctl_idle = await self.loginctl_idle_seconds()
            if loginctl_idle is not None:
                return loginctl_idle
        except Exception:  # pylint: disable=broad-except
            pass

        # Fallback: systemd-logind IdleHint / IdleSinceHintMonotonic
        try:
            from dbus_next.aio import MessageBus  # type: ignore
            from dbus_next import Message  # type: ignore
            from dbus_next.constants import BusType  # type: ignore

            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            try:
                # ListSessions to find this user's session
                list_msg = Message(
                    destination="org.freedesktop.login1",
                    path="/org/freedesktop/login1",
                    interface="org.freedesktop.login1.Manager",
                    member="ListSessions",
                )
                rep: Any = await asyncio.wait_for(bus.call(list_msg), timeout=2.0)
                sessions = rep.body[0] if rep is not None else []
                uid = os.getuid()
                username = os.getenv("USER", "")
                session_path: Optional[str] = None
                # ListSessions returns: (session_id, uid, username, seat, object_path)
                for _sid, _uid_val, _user, _seat, objpath in sessions:
                    # _uid_val can be either numeric or string representation of UID
                    if str(_uid_val) == str(uid) or _user == username:
                        session_path = objpath
                        break
                if not session_path:
                    return 0.0
                props = Message(
                    destination="org.freedesktop.login1",
                    path=session_path,
                    interface="org.freedesktop.DBus.Properties",
                    member="GetAll",
                    signature="s",
                    body=["org.freedesktop.login1.Session"],
                )
                props_reply: Any = await asyncio.wait_for(bus.call(props), timeout=2.0)
                d = dict(props_reply.body[0]) if props_reply is not None else {}
                # D-Bus returns Variant objects, extract the value
                idle_hint_var = d.get("IdleHint", False)
                idle_hint = (
                    idle_hint_var.value
                    if hasattr(idle_hint_var, "value")
                    else idle_hint_var
                )
                since_var = d.get("IdleSinceHintMonotonic", 0)
                since_us = int(
                    since_var.value if hasattr(since_var, "value") else since_var
                )  # µs
                if not idle_hint:
                    return 0.0
                with open("/proc/uptime", "r", encoding="utf-8") as f:
                    uptime_s = float(f.read().split()[0])
                return max(0.0, uptime_s - (since_us / 1_000_000.0))
            finally:
                try:
                    bus.disconnect()
                except Exception:
                    pass
        except Exception:  # pylint: disable=broad-except
            pass

        # Nothing worked
        return 0.0


def create_idle_detector(
    platform_type: PlatformType, display_server: DisplayServerType
) -> IdleDetectorBase:
    match platform_type:
        case PlatformType.WINDOWS | PlatformType.MACOS:
            return PynputIdleDetector()
        case PlatformType.LINUX:
            match display_server:
                case DisplayServerType.X11:
                    return X11IdleDetector()
                case DisplayServerType.WAYLAND:
                    return WaylandIdleDetector()
                case _:
                    # default to Wayland pathway (works for many DEs via DBus; safe fallback)
                    return WaylandIdleDetector()
        case _:
            # Unknown: fallback to Wayland-style (returns 0.0 if nothing available)
            return WaylandIdleDetector()
