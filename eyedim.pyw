import pystray
from PIL import Image, ImageDraw, ImageFont
import tkinter as tk
import screen_brightness_control as sbc
import win32gui
import win32api
import ctypes
from ctypes import wintypes
import math
import sys
import os
import winreg
import threading
import queue
import urllib.request
import urllib.parse
import io
import json
import time
import functools
from array import array

# --- SYSTEM CONSTANTS ---
DWMWA_WINDOW_CORNER_PREFERENCE = 33
APP_VERSION = "v2.1.0"
DONATE_URL = "https://your-donation-link.com"

# --- DEFAULTS / LOOK (kept in sync with the web page demo) ---
DEFAULT_PRESET = (80, 10, 50)      # brightness, night light, contrast
PANEL_BG = "#f4f4f6"
ACCENT = "#d98a12"                 # slider colour
SUN_COLOR = "#f2a93b"
MOON_COLOR = "#2f5fd0"
BTN_BG = "#dcdce0"
BTN_BG_HOVER = "#d0d0d6"
TEXT_COLOR = "#222222"
POPUP_W, POPUP_H = 280, 138        # settings window size

# --- GAMMA SAFETY LIMITS ---
GAMMA_GREEN_MIN = 0.70
GAMMA_BLUE_MIN = 0.55

# --- LOCALIZATION ---
LANG = "en"

TRANSLATIONS = {
    "en": {
        "menu_settings": "Open Settings",
        "menu_about": "About Eyedim",
        "menu_panel_test": "Test Display Panel",
        "menu_autostart": "Run at Windows Startup",
        "menu_lang": "Language: English",
        "menu_exit": "Exit",
        "mode_user": "Mode: User",
        "mode_default": "Mode: Default",
        "window_title": "Eyedim - Display Settings",
        "about_title": "About Eyedim",
        "about_desc": "Lightweight utility to control display brightness, night light, and contrast.",
        "about_author": "Developer: proyarik\nCreated with AI assistance.",
        "about_license": "Distributed under GNU GPLv3 terms.\nProvided AS IS, without any warranties.",
        "about_support": "Support the project:",
        "tooltip_format": "Eyedim — Brightness: {}% | Night Light: {}% | Contrast: {}%",
        "tooltip_format_short": "Eyedim — Brightness: {}% | Night Light: {}%",
        "test_hint": "{}/{} · {} · Click or Space: next · Right-click or ←: back · Esc: exit",
        "test_screens": ["Black", "White", "Red", "Green", "Blue", "Gray",
                         "Grid", "Fine grid", "Very fine grid", "Test card"]
    },
    "ru": {
        "menu_settings": "Открыть настройки",
        "menu_about": "О программе Eyedim",
        "menu_panel_test": "Проверка матрицы",
        "menu_autostart": "Запускать при старте Windows",
        "menu_lang": "Язык: Русский",
        "menu_exit": "Выход",
        "mode_user": "Режим: Пользователь",
        "mode_default": "Режим: По умолчанию",
        "window_title": "Eyedim - Настройки дисплея",
        "about_title": "О программе Eyedim",
        "about_desc": "Легковесная утилита для управления яркостью, ночным светом и контрастом.",
        "about_author": "Разработчик: proyarik\nСоздано при содействии ИИ.",
        "about_license": "Распространяется на условиях GNU GPLv3.\nПоставляется БЕЗ КАКИХ-ЛИБО ГАРАНТИЙ.",
        "about_support": "Поддержать проект:",
        "tooltip_format": "Eyedim — Яркость: {}% | Ночной свет: {}% | Контраст: {}%",
        "tooltip_format_short": "Eyedim — Яркость: {}% | Ночной свет: {}%",
        "test_hint": "{}/{} · {} · Щелчок или пробел — дальше · Правая кнопка или ← — назад · Esc — выход",
        "test_screens": ["Чёрный", "Белый", "Красный", "Зелёный", "Синий", "Серый",
                         "Сетка", "Мелкая сетка", "Очень мелкая сетка", "Настроечная таблица"]
    }
}

def t(key):
    return TRANSLATIONS[LANG].get(key, key)

# --- SETTINGS FILE ---
CONFIG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Eyedim")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
_config_lock = threading.Lock()
_exiting = False

def _clamp(v, default):
    try:
        return max(0, min(100, int(v)))
    except Exception:
        return default

def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None

def detect_system_language():
    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "ru" if (lang_id & 0x3FF) == 0x19 else "en"
    except Exception:
        return "en"

def read_all_brightness():
    try:
        return [int(v) if v is not None else None for v in sbc.get_brightness()]
    except Exception:
        return []

# --- INITIAL STATE (from settings file; first run = Default mode 80/10/50) ---
_cfg = load_config()
_first_run = _cfg is None
if _first_run:
    _cfg = {}

LANG = _cfg.get("lang") if _cfg.get("lang") in TRANSLATIONS else detect_system_language()

_user = _cfg.get("user")
if not (isinstance(_user, (list, tuple)) and len(_user) == 3):
    _user = DEFAULT_PRESET
saved_brightness, saved_night_light, saved_contrast = (
    _clamp(v, d) for v, d in zip(_user, DEFAULT_PRESET))

preset_active = True if _first_run else (_cfg.get("mode", "default") != "user")
# Contrast slider is hidden by default; the user can switch it on in the window.
show_contrast = bool(_cfg.get("show_contrast", False))

def effective_contrast():
    """Hidden contrast slider = neutral effect; the value itself is remembered."""
    return current_contrast if show_contrast else 50

def popup_height():
    return POPUP_H if show_contrast else POPUP_H - 24
if preset_active:
    current_brightness, current_night_light, current_contrast = DEFAULT_PRESET
else:
    current_brightness = saved_brightness
    current_night_light = saved_night_light
    current_contrast = saved_contrast

# Brightness the monitors had BEFORE Eyedim touched them. If the previous
# session did not exit cleanly, the stored value is still the true original.
_orig = _cfg.get("original_brightness")
if _first_run or _cfg.get("clean_exit", True) or not isinstance(_orig, list) or not _orig:
    original_brightness = read_all_brightness()
else:
    original_brightness = _orig

_reapply_timer = None
mode_btn_redraw_ref = None

def save_config(clean_exit=False):
    if _exiting and not clean_exit:
        return
    data = {
        "mode": "default" if preset_active else "user",
        "user": [saved_brightness, saved_night_light, saved_contrast],
        "lang": LANG,
        "show_contrast": show_contrast,
        "original_brightness": original_brightness,
        "clean_exit": clean_exit,
    }
    try:
        with _config_lock:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, CONFIG_PATH)
    except Exception:
        pass

SAVE_DELAY = 0.5
_save_requested = threading.Event()

def _save_worker():
    # Debounced writer: saves once the settings have been quiet for SAVE_DELAY.
    while True:
        _save_requested.wait()
        while True:
            _save_requested.clear()
            time.sleep(SAVE_DELAY)
            if not _save_requested.is_set():
                break
        save_config()

threading.Thread(target=_save_worker, daemon=True).start()

def schedule_save():
    _save_requested.set()

popup_window = None
about_window = None
panel_test_windows = []                # one full-screen window per monitor
tray_icon_ref = None
brightness_slider_ref = None
night_slider_ref = None
contrast_slider_ref = None
brightness_label_ref = None
night_label_ref = None
contrast_label_ref = None

startup_registry_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
app_name = "Eyedim"

# --- TK ROOT IN ITS OWN THREAD ---
root_window = None
_root_ready = threading.Event()

def tk_thread_main():
    global root_window
    root_window = tk.Tk()
    root_window.withdraw()
    _root_ready.set()
    root_window.mainloop()

threading.Thread(target=tk_thread_main, daemon=True).start()
_root_ready.wait()

def ui_call(fn, *args, **kwargs):
    if root_window is None:
        return
    root_window.after(0, lambda: fn(*args, **kwargs))

# --- AUTOSTART ---
def is_autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup_registry_key, 0,
                            winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, app_name)
        return True
    except Exception:
        return False

def toggle_autostart(icon, item):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup_registry_key, 0,
                            winreg.KEY_SET_VALUE) as key:
            if is_autostart_enabled():
                winreg.DeleteValue(key, app_name)
            else:
                if getattr(sys, 'frozen', False):
                    app_path = f'"{sys.executable}"'
                else:
                    app_path = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, app_path)
    except Exception as e:
        print(f"Autostart error: {e}")
    icon.update_menu()

