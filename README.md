# Eyedim

Lightweight Windows utility to control display **brightness**, **night light** and **contrast** directly from the system tray.

![Screenshot](screenshot.png)

## Features

- Brightness control for all monitors (DDC/CI + WMI)
- Night light (warm color temperature)
- Contrast adjustment via gamma ramp
- System tray icon showing current brightness
- "User" and "Default" preset modes
- Autostart with Windows
- English / Russian interface

## Requirements

- Windows 10 / 11
- Python 3.13+ (tested). Earlier versions may work but are not officially supported.

## Download

[![Download Eyedim.exe](https://img.shields.io/badge/Download-Eyedim.exe-brightgreen?style=for-the-badge&logo=windows)](https://github.com/proyarik/eyedim/releases/latest)

Latest version: **v2.0.1**

## Installation

Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

```bash
python Eyedim.pyw
```

(or `pythonw Eyedim.pyw` to run without a console window)

The app starts minimized in the system tray. Left-click the tray icon to open the settings popup.

## Building a standalone .exe

```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --name Eyedim Eyedim.pyw
```

## ⚠️ Note on antivirus

The pre-built `.exe` was compressed with UPX. Some antivirus software may falsely flag it as suspicious. If this happens, either:

- add `Eyedim.exe` to your antivirus exclusions, or
- build from source using the instructions above.

The source code is fully open and available in this repository.

## License

Distributed under the terms of the **GNU General Public License v3.0**.
See the [LICENSE](LICENSE) file for details.