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
import time

# --- SYSTEM CONSTANTS ---
DWMWA_WINDOW_CORNER_PREFERENCE = 33  # Window corner rounding preference for Win11
APP_VERSION = "v2.0.6"
DONATE_URL = "https://your-donation-link.com"  # Insert your payment link here

# --- GAMMA SAFETY LIMITS ---
# Many drivers (especially Intel integrated / laptop panels) silently
# reject SetDeviceGammaRamp when the blue channel drops below ~0.5 or
# the green channel below ~0.7 — the ramp must stay monotonic and
# within a range the driver considers valid. Capping the factors here
# guarantees the ramp is always accepted, at the cost of a slightly
# weaker maximum night-light effect. This is what fixed the
# "night light stops working above ~65%" bug.
GAMMA_GREEN_MIN = 0.70
GAMMA_BLUE_MIN = 0.55

# --- LOCALIZATION DICTIONARIES ---
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
        "about_author": "Developer: Lukin Yaroslav\nCreated with AI assistance.",
        "about_license": "Distributed under GNU GPLv3 terms.\nProvided AS IS, without any warranties.",
        "about_support": "Support the project:",
        "tooltip_format": "Eyedim — Brightness: {}% | Night Light: {}% | Contrast: {}%"
    },
    "ru": {
        "menu_settings": "Открыть настройки",
        "menu_about": "О программе Eyedim",
        "menu_autostart": "Запускать при старте Windows",
        "menu_lang": "Язык: Русский",
        "menu_exit": "Выход",
        "mode_user": "Режим: User",
        "mode_default": "Режим: Default",
        "window_title": "Eyedim - Настройки дисплея",
        "about_title": "О программе Eyedim",
        "about_desc": "Легковесная утилита для управления яркостью, ночным светом и контрастом.",
        "about_author": "Разработчик: Лукин Ярослав\nСоздано при содействии ИИ.",
        "about_license": "Распространяется на условиях GNU GPLv3.\nПоставляется БЕЗ КАКИХ-ЛИБО ГАРАНТИЙ.",
        "about_support": "Поддержать проект:",
        "tooltip_format": "Eyedim — Яркость: {}% | Ночной свет: {}% | Контраст: {}%"
    }
}

def t(key):
    return TRANSLATIONS[LANG].get(key, key)

# Initialize initial values
try:
    current_brightness = sbc.get_brightness(display=0)[0]
except Exception:
    current_brightness = 50

current_night_light = 0
current_contrast = 50

preset_active = False  # False = User mode, True = Default mode
saved_brightness = current_brightness
saved_night_light = current_night_light
saved_contrast = current_contrast

popup_window = None
about_window = None
tray_icon_ref = None
brightness_slider_ref = None
night_slider_ref = None
contrast_slider_ref = None
brightness_label_ref = None
night_label_ref = None
contrast_label_ref = None
preset_btn_ref = None

startup_registry_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
app_name = "Eyedim"

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
    update_tray_icon()
    icon.update_menu()
    if popup_window:
        close_popup()
        show_popup()

def update_tray_icon():
    global tray_icon_ref
    if tray_icon_ref:
        tray_icon_ref.icon = create_text_icon(current_brightness)
        tray_icon_ref.title = TRANSLATIONS[LANG]["tooltip_format"].format(current_brightness, current_night_light, current_contrast)

def update_popup_labels_and_sliders():
    global brightness_slider_ref, night_slider_ref, contrast_slider_ref, brightness_label_ref, night_label_ref, contrast_label_ref
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

# ============================================================
# GAMMA RAMP BUILD / APPLY (module level)
# ============================================================
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

# ============================================================
# GAMMA STATE — lock + dirty flag, no queue races
# ============================================================
gamma_lock = threading.Lock()
gamma_dirty = threading.Event()
gamma_pending = {"nl": current_night_light, "c": current_contrast}

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