def toggle_language(icon, item):
    global LANG
    LANG = "ru" if LANG == "en" else "en"
    schedule_save()
    update_tray_icon()
    icon.update_menu()
    ui_call(rebuild_open_windows)

def rebuild_open_windows():
    popup_was_open = popup_window is not None
    about_was_open = about_window is not None

    close_popup()
    close_about()

    if popup_was_open:
        _show_popup_ui()
    if about_was_open:
        _show_about_ui()

_tray_last_icon_value = None
_tray_last_title = None

def update_tray_icon():
    global _tray_last_icon_value, _tray_last_title
    if not tray_icon_ref:
        return
    # Rendering the PIL image and swapping the Windows icon is the expensive
    # part, so skip it (and the tooltip) when nothing visible has changed.
    if current_brightness != _tray_last_icon_value:
        tray_icon_ref.icon = create_text_icon(current_brightness)
        _tray_last_icon_value = current_brightness
    if show_contrast:
        title = TRANSLATIONS[LANG]["tooltip_format"].format(
            current_brightness, current_night_light, current_contrast)
    else:
        title = TRANSLATIONS[LANG]["tooltip_format_short"].format(
            current_brightness, current_night_light)
    if title != _tray_last_title:
        tray_icon_ref.title = title
        _tray_last_title = title

def update_popup_labels_and_sliders():
    global brightness_slider_ref, night_slider_ref, contrast_slider_ref
    global brightness_label_ref, night_label_ref, contrast_label_ref
    if brightness_slider_ref:
        brightness_slider_ref.set(current_brightness, update_command=False)
    if night_slider_ref:
        night_slider_ref.set(current_night_light, update_command=False)
    if contrast_slider_ref:
        contrast_slider_ref.set(current_contrast, update_command=False)

    if brightness_label_ref:
        brightness_label_ref.config(text=f"{current_brightness}")
    if night_label_ref:
        night_label_ref.config(text=f"{current_night_light}")
    if contrast_label_ref:
        contrast_label_ref.config(text=f"{current_contrast}")
    if mode_btn_redraw_ref:
        mode_btn_redraw_ref()

# --- GAMMA RAMP ---
@functools.lru_cache(maxsize=128)   # fades revisit the same values
def build_gamma_ramp(nl, c):
    """256 R + 256 G + 256 B 16-bit little-endian values (1536 bytes)."""
    nl_factor = nl / 100.0
    c_factor = (c - 50) / 50.0

    gf = max(GAMMA_GREEN_MIN, 1.0 - 0.40 * nl_factor)
    bf = max(GAMMA_BLUE_MIN, 1.0 - 0.75 * nl_factor)
    scale = 1.0 + c_factor * (0.5 if c_factor > 0 else 0.4)

    base = []
    for i in range(256):
        v = i * 257
        if c_factor != 0:
            v = max(0, min(65535, int(32768 + (v - 32768) / 32768.0 * scale * 32768)))
        base.append(v)

    ramp = array('H', base)                               # red
    ramp.extend(min(65535, int(v * gf)) for v in base)    # green
    ramp.extend(min(65535, int(v * bf)) for v in base)    # blue
    if sys.byteorder != 'little':
        ramp.byteswap()
    return ramp.tobytes()

def apply_gamma_direct(nl, c):
    try:
        ramp = build_gamma_ramp(nl, c)

        def monitor_enum_proc(hMonitor, hdcMonitor, lprcMonitor, dwData):
            try:
                info = win32api.GetMonitorInfo(hMonitor)
                device_name = info['Device']
                hdc = win32gui.CreateDC(device_name, device_name, None)
                if hdc:
                    try:
                        ctypes.windll.gdi32.SetDeviceGammaRamp(hdc, ramp)
                    finally:
                        win32gui.DeleteDC(hdc)
            except Exception:
                pass
            return True

        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
        )
        ctypes.windll.user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(monitor_enum_proc), 0)
    except Exception:
        pass

# --- GAMMA WORKER (smooth transitions) ---
# Night light / contrast changes are faded in a single worker thread: every
# new request retargets the running fade from the value currently on screen,
# so fast slider moves never queue up or "jump back". Values are rounded to
# whole units and identical frames are skipped, so a full 0 -> 100 fade costs
# at most ~25 ramp writes, and an idle app costs nothing (the thread sleeps).
GAMMA_FADE_FULL = 0.6               # seconds for a full 0 -> 100 change
GAMMA_FRAME = 1.0 / 40              # ~40 fps while a fade is running
GAMMA_INSTANT_DELTA = 2             # tiny steps (slider drag) apply at once
_shutting_down = threading.Event()

gamma_lock = threading.Lock()
gamma_dirty = threading.Event()
_gamma = {
    "from": (float(current_night_light), float(effective_contrast())),
    "to": (float(current_night_light), float(effective_contrast())),
    "t0": 0.0,
    "dur": 0.0,
    "force": True,                  # first frame is always written
    "restore_due": None,            # re-send ramp after a brightness write
    "restore_last": None,
}

def _gamma_value_at(now):
    """Value on screen at moment `now` (call with gamma_lock held)."""
    g = _gamma
    if g["dur"] <= 0 or now >= g["t0"] + g["dur"]:
        return g["to"]
    p = (now - g["t0"]) / g["dur"]
    p = p * p * (3 - 2 * p)         # smoothstep: soft start and soft finish
    (a0, b0), (a1, b1) = g["from"], g["to"]
    return a0 + (a1 - a0) * p, b0 + (b1 - b0) * p

def request_gamma_update(nl=None, c=None, instant=False):
    now = time.monotonic()
    with gamma_lock:
        cur = _gamma_value_at(now)
        old = _gamma["to"]
        new = (float(old[0] if nl is None else nl),
               float(old[1] if c is None else c))
        dist = max(abs(new[0] - cur[0]), abs(new[1] - cur[1]))
        _gamma["from"] = cur
        _gamma["to"] = new
        _gamma["t0"] = now
        if instant or dist <= GAMMA_INSTANT_DELTA:
            _gamma["dur"] = 0.0
        else:
            _gamma["dur"] = GAMMA_FADE_FULL * min(1.0, dist / 100.0)
        if instant:
            _gamma["force"] = True
    gamma_dirty.set()

def gamma_target():
    """Night light / contrast that should be on screen right now. The display
    panel test needs true colours, so it keeps the ramp neutral while open."""
    if panel_test_windows:
        return 0, 50
    return current_night_light, effective_contrast()

def read_pending_gamma():
    with gamma_lock:
        return _gamma["to"]

def schedule_gamma_restore(delay):
    """Re-send the current ramp `delay` s from now (DDC writes may reset it).
    Repeated calls keep the earliest pending restore and also guarantee one
    after the latest call, so a long brightness fade is covered too."""
    when = time.monotonic() + delay
    with gamma_lock:
        if _gamma["restore_due"] is None:
            _gamma["restore_due"] = when
        _gamma["restore_last"] = when
    gamma_dirty.set()

def gamma_worker():
    last_sent = None
    while True:
        now = time.monotonic()
        with gamma_lock:
            nl, c = _gamma_value_at(now)
            fading = _gamma["dur"] > 0 and now < _gamma["t0"] + _gamma["dur"]
            force = _gamma["force"]
            _gamma["force"] = False
            due = _gamma["restore_due"]
            if due is not None and now >= due:
                force = True
                last = _gamma["restore_last"]
                _gamma["restore_due"] = last if last > due else None
                due = _gamma["restore_due"]

        frame = (int(round(nl)), int(round(c)))
        if not _shutting_down.is_set() and (force or frame != last_sent):
            apply_gamma_direct(*frame)
            last_sent = frame

        # sleep until the next fade frame / pending restore, or forever if idle
        timeout = GAMMA_FRAME if fading else None
        if due is not None:
            left = max(0.0, due - time.monotonic())
            timeout = left if timeout is None else min(timeout, left)
        gamma_dirty.wait(timeout)
        gamma_dirty.clear()

threading.Thread(target=gamma_worker, daemon=True).start()

# --- FAST DDC/CI WRITERS ---
# sbc.set_brightness() re-enumerates monitors (WMI + dxva2) on every call and
# writes the displays one after another, which is ~130 ms per step on two
# external monitors: far too slow for a smooth fade. While fading we keep the
# physical monitor handles open and give every monitor its own writer thread
# that sends only the latest value (SetVCPFeature, VCP code 0x10), so the
# monitors update in parallel at their own DDC/CI speed. Used only when every
# display is an external DDC/CI monitor; otherwise sbc is used as before.
# Forced writes (startup, wake, display change, retries) always go through
# sbc and re-open the handles afterwards, since old handles may be stale.
VCP_BRIGHTNESS = 0x10

