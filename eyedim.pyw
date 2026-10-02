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

# --- SYSTEM CONSTANTS ---
DWMWA_WINDOW_CORNER_PREFERENCE = 33
APP_VERSION = "v2.0.8"
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
        "tooltip_format_short": "Eyedim — Brightness: {}% | Night Light: {}%"
    },
    "ru": {
        "menu_settings": "Открыть настройки",
        "menu_about": "О программе Eyedim",
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
        "tooltip_format_short": "Eyedim — Яркость: {}% | Ночной свет: {}%"
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

_save_timer = None
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

def schedule_save():
    global _save_timer
    if _save_timer:
        _save_timer.cancel()
    _save_timer = threading.Timer(0.5, save_config)
    _save_timer.daemon = True
    _save_timer.start()

popup_window = None
about_window = None
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
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup_registry_key, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, app_name)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False

def toggle_autostart(icon, item):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup_registry_key, 0, winreg.KEY_SET_VALUE)
        if is_autostart_enabled():
            winreg.DeleteValue(key, app_name)
        else:
            if getattr(sys, 'frozen', False):
                app_path = f'"{sys.executable}"'
            else:
                app_path = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, app_path)
        winreg.CloseKey(key)
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

def update_tray_icon():
    global tray_icon_ref
    if tray_icon_ref:
        tray_icon_ref.icon = create_text_icon(current_brightness)
        if show_contrast:
            tray_icon_ref.title = TRANSLATIONS[LANG]["tooltip_format"].format(
                current_brightness, current_night_light, current_contrast)
        else:
            tray_icon_ref.title = TRANSLATIONS[LANG]["tooltip_format_short"].format(
                current_brightness, current_night_light)

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
def build_gamma_ramp(nl, c):
    nl_factor = nl / 100.0
    c_factor = (c - 50) / 50.0

    gf = max(GAMMA_GREEN_MIN, 1.0 - 0.40 * nl_factor)
    bf = max(GAMMA_BLUE_MIN, 1.0 - 0.75 * nl_factor)

    ramp = bytearray(1536)
    for i in range(256):
        val_base = i * 257

        if c_factor != 0:
            normalized = (val_base - 32768) / 32768.0
            if c_factor > 0:
                adjusted = normalized * (1.0 + c_factor * 0.5)
            else:
                adjusted = normalized * (1.0 + c_factor * 0.4)
            val_base = max(0, min(65535, int(32768 + adjusted * 32768)))

        r = min(65535, int(val_base * 1.0))
        g = min(65535, int(val_base * gf))
        b = min(65535, int(val_base * bf))

        ramp[i * 2: i * 2 + 2] = r.to_bytes(2, 'little')
        ramp[512 + i * 2: 512 + i * 2 + 2] = g.to_bytes(2, 'little')
        ramp[1024 + i * 2: 1024 + i * 2 + 2] = b.to_bytes(2, 'little')
    return bytes(ramp)

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

# --- GAMMA WORKER ---
gamma_lock = threading.Lock()
gamma_dirty = threading.Event()
gamma_pending = {"nl": current_night_light, "c": effective_contrast()}

def request_gamma_update(nl=None, c=None):
    with gamma_lock:
        if nl is not None:
            gamma_pending["nl"] = nl
        if c is not None:
            gamma_pending["c"] = c
    gamma_dirty.set()

def read_pending_gamma():
    with gamma_lock:
        return gamma_pending["nl"], gamma_pending["c"]

def gamma_worker():
    while True:
        gamma_dirty.wait()
        gamma_dirty.clear()
        nl, c = read_pending_gamma()
        apply_gamma_direct(nl, c)

threading.Thread(target=gamma_worker, daemon=True).start()

# --- BRIGHTNESS WORKER ---
brightness_queue = queue.Queue()
BRIGHTNESS_STEP = 5
GAMMA_RESTORE_DELAY = 0.12
MAX_BRIGHTNESS_RETRIES = 3          # quick retries: 0.5 s, 1 s, 1.5 s
desired_brightness = current_brightness
_shutting_down = threading.Event()

def brightness_worker():
    last_applied = -1
    restore_timer = None
    fail_streak = 0

    def restore_gamma_later():
        time.sleep(GAMMA_RESTORE_DELAY)
        nl, c = read_pending_gamma()
        apply_gamma_direct(nl, c)

    while True:
        item = brightness_queue.get()
        if item is None:
            return
        val, force = item
        while not brightness_queue.empty():
            try:
                nxt = brightness_queue.get_nowait()
            except queue.Empty:
                break
            if nxt is None:
                return
            val = nxt[0]
            force = force or nxt[1]
        if _shutting_down.is_set():
            continue
        if val == last_applied and not force:
            continue
        try:
            sbc.set_brightness(val)
            last_applied = val
            fail_streak = 0
            if restore_timer is not None:
                restore_timer.cancel()
            restore_timer = threading.Timer(GAMMA_RESTORE_DELAY, restore_gamma_later)
            restore_timer.daemon = True
            restore_timer.start()
        except Exception:
            if fail_streak < MAX_BRIGHTNESS_RETRIES:
                fail_streak += 1
                tm = threading.Timer(0.5 * fail_streak,
                                     lambda: enqueue_brightness(desired_brightness, force=True))
                tm.daemon = True
                tm.start()