# ============================================================
# BRIGHTNESS WORKER — single thread, "last value wins", step 5
# ============================================================
brightness_queue = queue.Queue()
BRIGHTNESS_STEP = 5
GAMMA_RESTORE_DELAY = 0.12

def brightness_worker():
    last_applied = -1
    restore_timer = None

    def restore_gamma_later():
        time.sleep(GAMMA_RESTORE_DELAY)
        nl, c = read_pending_gamma()
        apply_gamma_direct(nl, c)

    while True:
        val = brightness_queue.get()
        if val is None:
            break
        while not brightness_queue.empty():
            try:
                val = brightness_queue.get_nowait()
            except queue.Empty:
                break
        if val == last_applied:
            continue
        try:
            sbc.set_brightness(val)
            last_applied = val
            if restore_timer is not None:
                restore_timer.cancel()
            restore_timer = threading.Timer(GAMMA_RESTORE_DELAY, restore_gamma_later)
            restore_timer.daemon = True
            restore_timer.start()
        except Exception:
            pass

threading.Thread(target=brightness_worker, daemon=True).start()

def enqueue_brightness(value):
    snapped = max(0, min(100, int(round(value / BRIGHTNESS_STEP)) * BRIGHTNESS_STEP))
    if brightness_queue.full():
        try:
            brightness_queue.get_nowait()
        except queue.Empty:
            pass
    brightness_queue.put(snapped)

# ============================================================
# SETTERS
# ============================================================
def set_brightness(val, send_system=True):
    global current_brightness
    current_brightness = max(0, min(100, int(val)))
    if send_system:
        enqueue_brightness(current_brightness)
    update_tray_icon()
    update_popup_labels_and_sliders()

def set_night_light(val):
    global current_night_light
    current_night_light = max(0, min(100, int(val)))
    request_gamma_update(nl=current_night_light)
    update_popup_labels_and_sliders()

def set_contrast(val):
    global current_contrast
    current_contrast = max(0, min(100, int(val)))
    request_gamma_update(c=current_contrast)
    update_popup_labels_and_sliders()

def toggle_preset():
    global preset_active, saved_brightness, saved_night_light, saved_contrast, \
           current_brightness, current_night_light, current_contrast

    old_brightness = current_brightness

    if not preset_active:
        saved_brightness = current_brightness
        saved_night_light = current_night_light
        saved_contrast = current_contrast

        current_brightness = 80
        current_night_light = 10
        current_contrast = 50
        preset_active = True
    else:
        current_brightness = saved_brightness
        current_night_light = saved_night_light
        current_contrast = saved_contrast
        preset_active = False

    update_popup_labels_and_sliders()
    update_tray_icon()

    if current_brightness != old_brightness:
        enqueue_brightness(current_brightness)

    request_gamma_update(current_night_light, current_contrast)

# Cached font loader to avoid disk overhead
_cached_font = None
def get_cached_font():
    global _cached_font
    if _cached_font is not None:
        return _cached_font
    for font_name in ["arialbd.ttf", "segoeuib.ttf", "arial.ttf"]:
        try:
            _cached_font = ImageFont.truetype(font_name, 36)
            return _cached_font
        except Exception:
            continue
    _cached_font = ImageFont.load_default()
    return _cached_font

def create_text_icon(brightness_val):
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    dc = ImageDraw.Draw(image)

    # Draw only the top and bottom horizontal lines
    line_color = (255, 200, 50, 255)
    line_width = 3
    dc.line([(2, 4), (62, 4)], fill=line_color, width=line_width)
    dc.line([(2, 60), (62, 60)], fill=line_color, width=line_width)

    text = f"{int(brightness_val)}"
    if len(text) == 1:
        xy = (19, 10)
    elif len(text) == 2:
        xy = (8, 10)
    else:
        xy = (-1, 10)

    font = get_cached_font()
    dc.text(xy, text, fill=(255, 200, 50), font=font)
    return image