class _PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [("handle", wintypes.HANDLE), ("description", wintypes.WCHAR * 128)]

_vcp_failures = 0
VCP_MAX_FAILURES = 2                # then stay on sbc for the rest of the session

class _VcpWriter:
    def __init__(self, handle, on_fail):
        self.handle = handle
        self.on_fail = on_fail
        self.value = None
        self.written = None
        self.alive = True
        self.event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def post(self, value):
        self.value = value
        self.event.set()

    def close(self):
        self.alive = False
        self.event.set()

    def _run(self):
        dxva2 = ctypes.windll.dxva2
        try:
            while True:
                self.event.wait()
                self.event.clear()
                if not self.alive:
                    return
                v = self.value
                if v is None or v == self.written:
                    continue
                for _ in range(3):
                    if dxva2.SetVCPFeature(self.handle, VCP_BRIGHTNESS, v):
                        self.written = v
                        schedule_gamma_restore(GAMMA_RESTORE_DELAY)
                        break
                    time.sleep(0.02)
                else:
                    self.on_fail()
                    return
        finally:
            try:
                dxva2.DestroyPhysicalMonitor(self.handle)
            except Exception:
                pass

def _open_vcp_writers(on_fail):
    """Writers for all monitors, or None if the fast path can't be used."""
    if _vcp_failures >= VCP_MAX_FAILURES:
        return None
    handles = []
    try:
        infos = sbc.list_monitors_info()
        if not infos or not all(
                "VCP" in getattr(i.get("method"), "__name__", str(i.get("method")))
                for i in infos):
            return None
        dxva2 = ctypes.windll.dxva2
        dxva2.SetVCPFeature.argtypes = (wintypes.HANDLE, wintypes.BYTE, wintypes.DWORD)
        dxva2.DestroyPhysicalMonitor.argtypes = (wintypes.HANDLE,)
        for m in win32api.EnumDisplayMonitors():
            hmon = wintypes.HMONITOR(m[0].handle)
            count = wintypes.DWORD()
            if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(count)):
                raise OSError("GetNumberOfPhysicalMonitorsFromHMONITOR failed")
            if count.value:
                arr = (_PHYSICAL_MONITOR * count.value)()
                if not dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, count.value, arr):
                    raise OSError("GetPhysicalMonitorsFromHMONITOR failed")
                handles.extend(p.handle for p in arr)
        # pseudo/duplicate monitors would make the mapping unclear: use sbc then
        if len(handles) != len(infos):
            raise OSError("monitor count mismatch")
        return [_VcpWriter(h, on_fail) for h in handles]
    except Exception:
        for h in handles:
            try:
                ctypes.windll.dxva2.DestroyPhysicalMonitor(h)
            except Exception:
                pass
        return None

def _close_vcp_writers(writers):
    for w in writers or ():
        w.close()
    for w in writers or ():
        w.thread.join(0.3)          # let an in-flight write finish first

# --- BRIGHTNESS WORKER (smooth transitions) ---
# The brightness on screen follows the slider through a critically damped
# spring: it accelerates softly, never overshoots, and when the target keeps
# moving (slider drag) it simply keeps following without stopping or
# restarting. The spring runs on real elapsed time and the writers always send
# the newest value, so slow hardware gets fewer writes on the same smooth
# curve instead of falling behind. DDC/CI monitors get a longer fade so each
# step stays small. Forced writes (startup, wake, retries) jump directly.
brightness_queue = queue.Queue()
BRIGHTNESS_SPRING_DDC = 3.5         # 1/s; 0 -> 100 settles in ~1.6 s
BRIGHTNESS_SPRING_FAST = 7.0        # laptop panels (WMI): ~0.8 s
BRIGHTNESS_FRAME = 1.0 / 40         # spring update rate while fading
GAMMA_RESTORE_DELAY = 0.12
MAX_BRIGHTNESS_RETRIES = 3          # quick retries: 0.5 s, 1 s, 1.5 s
desired_brightness = current_brightness

def brightness_worker():
    global _vcp_failures
    last_applied = -1
    fail_streak = 0
    target = None
    force = False
    pos = vel = 0.0                 # spring state (value on screen, %/s)
    last_t = time.monotonic()
    writers = None
    vcp_broken = threading.Event()

    def on_vcp_fail():
        vcp_broken.set()
        brightness_queue.put((desired_brightness, True))   # wake + resync via sbc

    def schedule_retry():
        nonlocal fail_streak
        if fail_streak < MAX_BRIGHTNESS_RETRIES:
            fail_streak += 1
            tm = threading.Timer(0.5 * fail_streak,
                                 lambda: enqueue_brightness(desired_brightness, force=True))
            tm.daemon = True
            tm.start()

    try:
        while True:
            if target is None:
                item = brightness_queue.get()
                if item is None:
                    return
                target, force = item
                pos, vel = float(max(last_applied, 0)), 0.0
                last_t = time.monotonic()
            # coalesce everything queued so far: only the latest target matters
            while True:
                try:
                    nxt = brightness_queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is None:
                    return
                target = nxt[0]
                force = force or nxt[1]
            if _shutting_down.is_set():
                target = None
                continue
            if vcp_broken.is_set():
                vcp_broken.clear()
                _vcp_failures += 1
                _close_vcp_writers(writers)
                writers = None
                force = True

            if force or last_applied < 0:
                # direct, robust write through sbc
                _close_vcp_writers(writers)
                writers = None
                try:
                    sbc.set_brightness(target)
                except Exception:
                    target = None
                    force = False
                    schedule_retry()
                    continue
                last_applied = target
                pos, vel = float(target), 0.0
                fail_streak = 0
                force = False
                target = None
                schedule_gamma_restore(GAMMA_RESTORE_DELAY)
                writers = _open_vcp_writers(on_vcp_fail)
                continue

            now = time.monotonic()
            dt, last_t = now - last_t, now
            # exact critically damped step: stable for any dt
            w = BRIGHTNESS_SPRING_DDC if writers else BRIGHTNESS_SPRING_FAST
            x = pos - target
            k = vel + w * x
            e = math.exp(-w * dt)
            pos = target + (x + k * dt) * e
            vel = (vel - k * w * dt) * e
            if abs(pos - target) < 1.0 and abs(vel) < 3.0:
                pos, vel = float(target), 0.0
            val = max(0, min(100, int(round(pos))))
            settled = pos == target and vel == 0.0

            if val != last_applied:
                if writers:
                    for wr in writers:
                        wr.post(val)            # non-blocking, latest value wins
                    last_applied = val
                else:
                    try:
                        sbc.set_brightness(val)
                        last_applied = val
                        fail_streak = 0
                        schedule_gamma_restore(GAMMA_RESTORE_DELAY)
                    except Exception:
                        target = None
                        schedule_retry()
                        continue

            if settled:
                target = None
            else:
                spent = time.monotonic() - now
                if _shutting_down.wait(max(0.0, BRIGHTNESS_FRAME - spent)):
                    target = None
    finally:
        _close_vcp_writers(writers)

brightness_thread = threading.Thread(target=brightness_worker, daemon=True)
brightness_thread.start()

def enqueue_brightness(value, force=False):
    global desired_brightness
    if _shutting_down.is_set():
        return
    value = max(0, min(100, int(round(value))))
    desired_brightness = value
    brightness_queue.put((value, force))

def restore_original_brightness(timeout=3.0):
    vals = original_brightness
    if not vals:
        return

    def work():
        for i, v in enumerate(vals):
            if v is None:
                continue
            try:
                sbc.set_brightness(v, display=i)
            except Exception:
                pass

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout)

# --- RE-APPLY AFTER SLEEP / DISPLAY CHANGE ---
# Windows, the GPU driver and many monitors drop the gamma ramp (night light /
# contrast) and sometimes the DDC/CI brightness when the screen is powered off
# by the idle timeout, the PC wakes from sleep, or the session is unlocked.
# None of those reliably sends WM_DISPLAYCHANGE, so we also subscribe to the
# console display state and session lock notifications.
REAPPLY_DELAYS = (1.5, 5.0)   # second pass catches late resets by driver/monitor

def reapply_all():
    if _shutting_down.is_set():
        return
    request_gamma_update(*gamma_target(), instant=True)
    enqueue_brightness(current_brightness, force=True)

