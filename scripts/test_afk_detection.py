import asyncio

from clepsy_desktop_source.config import detect_display_server, detect_platform
from clepsy_desktop_source.idle_detector import create_idle_detector


def format_status(idle_seconds: float) -> str:
    minutes = int(idle_seconds // 60)
    seconds = int(idle_seconds % 60)
    if minutes > 0:
        time_str = f"{minutes}m {seconds}s"
    else:
        time_str = f"{seconds}s"

    if idle_seconds < 5:
        status = "🟢 ACTIVE"
    elif idle_seconds < 30:
        status = "🟡 IDLE"
    else:
        status = "🔴 AFK"

    return f"{status} | Idle time: {time_str} ({idle_seconds:.1f}s)"


async def main() -> None:
    platform = detect_platform()
    display_server = detect_display_server(platform)

    print("🔍 Testing AFK Detection")
    print("=" * 50)
    print(f"Platform: {platform}")
    print(f"Display Server: {display_server}")
    print("=" * 50)

    detector = create_idle_detector(platform, display_server)

    print(f"✓ Created detector: {type(detector).__name__}")
    print(f"✓ Is async: {detector.is_async}")
    print()
    print("Monitoring idle time... (Press Ctrl+C to stop)")
    print("Move your mouse or press a key to reset the idle timer")
    print("-" * 50)

    try:
        async with detector:
            for _ in range(60):
                idle_seconds = await detector.get_idle_seconds()
                print(f"\r{format_status(idle_seconds)}  ", end="", flush=True)
                await asyncio.sleep(1)
        print("\n\n✓ 60-second test finished")
    except KeyboardInterrupt:
        print("\n\n✓ Test stopped early")


if __name__ == "__main__":
    asyncio.run(main())