def draw_vector_sun(canvas, x, y):
    canvas.create_oval(x - 7, y - 7, x + 7, y + 7, fill="#ff9900", outline="")
    for i in range(8):
        angle = i * (360 / 8)
        rad = math.radians(angle)
        x1 = x + int(9 * math.cos(rad))
        y1 = y + int(9 * math.sin(rad))
        x2 = x + int(12 * math.cos(rad))
        y2 = y + int(12 * math.sin(rad))
        canvas.create_line(x1, y1, x2, y2, fill="#ff9900", width=2)

def draw_vector_moon(canvas, x, y):
    canvas.create_oval(x - 8, y - 8, x + 8, y + 8, fill="#4a90e2", outline="")
    canvas.create_oval(x - 4, y - 9, x + 10, y + 7, fill="#f5f5f5", outline="")

def draw_vector_contrast(canvas, x, y):
    canvas.create_oval(x - 8, y - 8, x + 8, y + 8, fill="#555555", outline="")
    canvas.create_arc(x - 8, y - 8, x + 8, y + 8, start=90, extent=180, fill="#dddddd", outline="")

def close_popup():
    global popup_window, brightness_slider_ref, night_slider_ref, contrast_slider_ref, brightness_label_ref, night_label_ref, contrast_label_ref, preset_btn_ref
    brightness_slider_ref = None
    night_slider_ref = None
    contrast_slider_ref = None
    brightness_label_ref = None
    night_label_ref = None
    contrast_label_ref = None
    preset_btn_ref = None
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

def show_about(icon=None, item=None):
    global about_window
    if about_window is not None:
        try:
            about_window.focus_force()
            return
        except Exception:
            about_window = None

    def run_about_thread():
        global about_window
        win = tk.Tk()
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
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(ctypes.c_int(2)), ctypes.sizeof(ctypes.c_int))
        except Exception:
            pass

        screen_width = win.winfo_screenwidth()
        screen_height = win.winfo_screenheight()
        win.geometry(f"+{screen_width // 2 - 130}+{screen_height // 2 - 140}")

        frame = tk.Frame(win, padx=12, pady=12, bg="#f5f5f5")
        win.config(bg="#f5f5f5")
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(frame, text=f"Eyedim {APP_VERSION}", font=("Segoe UI", 11, "bold"), bg="#f5f5f5").pack(anchor="w")
        tk.Label(frame, text=t("about_desc"), font=("Segoe UI", 8), fg="#555555", bg="#f5f5f5", justify="left", wraplength=230).pack(anchor="w", pady=(2, 6))

        tk.Label(frame, text=t("about_author"), font=("Segoe UI", 8), bg="#f5f5f5", justify="left").pack(anchor="w", pady=(0, 4))
        tk.Label(frame, text=t("about_license"), font=("Segoe UI", 7), fg="#777777", bg="#f5f5f5", justify="left").pack(anchor="w", pady=(0, 6))

        tk.Label(frame, text=t("about_support"), font=("Segoe UI", 8, "bold"), bg="#f5f5f5").pack(anchor="w", pady=(2, 2))

        try:
            qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=90x90&data={urllib.parse.quote(DONATE_URL)}"
            with urllib.request.urlopen(qr_url, timeout=2) as response:
                qr_data = response.read()
            qr_img_raw = Image.open(io.BytesIO(qr_data))

            from PIL import ImageTk
            qr_photo = ImageTk.PhotoImage(qr_img_raw)

            qr_lbl = tk.Label(frame, image=qr_photo, bg="#f5f5f5")
            qr_lbl.image = qr_photo
            qr_lbl.pack()
        except Exception:
            tk.Label(frame, text="[QR Code Loading Error]", font=("Segoe UI", 7), fg="red", bg="#f5f5f5").pack()

        def on_close():
            global about_window
            about_window = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        win.deiconify()
        win.mainloop()

    threading.Thread(target=run_about_thread, daemon=True).start()