def schedule_reapply(delays=REAPPLY_DELAYS):
    global _reapply_timer
    if _shutting_down.is_set() or not delays:
        return
    if _reapply_timer:
        _reapply_timer.cancel()

    first, rest = delays[0], delays[1:]

    def fire():
        reapply_all()
        if rest:
            schedule_reapply(tuple(d - first for d in rest))

    _reapply_timer = threading.Timer(first, fire)
    _reapply_timer.daemon = True
    _reapply_timer.start()

WM_DISPLAYCHANGE = 0x007E
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_POWERSETTINGCHANGE = 0x8013
WTS_CONSOLE_CONNECT = 0x1
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0
DEVICE_NOTIFY_WINDOW_HANDLE = 0
DISPLAY_STATE_ON = 1          # 0 = off, 1 = on, 2 = dimmed

class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    def __eq__(self, other):
        return bytes(self) == bytes(other)

# GUID_CONSOLE_DISPLAY_STATE {6FE69556-704A-47A0-8F24-C28D936FDA47}
GUID_CONSOLE_DISPLAY_STATE = GUID(
    0x6FE69556, 0x704A, 0x47A0,
    (ctypes.c_ubyte * 8)(0x8F, 0x24, 0xC2, 0x8D, 0x93, 0x6F, 0xDA, 0x47))

class POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [("PowerSetting", GUID), ("DataLength", ctypes.c_uint32),
                ("Data", ctypes.c_uint32)]

def start_display_watcher():
    def run():
        try:
            display_state = {"last": None}

            def on_power_setting(lparam):
                if not lparam:
                    return
                pbs = ctypes.cast(lparam, ctypes.POINTER(POWERBROADCAST_SETTING)).contents
                if pbs.PowerSetting != GUID_CONSOLE_DISPLAY_STATE:
                    return
                state = pbs.Data
                prev = display_state["last"]
                display_state["last"] = state
                # Windows sends the current state right after registering;
                # only react to a real transition back to "on".
                if state == DISPLAY_STATE_ON and prev is not None and prev != DISPLAY_STATE_ON:
                    schedule_reapply()

            def wndproc(hwnd, msg, wparam, lparam):
                try:
                    if msg == WM_DISPLAYCHANGE:
                        schedule_reapply()
                    elif msg == WM_POWERBROADCAST:
                        if wparam in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC):
                            schedule_reapply()
                        elif wparam == PBT_POWERSETTINGCHANGE:
                            on_power_setting(lparam)
                    elif msg == WM_WTSSESSION_CHANGE:
                        if wparam in (WTS_SESSION_UNLOCK, WTS_CONSOLE_CONNECT):
                            schedule_reapply()
                except Exception:
                    pass
                return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

            wc = win32gui.WNDCLASS()
            wc.hInstance = win32api.GetModuleHandle(None)
            wc.lpszClassName = "EyedimWatcher"
            wc.lpfnWndProc = wndproc
            cls = win32gui.RegisterClass(wc)
            hwnd = win32gui.CreateWindow(cls, "EyedimWatcher", 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None)

            # screen turned off by idle timeout and back on
            try:
                reg = ctypes.windll.user32.RegisterPowerSettingNotification
                reg.restype = ctypes.c_void_p
                reg.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), wintypes.DWORD]
                reg(hwnd, ctypes.byref(GUID_CONSOLE_DISPLAY_STATE), DEVICE_NOTIFY_WINDOW_HANDLE)
            except Exception as e:
                print(f"Power setting notification error: {e}")

            # lock screen -> unlock (often follows the idle screen-off)
            try:
                ctypes.windll.wtsapi32.WTSRegisterSessionNotification(
                    wintypes.HWND(hwnd), NOTIFY_FOR_THIS_SESSION)
            except Exception as e:
                print(f"Session notification error: {e}")

            win32gui.PumpMessages()
        except Exception as e:
            print(f"Display watcher error: {e}")

    threading.Thread(target=run, daemon=True).start()

# --- SETTERS ---
def _commit_user_mode():
    """Any slider movement switches to User mode and stores the values."""
    global preset_active, saved_brightness, saved_night_light, saved_contrast
    preset_active = False
    saved_brightness = current_brightness
    saved_night_light = current_night_light
    saved_contrast = current_contrast
    schedule_save()

def set_brightness(val, send_system=True):
    global current_brightness
    current_brightness = max(0, min(100, int(val)))
    if send_system:
        enqueue_brightness(current_brightness)
    _commit_user_mode()
    update_tray_icon()
    update_popup_labels_and_sliders()

def set_night_light(val):
    global current_night_light
    current_night_light = max(0, min(100, int(val)))
    request_gamma_update(nl=current_night_light)
    _commit_user_mode()
    update_tray_icon()
    update_popup_labels_and_sliders()

def set_contrast(val):
    global current_contrast
    current_contrast = max(0, min(100, int(val)))
    request_gamma_update(c=effective_contrast())
    _commit_user_mode()
    update_tray_icon()
    update_popup_labels_and_sliders()

def toggle_preset():
    global preset_active, current_brightness, current_night_light, current_contrast

    old_brightness = current_brightness

    if preset_active:
        # Default -> User: saved_* always hold the latest User values
        current_brightness = saved_brightness
        current_night_light = saved_night_light
        current_contrast = saved_contrast
        preset_active = False
    else:
        # User -> Default
        current_brightness, current_night_light, current_contrast = DEFAULT_PRESET
        preset_active = True

    update_popup_labels_and_sliders()
    update_tray_icon()

    if current_brightness != old_brightness:
        enqueue_brightness(current_brightness)

    request_gamma_update(current_night_light, effective_contrast())
    schedule_save()

# --- TRAY ICON ---
_font_cache = {}

def _get_font(size):
    if size not in _font_cache:
        for name in ("arialbd.ttf", "segoeuib.ttf", "arial.ttf"):
            try:
                _font_cache[size] = ImageFont.truetype(name, size)
                break
            except Exception:
                continue
        else:
            _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]