brightness_thread = threading.Thread(target=brightness_worker, daemon=True)
brightness_thread.start()

def enqueue_brightness(value, force=False):
    global desired_brightness
    if _shutting_down.is_set():
        return
    snapped = max(0, min(100, int(round(value / BRIGHTNESS_STEP)) * BRIGHTNESS_STEP))
    desired_brightness = snapped
    brightness_queue.put((snapped, force))

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
def reapply_all():
    request_gamma_update(current_night_light, effective_contrast())
    enqueue_brightness(current_brightness, force=True)

def schedule_reapply(delay=1.0):
    global _reapply_timer
    if _shutting_down.is_set():
        return
    if _reapply_timer:
        _reapply_timer.cancel()
    _reapply_timer = threading.Timer(delay, reapply_all)
    _reapply_timer.daemon = True
    _reapply_timer.start()

WM_DISPLAYCHANGE = 0x007E
WM_POWERBROADCAST = 0x0218
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012

def start_display_watcher():
    def run():
        try:
            def wndproc(hwnd, msg, wparam, lparam):
                if msg == WM_DISPLAYCHANGE or (
                        msg == WM_POWERBROADCAST and
                        wparam in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC)):
                    schedule_reapply()
                return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

            wc = win32gui.WNDCLASS()
            wc.hInstance = win32api.GetModuleHandle(None)
            wc.lpszClassName = "EyedimWatcher"
            wc.lpfnWndProc = wndproc
            cls = win32gui.RegisterClass(wc)
            win32gui.CreateWindow(cls, "EyedimWatcher", 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None)
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
    update_popup_labels_and_sliders()

def set_contrast(val):
    global current_contrast
    current_contrast = max(0, min(100, int(val)))
    request_gamma_update(c=effective_contrast())
    _commit_user_mode()
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

    try:
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=90x90&data={urllib.parse.quote(DONATE_URL)}"
        with urllib.request.urlopen(qr_url, timeout=2) as response:
            qr_data = response.read()
        qr_img_raw = Image.open(io.BytesIO(qr_data))
        from PIL import ImageTk
        qr_photo = ImageTk.PhotoImage(qr_img_raw)
        qr_lbl = tk.Label(frame, image=qr_photo, bg=PANEL_BG)
        qr_lbl.image = qr_photo
        qr_lbl.pack()
    except Exception:
        tk.Label(frame, text="[QR Code Loading Error]", font=("Segoe UI", 7),
                 fg="red", bg=PANEL_BG).pack()

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
        self._value = max(self.from_, min(self.to, int(val)))
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

        radius = 6
        btn_canvas.create_arc(0, 0, radius * 2, radius * 2, start=90, extent=90,
                              fill=bg_col, outline="")
        btn_canvas.create_arc(w - radius * 2, 0, w, radius * 2, start=0, extent=90,
                              fill=bg_col, outline="")
        btn_canvas.create_arc(0, h - radius * 2, radius * 2, h, start=180, extent=90,
                              fill=bg_col, outline="")
        btn_canvas.create_arc(w - radius * 2, h - radius * 2, w, h, start=270, extent=90,
                              fill=bg_col, outline="")
        btn_canvas.create_rectangle(radius, 0, w - radius, h, fill=bg_col, outline="")
        btn_canvas.create_rectangle(0, radius, w, h - radius, fill=bg_col, outline="")

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

# --- QUIT ---
def quit_app(icon, item):
    global _exiting
    _exiting = True
    _shutting_down.set()
    try:
        icon.visible = False          # tray icon disappears immediately
    except Exception:
        pass
    for tm in (_save_timer, _reapply_timer):
        if tm:
            tm.cancel()
    brightness_queue.put(None)
    brightness_thread.join(1.0)

    # back to the original state: neutral gamma + brightness from before launch
    request_gamma_update(0, 50)
    apply_gamma_direct(0, 50)
    restore_original_brightness()
    apply_gamma_direct(0, 50)         # DDC writes may reset the ramp again
    save_config(clean_exit=True)

    def shutdown():
        close_popup()
        close_about()
        try:
            root_window.quit()
            root_window.destroy()
        except Exception:
            pass
        icon.stop()

    ui_call(shutdown)

# --- TRAY ICON SETUP ---
tray_icon_ref = pystray.Icon(
    "Eyedim",
    create_text_icon(current_brightness),
    f"Eyedim {APP_VERSION} — Brightness: {current_brightness}%",
    pystray.Menu(
        pystray.MenuItem(lambda item: t("menu_settings"), show_popup, default=True),
        pystray.MenuItem(lambda item: t("menu_about"), show_about),
        pystray.MenuItem(lambda item: t("menu_autostart"), toggle_autostart,
                         checked=lambda item: is_autostart_enabled()),
        pystray.MenuItem(lambda item: t("menu_lang"), toggle_language),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda item: t("menu_exit"), quit_app)
    )
)

tray_icon_ref.default_action = show_popup
# apply the stored state right away, then keep it alive across sleep/display changes
enqueue_brightness(current_brightness, force=True)
request_gamma_update(current_night_light, effective_contrast())
start_display_watcher()
save_config()

tray_icon_ref.run()