class ThinSlider(tk.Canvas):
    def __init__(self, parent, from_=0, to=100, command=None, initial=0):
        super().__init__(parent, height=22, bg="#f5f5f5", highlightthickness=0)
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

        thumb_size = 12  # Round thumb diameter
        margin = thumb_size // 2 + 2

        line_y = h // 2
        inner_w = w - 2 * margin

        if self.to > self.from_ and inner_w > 0:
            pos = margin + (inner_w * (self._value - self.from_) / (self.to - self.from_))
        else:
            pos = margin

        # 1. Filled (passed) part of the line in amber
        if pos > margin:
            self.create_rectangle(margin, line_y - 1, pos, line_y + 1,
                                  fill="#ffb74d", outline="")

        # 2. Remaining (not passed) part of the line in gray
        if pos < w - margin:
            self.create_rectangle(pos, line_y - 1, w - margin, line_y + 1,
                                  fill="#cccccc", outline="")

        # 3. Round thumb
        self.create_oval(
            pos - thumb_size // 2, line_y - thumb_size // 2,
            pos + thumb_size // 2, line_y + thumb_size // 2,
            fill="#ffffff", outline="#888888", width=1
        )

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

def show_popup(icon=None, item=None):
    global popup_window, brightness_slider_ref, night_slider_ref, contrast_slider_ref, brightness_label_ref, night_label_ref, contrast_label_ref, preset_btn_ref
    if popup_window is not None:
        close_popup()
        return

    close_about()

    popup_window = tk.Tk()
    popup_window.title(t("window_title"))
    popup_window.geometry("220x140")
    popup_window.attributes("-topmost", True)
    popup_window.resizable(False, False)
    popup_window.overrideredirect(True)

    popup_window.update_idletasks()
    try:
        hwnd = ctypes.windll.user32.GetParent(popup_window.winfo_id())
        if not hwnd:
            hwnd = popup_window.winfo_id()
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(ctypes.c_int(2)), ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass

    screen_width = popup_window.winfo_screenwidth()
    screen_height = popup_window.winfo_screenheight()
    popup_window.geometry(f"+{screen_width - 240}+{screen_height - 200}")

    frame = tk.Frame(popup_window, padx=10, pady=10, bg="#f5f5f5")
    popup_window.config(bg="#f5f5f5")
    frame.pack(fill=tk.BOTH, expand=True)

    frame.columnconfigure(1, weight=1)

    # Brightness control row
    canvas_sun = tk.Canvas(frame, width=24, height=22, bg="#f5f5f5", highlightthickness=0)
    canvas_sun.grid(row=0, column=0, sticky="w", padx=(0, 2))
    draw_vector_sun(canvas_sun, 12, 11)

    brightness_slider_ref = ThinSlider(frame, from_=0, to=100, command=set_brightness, initial=current_brightness)
    brightness_slider_ref.grid(row=0, column=1, sticky="ew", padx=(2, 4))

    brightness_label_ref = tk.Label(frame, text=f"{current_brightness}", font=("Segoe UI Light", 10), bg="#f5f5f5", width=3, anchor="e")
    brightness_label_ref.grid(row=0, column=2, sticky="e")

    # Night light control row
    canvas_moon = tk.Canvas(frame, width=24, height=22, bg="#f5f5f5", highlightthickness=0)
    canvas_moon.grid(row=1, column=0, sticky="w", padx=(0, 2), pady=(4, 0))
    draw_vector_moon(canvas_moon, 12, 11)

    night_slider_ref = ThinSlider(frame, from_=0, to=100, command=set_night_light, initial=current_night_light)
    night_slider_ref.grid(row=1, column=1, sticky="ew", padx=(2, 4), pady=(4, 0))

    night_label_ref = tk.Label(frame, text=f"{current_night_light}", font=("Segoe UI Light", 10), bg="#f5f5f5", width=3, anchor="e")
    night_label_ref.grid(row=1, column=2, sticky="e", pady=(4, 0))

    # Contrast control row
    canvas_contrast = tk.Canvas(frame, width=24, height=22, bg="#f5f5f5", highlightthickness=0)
    canvas_contrast.grid(row=2, column=0, sticky="w", padx=(0, 2), pady=(4, 0))
    draw_vector_contrast(canvas_contrast, 12, 11)

    contrast_slider_ref = ThinSlider(frame, from_=0, to=100, command=set_contrast, initial=current_contrast)
    contrast_slider_ref.grid(row=2, column=1, sticky="ew", padx=(2, 4), pady=(4, 0))

    contrast_label_ref = tk.Label(frame, text=f"{current_contrast}", font=("Segoe UI Light", 10), bg="#f5f5f5", width=3, anchor="e")
    contrast_label_ref.grid(row=2, column=2, sticky="e", pady=(4, 0))

    # Mode toggle button
    btn_canvas = tk.Canvas(frame, height=26, bg="#f5f5f5", highlightthickness=0, cursor="hand2")
    btn_canvas.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))

    def current_bg():
        return "#ffe0b2" if preset_active else "#f0f0f0"

    def current_hover_bg():
        return "#ffd54f" if preset_active else "#e0e0e0"

    def draw_rounded_btn(bg_col):
        btn_canvas.delete("all")
        w = btn_canvas.winfo_width()
        h = btn_canvas.winfo_height()
        if w <= 1:
            w = 200

        radius = 6

        btn_canvas.create_arc(0, 0, radius * 2, radius * 2, start=90, extent=90, fill=bg_col, outline="")
        btn_canvas.create_arc(w - radius * 2, 0, w, radius * 2, start=0, extent=90, fill=bg_col, outline="")
        btn_canvas.create_arc(0, h - radius * 2, radius * 2, h, start=180, extent=90, fill=bg_col, outline="")
        btn_canvas.create_arc(w - radius * 2, h - radius * 2, w, h, start=270, extent=90, fill=bg_col, outline="")

        btn_canvas.create_rectangle(radius, 0, w - radius, h, fill=bg_col, outline="")
        btn_canvas.create_rectangle(0, radius, w, h - radius, fill=bg_col, outline="")

        label = t("mode_default") if preset_active else t("mode_user")
        btn_canvas.create_text(w / 2, h / 2, text=label, fill="#555555", font=("Segoe UI", 9, "bold"))

    btn_canvas.bind("<Configure>", lambda e: draw_rounded_btn(current_bg()))
    btn_canvas.bind("<Enter>", lambda e: draw_rounded_btn(current_hover_bg()))
    btn_canvas.bind("<Leave>", lambda e: draw_rounded_btn(current_bg()))

    def on_btn_click(e):
        toggle_preset()
        draw_rounded_btn(current_bg())

    btn_canvas.bind("<Button-1>", on_btn_click)

    popup_window.bind("<FocusOut>", lambda e: close_popup())
    popup_window.focus_force()
    popup_window.mainloop()

def quit_app(icon, item):
    global current_night_light, current_contrast
    current_night_light = 0
    current_contrast = 50
    request_gamma_update(current_night_light, current_contrast)
    
    # Properly destroy open UI windows and perform immediate exit
    close_popup()
    close_about()
    icon.stop()
    os._exit(0)

tray_icon_ref = pystray.Icon(
    "Eyedim",
    create_text_icon(current_brightness),
    f"Eyedim {APP_VERSION} — Brightness: {current_brightness}%",
    pystray.Menu(
        pystray.MenuItem(lambda item: t("menu_settings"), show_popup, default=True),
        pystray.MenuItem(lambda item: t("menu_about"), show_about),
        pystray.MenuItem(lambda item: t("menu_autostart"), toggle_autostart, checked=lambda item: is_autostart_enabled()),
        pystray.MenuItem(lambda item: t("menu_lang"), toggle_language),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda item: t("menu_exit"), quit_app)
    )
)

tray_icon_ref.default_action = show_popup
tray_icon_ref.run()