def create_text_icon(brightness_val):
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    dc = ImageDraw.Draw(image)

    line_color = (255, 200, 50, 255)
    dc.line([(2, 3), (62, 3)], fill=line_color, width=3)
    dc.line([(2, 60), (62, 60)], fill=line_color, width=3)

    text = f"{int(brightness_val)}"
    size = 52 if len(text) == 1 else (46 if len(text) == 2 else 34)
    font = _get_font(size)

    bbox = dc.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    dc.text(((64 - tw) // 2 - bbox[0], (64 - th) // 2 - bbox[1]),
            text, fill=(255, 200, 50), font=font)
    return image

# --- VECTOR ICONS ---
def draw_vector_sun(canvas, x, y):
    r = 5
    canvas.create_oval(x - r, y - r, x + r, y + r, fill=SUN_COLOR, outline="")
    for i in range(8):
        a = math.radians(i * 45)
        x1 = x + 8 * math.cos(a); y1 = y + 8 * math.sin(a)
        x2 = x + 11 * math.cos(a); y2 = y + 11 * math.sin(a)
        canvas.create_line(x1, y1, x2, y2, fill=SUN_COLOR, width=2, capstyle="round")

def _moon_points():
    # Crescent = big disk minus an offset disk (same geometry as the web icon).
    R, rr, ox, oy = 9.0, 9.0, 6.4, -6.4
    d = math.hypot(ox, oy)
    a = (R * R - rr * rr + d * d) / (2 * d)
    h = math.sqrt(max(R * R - a * a, 0))
    th = math.atan2(oy, ox)
    phi = math.atan2(h, a)
    n1, n2 = 48, 32
    outer = [(R * math.cos(t), R * math.sin(t)) for t in
             (th + phi + i * (2 * math.pi - 2 * phi) / n1 for i in range(n1 + 1))]
    psi_c = th + math.pi
    psi = math.atan2(h, d - a)
    inner = [(ox + rr * math.cos(t), oy + rr * math.sin(t)) for t in
             (psi_c - psi + i * 2 * psi / n2 for i in range(n2 + 1))]
    last = outer[-1]
    if math.hypot(inner[0][0] - last[0], inner[0][1] - last[1]) > \
            math.hypot(inner[-1][0] - last[0], inner[-1][1] - last[1]):
        inner.reverse()
    return outer + inner

_MOON_PTS = _moon_points()

def draw_vector_moon(canvas, x, y, bg=PANEL_BG):
    k = 0.9
    cx, cy = x - 0.7 * k, y + 0.7 * k
    coords = []
    for px, py in _MOON_PTS:
        coords += [cx + px * k, cy + py * k]
    canvas.create_polygon(coords, fill=MOON_COLOR, outline="")

def draw_vector_contrast(canvas, x, y):
    r = 8
    canvas.create_arc(x - r, y - r, x + r, y + r, start=-90, extent=180,
                      fill="#444444", outline="", style="pieslice")
    canvas.create_oval(x - r, y - r, x + r, y + r, outline="#444444", width=2)

def draw_vector_gear(canvas, x, y, color=MOON_COLOR):
    k = 16 / 24.0
    canvas.create_oval(x - 3 * k, y - 3 * k, x + 3 * k, y + 3 * k, outline=color, width=1)
    for i in range(8):
        a = math.radians(i * 45)
        canvas.create_line(x + 7 * k * math.cos(a), y + 7 * k * math.sin(a),
                           x + 10 * k * math.cos(a), y + 10 * k * math.sin(a),
                           fill=color, width=1, capstyle="round")

def draw_round_rect(canvas, w, h, color, radius=6):
    d = radius * 2
    canvas.create_arc(0, 0, d, d, start=90, extent=90, fill=color, outline="")
    canvas.create_arc(w - d, 0, w, d, start=0, extent=90, fill=color, outline="")
    canvas.create_arc(0, h - d, d, h, start=180, extent=90, fill=color, outline="")
    canvas.create_arc(w - d, h - d, w, h, start=270, extent=90, fill=color, outline="")
    canvas.create_rectangle(radius, 0, w - radius, h, fill=color, outline="")
    canvas.create_rectangle(0, radius, w, h - radius, fill=color, outline="")

# --- WINDOW CLOSE HELPERS ---
def close_popup():
    global popup_window, brightness_slider_ref, night_slider_ref, contrast_slider_ref
    global brightness_label_ref, night_label_ref, contrast_label_ref, mode_btn_redraw_ref
    mode_btn_redraw_ref = None
    brightness_slider_ref = None
    night_slider_ref = None
    contrast_slider_ref = None
    brightness_label_ref = None
    night_label_ref = None
    contrast_label_ref = None
    if popup_window:
        try:
            popup_window.destroy()
        except Exception:
            pass
        popup_window = None

def close_about():
    global about_window
    if about_window:
        try:
            about_window.destroy()
        except Exception:
            pass
        about_window = None

# --- ABOUT WINDOW ---
_qr_cache = None

def show_about(icon=None, item=None):
    ui_call(_show_about_ui)

def _show_about_ui():
    global about_window
    if about_window is not None:
        try:
            about_window.focus_force()
            return
        except Exception:
            about_window = None

    win = tk.Toplevel(root_window)
    about_window = win
    win.withdraw()
    win.title(f"Eyedim {APP_VERSION}")
    win.geometry("260x280")
    win.attributes("-topmost", True)
    win.resizable(False, False)

    win.update_idletasks()
    try:
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        if not hwnd:
            hwnd = win.winfo_id()
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(ctypes.c_int(2)), ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass

    screen_width = win.winfo_screenwidth()
    screen_height = win.winfo_screenheight()
    win.geometry(f"+{screen_width // 2 - 130}+{screen_height // 2 - 140}")

    frame = tk.Frame(win, padx=12, pady=12, bg=PANEL_BG)
    win.config(bg=PANEL_BG)
    frame.pack(fill=tk.BOTH, expand=True)

    tk.Label(frame, text=f"Eyedim {APP_VERSION}", font=("Segoe UI", 11, "bold"),
             bg=PANEL_BG).pack(anchor="w")
    tk.Label(frame, text=t("about_desc"), font=("Segoe UI", 8), fg="#555555",
             bg=PANEL_BG, justify="left", wraplength=230).pack(anchor="w", pady=(2, 6))
    tk.Label(frame, text=t("about_author"), font=("Segoe UI", 8),
             bg=PANEL_BG, justify="left").pack(anchor="w", pady=(0, 4))
    tk.Label(frame, text=t("about_license"), font=("Segoe UI", 7), fg="#777777",
             bg=PANEL_BG, justify="left").pack(anchor="w", pady=(0, 6))
    tk.Label(frame, text=t("about_support"), font=("Segoe UI", 8, "bold"),
             bg=PANEL_BG).pack(anchor="w", pady=(2, 2))

    qr_lbl = tk.Label(frame, text="...", font=("Segoe UI", 7), fg="#777777", bg=PANEL_BG)
    qr_lbl.pack()

    def show_qr(data):
        if about_window is not win:
            return          # window was closed or rebuilt meanwhile
        try:
            from PIL import ImageTk
            qr_photo = ImageTk.PhotoImage(Image.open(io.BytesIO(data)))
            qr_lbl.config(image=qr_photo, text="")
            qr_lbl.image = qr_photo
        except Exception:
            qr_lbl.config(text="[QR Code Loading Error]", fg="red")

    def fetch_qr():
        global _qr_cache
        try:
            qr_url = ("https://api.qrserver.com/v1/create-qr-code/?size=90x90&data="
                      + urllib.parse.quote(DONATE_URL))
            with urllib.request.urlopen(qr_url, timeout=5) as response:
                _qr_cache = response.read()
        except Exception:
            ui_call(lambda: about_window is win and
                    qr_lbl.config(text="[QR Code Loading Error]", fg="red"))
            return
        ui_call(show_qr, _qr_cache)

    if _qr_cache:
        show_qr(_qr_cache)
    else:
        threading.Thread(target=fetch_qr, daemon=True).start()

    def on_close():
        global about_window
        about_window = None
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", on_close)
    win.deiconify()

# --- THIN SLIDER ---
class ThinSlider(tk.Canvas):
    def __init__(self, parent, from_=0, to=100, command=None, initial=0):
        super().__init__(parent, height=22, bg=PANEL_BG, highlightthickness=0)
        self.from_ = from_
        self.to = to
        self.command = command
        self._value = initial

        self.bind("<Configure>", self.draw)
        self.bind("<Button-1>", self.on_click)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<MouseWheel>", self.on_wheel)
        self.bind("<Button-4>", self.on_wheel)
        self.bind("<Button-5>", self.on_wheel)

    def get(self):
        return self._value

    def set(self, val, update_command=True):
        val = max(self.from_, min(self.to, int(val)))
        if val == self._value:
            return          # same value: no redraw, no system call, no save
        self._value = val
        self.draw()
        if update_command and self.command:
            self.command(self._value)

    def draw(self, event=None):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()

        thumb_size = 12
        margin = thumb_size // 2 + 2
        line_y = h // 2
        inner_w = w - 2 * margin

        if self.to > self.from_ and inner_w > 0:
            pos = margin + (inner_w * (self._value - self.from_) / (self.to - self.from_))
        else:
            pos = margin

        if pos > margin:
            self.create_rectangle(margin, line_y - 1, pos, line_y + 1,
                                  fill=ACCENT, outline="")
        if pos < w - margin:
            self.create_rectangle(pos, line_y - 1, w - margin, line_y + 1,
                                  fill="#cccccc", outline="")

        self.create_oval(pos - thumb_size // 2, line_y - thumb_size // 2,
                         pos + thumb_size // 2, line_y + thumb_size // 2,
                         fill=ACCENT, outline=ACCENT, width=0)

    def _update_from_event(self, event):
        w = self.winfo_width()
        thumb_size = 12
        margin = thumb_size // 2 + 2
        inner_w = w - 2 * margin
        if inner_w <= 0:
            return
        x = max(margin, min(w - margin, event.x))
        fraction = (x - margin) / inner_w
        val = self.from_ + fraction * (self.to - self.from_)
        self.set(val, update_command=True)

    def on_click(self, event):
        self._update_from_event(event)

    def on_drag(self, event):
        self._update_from_event(event)

    def on_wheel(self, event):
        delta = 1 if event.delta > 0 or event.num == 4 else -1
        self.set(self._value + delta * 3, update_command=True)

# --- POPUP WINDOW ---
def show_popup(icon=None, item=None):
    ui_call(_show_popup_ui)

def _show_popup_ui():
    global popup_window, brightness_slider_ref, night_slider_ref, contrast_slider_ref
    global brightness_label_ref, night_label_ref, contrast_label_ref, mode_btn_redraw_ref
    if popup_window is not None:
        close_popup()
        return

    close_about()

    popup_window = tk.Toplevel(root_window)
    popup_window.title(t("window_title"))
    popup_window.geometry(f"{POPUP_W}x{popup_height()}")
    popup_window.attributes("-topmost", True)
    popup_window.resizable(False, False)
    popup_window.overrideredirect(True)

    popup_window.update_idletasks()
    try:
        hwnd = ctypes.windll.user32.GetParent(popup_window.winfo_id())
        if not hwnd:
            hwnd = popup_window.winfo_id()
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(ctypes.c_int(2)), ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass

    screen_width = popup_window.winfo_screenwidth()
    screen_height = popup_window.winfo_screenheight()
    popup_window.geometry(f"+{screen_width - POPUP_W - 20}+{screen_height - popup_height() - 60}")

    frame = tk.Frame(popup_window, padx=16, pady=14, bg=PANEL_BG)
    popup_window.config(bg=PANEL_BG)
    frame.pack(fill=tk.BOTH, expand=True)
    frame.columnconfigure(1, weight=1)

    # Brightness row
    canvas_sun = tk.Canvas(frame, width=26, height=22, bg=PANEL_BG, highlightthickness=0)
    canvas_sun.grid(row=0, column=0, sticky="w", padx=(0, 4))
    draw_vector_sun(canvas_sun, 13, 11)

    brightness_slider_ref = ThinSlider(frame, from_=0, to=100,
                                       command=set_brightness, initial=current_brightness)
    brightness_slider_ref.grid(row=0, column=1, sticky="ew", padx=(0, 6))

    brightness_label_ref = tk.Label(frame, text=f"{current_brightness}",
                                    font=("Segoe UI", 10), fg=TEXT_COLOR,
                                    bg=PANEL_BG, width=3, anchor="e")
    brightness_label_ref.grid(row=0, column=2, sticky="e")

    # Night light row
    canvas_moon = tk.Canvas(frame, width=26, height=22, bg=PANEL_BG, highlightthickness=0)
    canvas_moon.grid(row=1, column=0, sticky="w", padx=(0, 4), pady=(2, 0))
    draw_vector_moon(canvas_moon, 13, 11, bg=PANEL_BG)

    night_slider_ref = ThinSlider(frame, from_=0, to=100,
                                  command=set_night_light, initial=current_night_light)
    night_slider_ref.grid(row=1, column=1, sticky="ew", padx=(0, 6), pady=(2, 0))

    night_label_ref = tk.Label(frame, text=f"{current_night_light}",
                               font=("Segoe UI", 10), fg=TEXT_COLOR,
                               bg=PANEL_BG, width=3, anchor="e")
    night_label_ref.grid(row=1, column=2, sticky="e", pady=(2, 0))

    # Contrast row
    canvas_contrast = tk.Canvas(frame, width=26, height=22, bg=PANEL_BG, highlightthickness=0)
    canvas_contrast.grid(row=2, column=0, sticky="w", padx=(0, 4), pady=(2, 0))
    draw_vector_contrast(canvas_contrast, 13, 11)

    contrast_slider_ref = ThinSlider(frame, from_=0, to=100,
                                     command=set_contrast, initial=current_contrast)
    contrast_slider_ref.grid(row=2, column=1, sticky="ew", padx=(0, 6), pady=(2, 0))

    contrast_label_ref = tk.Label(frame, text=f"{current_contrast}",
                                  font=("Segoe UI", 10), fg=TEXT_COLOR,
                                  bg=PANEL_BG, width=3, anchor="e")
    contrast_label_ref.grid(row=2, column=2, sticky="e", pady=(2, 0))

    # Mode button
    btn_canvas = tk.Canvas(frame, height=30, bg=PANEL_BG,
                           highlightthickness=0, cursor="hand2")
    btn_canvas.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def current_bg():
        return BTN_BG

    def current_hover_bg():
        return BTN_BG_HOVER

    def draw_rounded_btn(bg_col):
        btn_canvas.delete("all")
        w = btn_canvas.winfo_width()
        h = btn_canvas.winfo_height()
        if w <= 1:
            w = 216
        if h <= 1:
            h = 30

        draw_round_rect(btn_canvas, w, h, bg_col)

        label = t("mode_default") if preset_active else t("mode_user")
        text_id = btn_canvas.create_text(w / 2 + 12, h / 2, text=label,
                                         fill=TEXT_COLOR, font=("Segoe UI", 10),
                                         anchor="center")
        bbox = btn_canvas.bbox(text_id)
        icon_x = (bbox[0] - 16) if bbox else (w / 2 - 40)
        icon_y = h / 2

        draw_vector_gear(btn_canvas, icon_x, icon_y)

    btn_canvas.bind("<Configure>", lambda e: draw_rounded_btn(current_bg()))
    btn_canvas.bind("<Enter>", lambda e: draw_rounded_btn(current_hover_bg()))
    btn_canvas.bind("<Leave>", lambda e: draw_rounded_btn(current_bg()))

    def on_btn_click(e):
        toggle_preset()
        draw_rounded_btn(current_bg())

    btn_canvas.bind("<Button-1>", on_btn_click)
    mode_btn_redraw_ref = lambda: draw_rounded_btn(current_bg())

    # Contrast on/off toggle (right of the Mode button)
    tog_canvas = tk.Canvas(frame, width=30, height=30, bg=PANEL_BG,
                           highlightthickness=0, cursor="hand2")
    tog_canvas.grid(row=3, column=2, sticky="e", padx=(6, 0), pady=(8, 0))
    contrast_row_widgets = (canvas_contrast, contrast_slider_ref, contrast_label_ref)

    def draw_toggle(hover=False):
        tog_canvas.delete("all")
        if show_contrast:
            col = "#efd29a" if hover else "#f6dfb8"
        else:
            col = BTN_BG_HOVER if hover else BTN_BG
        draw_round_rect(tog_canvas, 30, 30, col)
        draw_vector_contrast(tog_canvas, 15, 15)

    def apply_contrast_visibility():
        for wdg in contrast_row_widgets:
            if show_contrast:
                wdg.grid()
            else:
                wdg.grid_remove()
        h = popup_height()
        sw = popup_window.winfo_screenwidth()
        sh = popup_window.winfo_screenheight()
        popup_window.geometry(f"{POPUP_W}x{h}+{sw - POPUP_W - 20}+{sh - h - 60}")
        draw_toggle()

    def on_toggle_contrast(e=None):
        global show_contrast
        show_contrast = not show_contrast
        request_gamma_update(current_night_light, effective_contrast())
        update_tray_icon()
        schedule_save()
        apply_contrast_visibility()

    tog_canvas.bind("<Enter>", lambda e: draw_toggle(True))
    tog_canvas.bind("<Leave>", lambda e: draw_toggle(False))
    tog_canvas.bind("<Button-1>", on_toggle_contrast)
    apply_contrast_visibility()

    popup_window.bind("<FocusOut>", lambda e: close_popup())
    popup_window.focus_force()

# --- DISPLAY PANEL TEST ---
# Full-screen solid colours and test patterns for checking a monitor for dead
# or stuck pixels, backlight bleed and geometry. Opens on every monitor at once
# (all show the same screen); night light and contrast stay neutral meanwhile.
PANEL_TEST_SOLIDS = [(0, 0, 0), (255, 255, 255), (255, 0, 0),
                     (0, 255, 0), (0, 0, 255), (128, 128, 128)]
PANEL_TEST_HINT_MS = 2500
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

def _grid_lines(draw, w, h, cell, color):
    """1 px lines every `cell` pixels, aligned so that lines cross the centre."""
    cx, cy = w // 2, h // 2
    for x in range(cx % cell, w, cell):
        draw.line([(x, 0), (x, h - 1)], fill=color)
    for y in range(cy % cell, h, cell):
        draw.line([(0, y), (w - 1, y)], fill=color)

def render_grid(w, h, divisor=1):
    """Grid of ~12 cells across the short side, `divisor` times finer."""
    img = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(img)
    coarse = max(8, round(min(w, h) / 12))
    _grid_lines(d, w, h, max(4, coarse // divisor), (255, 255, 255))
    edge = max(2, round(w / 960))
    d.rectangle([0, 0, w - 1, h - 1], outline=(255, 255, 255), width=edge)
    cx, cy = w // 2, h // 2
    d.line([(cx, 0), (cx, h - 1)], fill=(255, 255, 255), width=edge)
    d.line([(0, cy), (w - 1, cy)], fill=(255, 255, 255), width=edge)
    return img

def render_test_card(w, h):
    img = Image.new("RGB", (w, h), (60, 60, 60))
    d = ImageDraw.Draw(img)
    _grid_lines(d, w, h, max(8, round(min(w, h) / 10)), (232, 232, 232))
    cell = max(8, round(min(w, h) / 10))
    line_w = max(1, cell // 20)
    cr = round(cell * 0.9)
    for x, y in ((cr * 1.3, cr * 1.3), (w - cr * 1.3, cr * 1.3),
                 (cr * 1.3, h - cr * 1.3), (w - cr * 1.3, h - cr * 1.3)):
        d.ellipse([x - cr, y - cr, x + cr, y + cr], outline=(255, 255, 255), width=line_w)

    # everything inside the big circle is drawn on its own square, then masked
    R = round(min(w, h) * 0.44)
    S = 2 * R
    inner = Image.new("RGB", (S, S), (26, 26, 26))
    di = ImageDraw.Draw(inner)
    band = S / 6

    def box(x0, y0, x1, y1, fill):
        di.rectangle([round(x0), round(y0), round(x1) - 1, round(y1) - 1], fill=fill)

    bars = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0),
            (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0)]
    for i, col in enumerate(bars):                       # colour bars
        box(i * S / 8, 0, (i + 1) * S / 8, band * 1.6, col)
    for i in range(11):                                  # grey steps 0..100 %
        v = round(i * 25.5)
        box(i * S / 11, band * 1.6, (i + 1) * S / 11, band * 2.5, (v, v, v))
    gy0, gy1 = round(band * 2.5), round(band * 3.2)      # black-to-white ramp
    for x in range(S):
        v = round(x * 255 / max(1, S - 1))
        di.line([(x, gy0), (x, gy1 - 1)], fill=(v, v, v))
    fy0, fy1 = band * 3.2, band * 4.2                    # line-pair wedges
    for gi, p in enumerate((8, 5, 3, 2, 1)):
        gx = round(S * 0.08 + gi * S * 0.17)
        gw = round(S * 0.15)
        box(gx, fy0, gx + gw, fy1, (0, 0, 0))
        for x in range(0, gw, p * 2):
            box(gx + x, fy0, gx + min(x + p, gw), fy1, (255, 255, 255))
    for i, col in enumerate([(204, 0, 0), (0, 170, 0), (0, 0, 204)]):
        box(i * S / 3, band * 4.2, (i + 1) * S / 3, band * 5, col)
    box(0, band * 5, S, S, (255, 255, 255))
    font = _get_font(max(10, round(R * 0.11)))
    tb = di.textbbox((0, 0), "EYEDIM", font=font)
    di.text((R - (tb[2] - tb[0]) / 2 - tb[0], band * 5.5 - (tb[3] - tb[1]) / 2 - tb[1]),
            "EYEDIM", fill=(0, 0, 0), font=font)

    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, S - 1, S - 1], fill=255)
    cx, cy = w // 2, h // 2
    img.paste(inner, (cx - R, cy - R), mask)
    d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=(255, 255, 255),
              width=max(2, R // 80))
    d.line([(cx - R * 0.12, cy + R * 0.5), (cx + R * 0.12, cy + R * 0.5)],
           fill=(255, 255, 255), width=max(1, R // 150))
    return img

PANEL_TEST_PATTERNS = [lambda w, h: render_grid(w, h),
                       lambda w, h: render_grid(w, h, 4),
                       lambda w, h: render_grid(w, h, 8),
                       render_test_card]

def _monitor_rects():
    """Rectangles of all monitors; the one under the mouse cursor goes first."""
    rects = []
    try:
        cursor = win32api.GetCursorPos()
        for _h, _dc, rect in win32api.EnumDisplayMonitors(None, None):
            l, t_, r, b = rect
            under = l <= cursor[0] < r and t_ <= cursor[1] < b
            rects.insert(0, rect) if under else rects.append(rect)
    except Exception:
        pass
    if not rects:
        rects = [(0, 0, root_window.winfo_screenwidth(), root_window.winfo_screenheight())]
    return rects

def show_panel_test(icon=None, item=None):
    ui_call(_toggle_panel_test)

def _toggle_panel_test():
    if panel_test_windows:
        close_panel_test()
    else:
        _open_panel_test()

def close_panel_test(restore_gamma=True):
    global panel_test_windows
    wins = panel_test_windows
    if not wins:
        return
    panel_test_windows = []
    for win in wins:
        try:
            win.destroy()
        except Exception:
            pass
    if restore_gamma and not _shutting_down.is_set():
        request_gamma_update(*gamma_target())

def _open_panel_test():
    global panel_test_windows
    close_popup()
    close_about()

    # Create the windows per-monitor DPI aware, so that on a scaled display
    # (125 %, 150 % ...) every pattern pixel is one physical pixel and Windows
    # does not blur the picture. The rest of the app stays as it is.
    user32 = ctypes.windll.user32
    old_ctx = None
    try:
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        old_ctx = user32.SetThreadDpiAwarenessContext(
            ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
    except Exception:
        old_ctx = None
    screens = []                                # (window, canvas, w, h) per monitor
    try:
        for left, top, right, bottom in _monitor_rects():
            w, h = right - left, bottom - top
            win = tk.Toplevel(root_window, bg="black")
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.geometry(f"{w}x{h}+{left}+{top}")
            canvas = tk.Canvas(win, width=w, height=h, bg="black",
                               highlightthickness=0, cursor="none")
            canvas.pack(fill=tk.BOTH, expand=True)
            screens.append((win, canvas, w, h))
        root_window.update_idletasks()
    finally:
        if old_ctx:
            try:
                user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old_ctx))
            except Exception:
                pass

    panel_test_windows = [s[0] for s in screens]
    request_gamma_update(0, 50, instant=True)

    total = len(PANEL_TEST_SOLIDS) + len(PANEL_TEST_PATTERNS)
    state = {"i": 0, "hint_job": None}
    photos = {}                                 # (pattern, w, h) -> PhotoImage

    def hide_hint():
        state["hint_job"] = None
        for _win, canvas, _w, _h in screens:
            canvas.delete("hint")

    def draw(canvas, w, h, i):
        canvas.delete("all")
        if i < len(PANEL_TEST_SOLIDS):
            canvas.config(bg="#%02x%02x%02x" % PANEL_TEST_SOLIDS[i])
            return
        key = (i, w, h)
        if key not in photos:
            from PIL import ImageTk
            img = PANEL_TEST_PATTERNS[i - len(PANEL_TEST_SOLIDS)](w, h)
            photos[key] = ImageTk.PhotoImage(img, master=canvas)
        canvas.config(bg="black")
        canvas.create_image(0, 0, image=photos[key], anchor="nw")

    def draw_hint(canvas, w, h, text):
        font_px = max(11, h // 70)
        tid = canvas.create_text(w // 2, h - h // 16, text=text, fill="#eeeeee",
                                 font=("Segoe UI", -font_px), tags="hint")
        x0, y0, x1, y1 = canvas.bbox(tid)
        pad = font_px // 2
        bg = canvas.create_rectangle(x0 - pad * 2, y0 - pad, x1 + pad * 2, y1 + pad,
                                     fill="#202020", outline="#3a3a3a", tags="hint")
        canvas.tag_lower(bg, tid)

    def show():
        i = state["i"]
        names = t("test_screens")
        text = t("test_hint").format(i + 1, total, names[i] if i < len(names) else "")
        for _win, canvas, w, h in screens:
            draw(canvas, w, h, i)
            draw_hint(canvas, w, h, text)
        if state["hint_job"]:
            root_window.after_cancel(state["hint_job"])
        state["hint_job"] = root_window.after(PANEL_TEST_HINT_MS, hide_hint)

    def step(delta):
        if not panel_test_windows:
            return
        state["i"] = (state["i"] + delta) % total
        show()

    for win, _canvas, _w, _h in screens:
        win.bind("<Button-1>", lambda e: step(1))
        win.bind("<space>", lambda e: step(1))
        win.bind("<Right>", lambda e: step(1))
        win.bind("<Return>", lambda e: step(1))
        win.bind("<Button-3>", lambda e: step(-1))
        win.bind("<Left>", lambda e: step(-1))
        win.bind("<BackSpace>", lambda e: step(-1))
        win.bind("<Escape>", lambda e: close_panel_test())

    show()
    for win, _canvas, _w, _h in screens:
        win.lift()
    screens[0][0].focus_force()             # keyboard goes to the cursor's monitor

# --- QUIT ---
def quit_app(icon, item):
    global _exiting
    _exiting = True
    _shutting_down.set()
    try:
        icon.visible = False          # tray icon disappears immediately
    except Exception:
        pass
    if _reapply_timer:
        _reapply_timer.cancel()
    stop_wheel_control()
    brightness_queue.put(None)
    brightness_thread.join(1.0)

    # back to the original state: neutral gamma + brightness from before launch
    request_gamma_update(0, 50, instant=True)
    apply_gamma_direct(0, 50)
    restore_original_brightness()
    apply_gamma_direct(0, 50)         # DDC writes may reset the ramp again
    save_config(clean_exit=True)

    def shutdown():
        close_panel_test(restore_gamma=False)
        close_popup()
        close_about()
        try:
            root_window.quit()
            root_window.destroy()
        except Exception:
            pass
        icon.stop()

    ui_call(shutdown)

# --- MOUSE WHEEL OVER THE TRAY ICON ---
# Windows never sends wheel messages to tray icons, so we catch them with a
# low-level mouse hook. The hook is installed only while the cursor is over
# our icon (the icon reports WM_MOUSEMOVE) and is removed as soon as it leaves,
# so the rest of the time nothing runs and other programs keep their wheel.
WHEEL_STEP = 5                      # % per wheel notch
WHEEL_DELTA = 120
WHEEL_POLL_MS = 150                 # how often we check that the cursor is still there
WM_MOUSEMOVE = 0x0200
WM_MOUSEWHEEL = 0x020A
WM_TIMER = 0x0113
WM_QUIT = 0x0012
WM_APP_WHEEL_ARM = 0x8000 + 21
WM_APP_WHEEL_STEP = 0x8000 + 22
WH_MOUSE_LL = 14
HC_ACTION = 0

class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]

class _NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("guidItem", GUID)]

_LowLevelMouseProc = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int,
                                        wintypes.WPARAM, wintypes.LPARAM)
