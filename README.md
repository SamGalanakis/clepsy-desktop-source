# Clepsy Desktop

<p align="center">
	<img src="docs/media/logo.svg" alt="Clepsy logo" width="128" />
</p>

> ⚠️ **Alpha Software** - Clepsy is currently in alpha. Expect breaking changes and rough edges.
## Overview

Clepsy Desktop is native and cross-platform. It runs in the background, keeps track of the currently focused window, detects when the user is idle, and periodically captures screenshots when enabled. Collected events are queued and sent securely to the Clepsy backend.

## Supported Platforms

- **Windows** 10 and later (64-bit)
- **macOS** 11 (Big Sur) and later (Intel and Apple Silicon)
- **Linux** (64-bit)
  - X11 desktop environments (GNOME, KDE, XFCE, etc.)
  - Wayland compositors (GNOME Shell, KDE Plasma, Hyprland, Sway, etc.)
    - **Additional requirement:** For accurate idle detection on Wayland compositors like Hyprland and Sway, you need to configure an idle management daemon:
      - **Recommended:** `swayidle` or `hypridle` configured to report idle state to systemd-logind
      - See the [Linux requirements and notes](#linux-requirements-and-notes) section for setup instructions

## Features

- System tray application with quick access to settings and status
- Automatic tracking of active window and application usage
- Intelligent idle time detection across all supported platforms
- Optional screenshot capture
- Secure, encrypted data transmission to the Clepsy backend
- Lightweight background operation with minimal system impact

## Installation

Prebuilt installers and packages are automatically generated for each release via GitHub Actions. Download the appropriate installer for your platform from the [Releases page](https://github.com/SamGalanakis/clepsy-desktop-source/releases).

### Windows

1. Download `clepsy-desktop-<version>-windows-setup.zip` from the latest release.
2. Extract the zip file to get `clepsy-desktop-<version>-setup.exe`.
3. Run the installer and follow the prompts. The application will be installed to `Program Files` and a desktop shortcut will be created.
4. Launch **Clepsy** from the Start Menu or the desktop shortcut.
5. The tray icon appears in the system tray (bottom-right). Right-click the icon for quick actions and settings.


### macOS

1. Download `clepsy-desktop-<version>-macos-dmg.zip` from the latest release.
2. Extract the zip file to get `Clepsy-<version>.dmg`.
3. Open the DMG and drag **Clepsy** to your Applications folder.
4. Launch **Clepsy** from Applications or Spotlight.
5. The first launch may ask for permission to run; go to **System Settings ▸ Privacy & Security** and click "Open Anyway" if prompted.
6. Grant **Accessibility** and **Screen Recording** permissions when requested (required for window tracking and screenshots).



### Linux

Multiple Linux package formats are provided to cover most distributions. Choose the appropriate package for your system:

#### Debian / Ubuntu (`.deb`)

```bash
# Download clepsy-desktop-<version>-linux-deb.zip from the Releases page, then:
unzip clepsy-desktop-<version>-linux-deb.zip
sudo dpkg -i clepsy-desktop_<version>_amd64.deb
sudo apt-get install -f  # resolve any missing dependencies
```

Launch from your application menu or run `clepsy_desktop_source` from the terminal.

#### Fedora / Red Hat / CentOS (`.rpm`)

```bash
# Download clepsy-desktop-<version>-linux-rpm.zip from the Releases page, then:
unzip clepsy-desktop-<version>-linux-rpm.zip
sudo dnf install clepsy-desktop-<version>-1.x86_64.rpm
# or on older systems:
sudo yum install clepsy-desktop-<version>-1.x86_64.rpm
```

Launch from your application menu or run `clepsy_desktop_source` from the terminal.

#### Arch Linux (PKGBUILD)

```bash
# Download clepsy-desktop-<version>-linux-pkgbuild.zip from the Releases page, then:
unzip clepsy-desktop-<version>-linux-pkgbuild.zip
cd clepsy-desktop-<version>-linux-pkgbuild
tar -xzf clepsy-desktop-<version>-pkgbuild.tar.gz
makepkg -si
```

This extracts the archive, unpacks the `PKGBUILD` and source tarball, then builds and installs the package.

#### Generic Linux (tarball)

If your distribution isn't covered above or you prefer manual installation:

```bash
# Download clepsy-desktop-<version>-linux-tarball.zip from the Releases page, then:
unzip clepsy-desktop-<version>-linux-tarball.zip
sudo tar -xzf clepsy-desktop-<version>-linux-x86_64.tar.gz -C /
sudo gtk-update-icon-cache /usr/share/icons/hicolor  # refresh icon cache
```

Run with `clepsy_desktop_source` from the terminal or add it to your startup applications.

#### Linux requirements and notes

- **X11:** Idle detection works automatically via the `python-xlib` backend.
- **Wayland (Hyprland, sway, GNOME, KDE, etc.):** Idle detection uses portal/DBus APIs and falls back to `loginctl`. For compositors like Hyprland or sway, configure an idle helper to keep systemd-logind synchronized:

  - **sway/Hyprland with swayidle:**

    ```bash
    swayidle idlehint 300 &  # sets logind IdleHint after 5 minutes of inactivity
    ```

  - **Hyprland with hypridle:**
    Add `idlehint` configuration to your Hypridle config.

- Desktop file and icon are installed to standard XDG locations. Run `gtk-update-icon-cache` or equivalent if the icon doesn't appear in your launcher.



### Development

- **Run the desktop app:**

  ```bash
  uv run python src/clepsy_desktop_source/main.py
  ```

- **Idle detector smoke test:**

  ```bash
  uv run python scripts/test_afk_detection.py
  ```


## License

This project is dual-licensed:

- **Open Source License:** GNU Affero General Public License v3.0 (AGPL-3.0) for open source use. See the [LICENSE](LICENSE) file for details.
- **Commercial License:** For commercial licensing options please contact [sam@clepsy.ai](mailto:sam@clepsy.ai).

**Copyright © 2025 Samouil Galanakis. All rights reserved.**
```