_wheel_tid = None
_wheel_armed = False

def _wheel_apply(steps):
    if _shutting_down.is_set() or steps == 0:
        return
    cur = current_brightness
    # snap to the step grid, so 83 goes to 85 / 80 and then in whole steps
    if steps > 0:
        val = (cur // WHEEL_STEP + steps) * WHEEL_STEP
    else:
        val = (-(-cur // WHEEL_STEP) + steps) * WHEEL_STEP
    val = max(0, min(100, val))
    if val != cur:
        set_brightness(val)         # same smooth path as the slider

def _wheel_thread_main(ready):
    global _wheel_tid, _wheel_armed
    user32 = ctypes.windll.user32
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _LowLevelMouseProc,
                                         wintypes.HINSTANCE, wintypes.DWORD]
    user32.CallNextHookEx.restype = wintypes.LPARAM
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                      wintypes.WPARAM, wintypes.LPARAM]
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]
    user32.SetTimer.restype = ctypes.c_size_t
    user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
    user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
    kernel32 = ctypes.windll.kernel32
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    shell32 = ctypes.windll.shell32
    shell32.Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(_NOTIFYICONIDENTIFIER),
                                                ctypes.POINTER(wintypes.RECT)]
    # physical pixels everywhere: the hook, the cursor and the icon rect
    try:
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext(
            ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
    except Exception:
        pass

    tid = kernel32.GetCurrentThreadId()
    msg = wintypes.MSG()
    user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)   # create the queue
    _wheel_tid = tid
    ready.set()

    hook = None
    timer = 0
    rect = wintypes.RECT()
    acc = 0                         # sub-notch delta from smooth wheels / touchpads

    def icon_rect():
        icon = tray_icon_ref
        hwnd = getattr(icon, "_hwnd", None)
        if not hwnd:
            return False
        # pystray registers the icon with uID 0; try id(icon) too just in case
        for uid in (0, id(icon) & 0xFFFFFFFF):
            nid = _NOTIFYICONIDENTIFIER(cbSize=ctypes.sizeof(_NOTIFYICONIDENTIFIER),
                                        hWnd=hwnd, uID=uid)
            r = wintypes.RECT()
            if shell32.Shell_NotifyIconGetRect(ctypes.byref(nid), ctypes.byref(r)) == 0:
                rect.left, rect.top, rect.right, rect.bottom = r.left, r.top, r.right, r.bottom
                return True
        return False

    def inside(x, y):
        return rect.left <= x < rect.right and rect.top <= y < rect.bottom

    def on_wheel(l_param):
        nonlocal acc
        info = ctypes.cast(l_param, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
        if not inside(info.pt.x, info.pt.y):
            return False
        acc += ctypes.c_short((info.mouseData >> 16) & 0xFFFF).value
        steps = int(acc / WHEEL_DELTA)
        if steps:
            acc -= steps * WHEEL_DELTA
            # do the work outside the hook so the mouse never stutters
            user32.PostThreadMessageW(tid, WM_APP_WHEEL_STEP, steps & 0xFFFFFFFF, 0)
        return True

    def hook_proc(n_code, w_param, l_param):
        try:
            if n_code == HC_ACTION and w_param == WM_MOUSEWHEEL and on_wheel(l_param):
                return 1            # swallow: the taskbar should not scroll
        except Exception:
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    proc = _LowLevelMouseProc(hook_proc)    # keep a reference while hooked

    def disarm():
        global _wheel_armed
        nonlocal hook, timer, acc
        if timer:
            user32.KillTimer(None, timer)
            timer = 0
        if hook:
            user32.UnhookWindowsHookEx(hook)
            hook = None
        acc = 0
        _wheel_armed = False

    def cursor_inside():
        pt = wintypes.POINT()
        return bool(user32.GetCursorPos(ctypes.byref(pt))) and inside(pt.x, pt.y)

    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_APP_WHEEL_ARM:
                if _shutting_down.is_set():
                    continue
                if not icon_rect():
                    # no rect from the shell: a small box around the cursor
                    pt = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    rect.left, rect.top = pt.x - 16, pt.y - 16
                    rect.right, rect.bottom = pt.x + 16, pt.y + 16
                if hook is None:
                    hook = user32.SetWindowsHookExW(WH_MOUSE_LL, proc,
                                                    kernel32.GetModuleHandleW(None), 0)
                    if not hook:
                        _wheel_armed = False
                        continue
                if not timer:
                    timer = user32.SetTimer(None, 0, WHEEL_POLL_MS, None)
                _wheel_armed = True
            elif msg.message == WM_TIMER:
                if hook is not None and (_shutting_down.is_set() or not cursor_inside()):
                    disarm()
            elif msg.message == WM_APP_WHEEL_STEP:
                steps = ctypes.c_int(msg.wParam & 0xFFFFFFFF).value
                ui_call(_wheel_apply, steps)
            else:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        disarm()

def start_wheel_control(icon):
    """Route wheel scrolls over the tray icon to the brightness."""
    ready = threading.Event()
    threading.Thread(target=_wheel_thread_main, args=(ready,), daemon=True).start()
    ready.wait(2.0)
    handlers = getattr(icon, "_message_handlers", None)
    on_notify = getattr(icon, "_on_notify", None)
    if not isinstance(handlers, dict) or on_notify is None:
        return                      # unknown pystray internals: skip the feature
    for code, fn in list(handlers.items()):
        if fn == on_notify:
            def notify(wparam, lparam, _orig=fn):
                if lparam == WM_MOUSEMOVE and not _wheel_armed and _wheel_tid:
                    ctypes.windll.user32.PostThreadMessageW(_wheel_tid, WM_APP_WHEEL_ARM, 0, 0)
                return _orig(wparam, lparam)
            handlers[code] = notify
            break

def stop_wheel_control():
    if _wheel_tid:
        try:
            ctypes.windll.user32.PostThreadMessageW(_wheel_tid, WM_QUIT, 0, 0)
        except Exception:
            pass

# --- TRAY ICON SETUP ---
tray_icon_ref = pystray.Icon(
    "Eyedim",
    create_text_icon(current_brightness),
    f"Eyedim {APP_VERSION} — Brightness: {current_brightness}%",
    pystray.Menu(
        pystray.MenuItem(lambda item: t("menu_settings"), show_popup, default=True),
        pystray.MenuItem(lambda item: t("menu_panel_test"), show_panel_test),
        pystray.MenuItem(lambda item: t("menu_about"), show_about),
        pystray.MenuItem(lambda item: t("menu_autostart"), toggle_autostart,
                         checked=lambda item: is_autostart_enabled()),
        pystray.MenuItem(lambda item: t("menu_lang"), toggle_language),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda item: t("menu_exit"), quit_app)
    )
)

tray_icon_ref.default_action = show_popup
start_wheel_control(tray_icon_ref)
update_tray_icon()          # localized tooltip from the very start
# apply the stored state right away, then keep it alive across sleep/display changes
enqueue_brightness(current_brightness, force=True)
request_gamma_update(current_night_light, effective_contrast(), instant=True)
start_display_watcher()
save_config()

tray_icon_ref.run()