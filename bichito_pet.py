"""El bichito: ventana flotante que muestra si Claude Code esta trabajando.

Lee state/*.json (uno por sesion, los escribe bichito-hook.exe) y anima el
sprite que corresponde. Se dibuja con UpdateLayeredWindow para tener alfa real
por pixel: con la transparencia normal de tkinter (-transparentcolor) los bordes
suavizados del sprite quedarian con un halo del color de fondo de la ventana.

  arrastrar con el boton izquierdo  -> mover (la posicion se guarda)
  boton derecho                     -> menu (siempre encima / cerrar)
"""
import ctypes
import json
import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes

from PIL import Image, ImageChops, ImageDraw, ImageFont

import bichito_core as core

LOCK_PORT = 50519      # instancia unica: si el puerto esta tomado, ya hay uno
POLL_MS = 250          # cada cuanto se relee state/ y la config
WORKING_TIMEOUT = 900  # s sin latido -> se asume que la sesion murio (Esc, cierre)
STALE = 86400          # s -> archivo de sesion viejo, se borra
CELEBRATE = 2.4        # s de festejo antes de dormirse

TEXTO = {"cocinando": "Cocinando", "esperando": "Te espera",
         "termino": "Listo", "dormido": ""}

# ------------------------------------------------------------------- win32
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

ULW_ALPHA = 0x02
AC_SRC_OVER, AC_SRC_ALPHA = 0x00, 0x01
WS_EX_LAYERED, WS_EX_TOOLWINDOW = 0x00080000, 0x00000080
GWL_EXSTYLE = -20
LONG_PTR = ctypes.c_ssize_t


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
_get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
_set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
_get_long.argtypes = [wintypes.HWND, ctypes.c_int]
_get_long.restype = LONG_PTR
_set_long.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
_set_long.restype = LONG_PTR
user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
    ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
    wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                   wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ


def toplevel_hwnd(widget):
    h = widget.winfo_id()
    while True:
        p = user32.GetParent(h)
        if not p:
            return h
        h = p


def premultiply(img):
    """UpdateLayeredWindow con AC_SRC_ALPHA exige alfa premultiplicado.

    PIL entrega alfa directo; sin premultiplicar aparece un borde negro.
    """
    r, g, b, a = img.split()
    return Image.merge("RGBA", (ImageChops.multiply(r, a), ImageChops.multiply(g, a),
                                ImageChops.multiply(b, a), a))


class Layered:
    """Superficie RGBA de tamano fijo dibujada directamente por Windows."""

    def __init__(self, root, w, h):
        self.w, self.h = w, h
        self.hwnd = toplevel_hwnd(root)
        # TOOLWINDOW la saca del alt-tab. Nada de NOACTIVATE: una ventana que no
        # se puede activar rompe el menu de boton derecho, que es la unica
        # manera de cerrar el bichito.
        style = _get_long(self.hwnd, GWL_EXSTYLE)
        _set_long(self.hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TOOLWINDOW)

        self.screen_dc = user32.GetDC(None)
        self.mem_dc = gdi32.CreateCompatibleDC(self.screen_dc)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # top-down, igual orden que PIL
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        self.bits = ctypes.c_void_p()
        self.hbmp = gdi32.CreateDIBSection(self.screen_dc, ctypes.byref(bmi), 0,
                                           ctypes.byref(self.bits), None, 0)
        if not self.hbmp:
            raise OSError(f"CreateDIBSection: {ctypes.get_last_error()}")
        gdi32.SelectObject(self.mem_dc, self.hbmp)

    def blit(self, img, x, y):
        raw = img.tobytes("raw", "BGRA")
        ctypes.memmove(self.bits, raw, len(raw))
        pt_dst = wintypes.POINT(int(x), int(y))
        size = wintypes.SIZE(self.w, self.h)
        pt_src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(self.hwnd, self.screen_dc, ctypes.byref(pt_dst),
                                   ctypes.byref(size), self.mem_dc, ctypes.byref(pt_src),
                                   0, ctypes.byref(blend), ULW_ALPHA)


# -------------------------------------------------------------------- estado
def read_state():
    """Junta los archivos de todas las sesiones.

    Prioridad esperando > cocinando > dormido: si hay dos Claude abiertos, que
    uno termine no tiene que apagar al otro.
    """
    best, since, timed_out = "idle", None, False
    now = time.time()
    sdir = core.state_dir()
    for name in os.listdir(sdir):
        if not name.endswith(".json"):
            continue
        path = os.path.join(sdir, name)
        try:
            with open(path, encoding="utf-8-sig") as fh:
                data = json.load(fh)
            age = now - float(data.get("ts", 0))
        except (OSError, ValueError):
            continue  # escritura a medio terminar; se reintenta en el proximo poll
        if age > STALE:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        st = data.get("state", "idle")
        # sin latido por mucho rato: la sesion murio sin disparar Stop
        if st in ("working", "waiting") and age > WORKING_TIMEOUT:
            timed_out = True
            continue
        if st == "waiting" and best != "waiting":
            best, since = "waiting", data.get("since")
        elif st == "working" and best == "idle":
            best, since = "working", data.get("since")
    return best, since, timed_out


def fmt(seconds):
    s = int(max(0, seconds))
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


# ---------------------------------------------------------------------- app
class Bichito:
    def __init__(self):
        with open(core.resource_path("assets", "manifest.json"), encoding="utf-8") as fh:
            self.manifest = json.load(fh)
        self.w, self.h = self.manifest["size"]
        self.text_y = self.manifest["text_y"]
        self.frames = {
            name: [Image.open(core.resource_path("assets", name, f"{i:02d}.png")).convert("RGBA")
                   for i in range(info["frames"])]
            for name, info in self.manifest["states"].items()
        }
        try:
            self.font = ImageFont.truetype("segoeuib.ttf", 9)
        except OSError:
            self.font = ImageFont.load_default()

        self.cfg = core.load_config()
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.topmost = bool(self.cfg["always_on_top"])
        self.root.wm_attributes("-topmost", self.topmost)
        # home = donde vive; x,y = donde esta dibujado ahora. Se separan porque
        # mientras espera se va al centro y despues tiene que volver.
        self.home = self.load_pos()
        self.x, self.y = self.home
        self.fx, self.fy = float(self.x), float(self.y)
        self.center = ((self.root.winfo_screenwidth() - self.w) // 2,
                       (self.root.winfo_screenheight() - self.h) // 2)
        self.root.geometry(f"{self.w}x{self.h}+{self.x}+{self.y}")
        self.root.update_idletasks()
        self.layer = Layered(self.root, self.w, self.h)

        self.visible = True
        self.visual = "dormido"
        self.raw = "idle"
        self.since = None
        self.frame_i = 0
        self.celebrate_until = 0.0
        self.frozen = ""
        self._cache = (None, None)
        self._drag = None

        self.root.bind("<Button-1>", self.drag_start)
        self.root.bind("<B1-Motion>", self.drag_move)
        self.root.bind("<ButtonRelease-1>", lambda e: self.save_pos())
        self.root.bind("<Button-3>", self.popup)
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Abrir panel", command=lambda: self.open_panel())
        self.menu.add_command(label="Siempre encima", command=self.toggle_top)
        self.menu.add_separator()
        # esconde la ventanita pero deja vivo el icono de la bandeja, que es por
        # donde se vuelve a mostrar
        self.menu.add_command(label="Ocultar bichito", command=self.toggle_pet)
        self.menu.add_command(label="Salir del todo", command=self.quit_all)

        self.tray = Tray(self)
        self.tray.start()

        self.poll()
        self.animate()
        self.slide()

    # --- acciones de la bandeja (siempre llegan ya marshaladas al hilo tk) ---
    def open_panel(self):
        exe = os.path.join(os.path.dirname(sys.executable), "Bichito.exe")
        try:
            if getattr(sys, "frozen", False) and os.path.exists(exe):
                subprocess.Popen([exe], creationflags=0x00000008)
            else:
                subprocess.Popen([sys.executable,
                                  os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                               "bichito_app.py")])
        except OSError:
            pass

    def toggle_pet(self):
        cfg = core.load_config()
        core.save_config({**cfg, "pet": not cfg["pet"]})  # poll() lo aplica solo

    def quit_all(self):
        self.tray.stop()
        self.root.destroy()

    # --- posicion ---
    def load_pos(self):
        try:
            with open(core.data_path("pet_pos.json"), encoding="utf-8-sig") as fh:
                c = json.load(fh)
            return int(c["x"]), int(c["y"])
        except (OSError, ValueError, KeyError):
            return self.root.winfo_screenwidth() - self.w - 40, 120

    def save_pos(self):
        try:
            with open(core.data_path("pet_pos.json"), "w", encoding="utf-8") as fh:
                json.dump({"x": self.home[0], "y": self.home[1]}, fh)
        except OSError:
            pass

    def glide(self):
        """Acerca la posicion dibujada al destino. Mientras espera el destino es
        el centro de la pantalla; el resto del tiempo, su lugar de siempre."""
        tx, ty = self.center if (self.visual == "esperando"
                                 and self.cfg["center_on_wait"]) else self.home
        if abs(tx - self.fx) < 1 and abs(ty - self.fy) < 1:
            self.fx, self.fy = float(tx), float(ty)
        else:
            self.fx += (tx - self.fx) * 0.22
            self.fy += (ty - self.fy) * 0.22
        self.x, self.y = round(self.fx), round(self.fy)

    def drag_start(self, e):
        self._drag = (e.x_root - self.x, e.y_root - self.y)

    def drag_move(self, e):
        if self._drag:
            self.x = e.x_root - self._drag[0]
            self.y = e.y_root - self._drag[1]
            self.fx, self.fy = float(self.x), float(self.y)
            self.home = (self.x, self.y)   # arrastrarlo redefine su lugar
            # solo cambio la posicion: se reusa el bitmap ya premultiplicado en
            # vez de recomponerlo en cada evento de movimiento
            if self._cache[1] is not None:
                self.layer.blit(self._cache[1], self.x, self.y)
            else:
                self.render()

    def popup(self, e):
        self.menu.entryconfigure(0, label="Siempre encima  " + ("si" if self.topmost else "no"))
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()  # si no, el menu se puede comer el mouse

    def toggle_top(self):
        self.topmost = not self.topmost
        self.root.wm_attributes("-topmost", self.topmost)
        self.cfg = core.save_config({**self.cfg, "always_on_top": self.topmost})

    # --- maquina de estados ---
    def poll(self):
        # la config se relee en cada vuelta: asi los interruptores del panel
        # tienen efecto al instante, sin reiniciar el bichito
        cfg = core.load_config()
        # Se esconde, NO se destruye: este proceso tambien sostiene el icono de
        # la bandeja, que tiene que seguir estando con la ventanita apagada.
        visible = bool(cfg["enabled"] and cfg["pet"])
        if visible != self.visible:
            self.visible = visible
            if visible:
                self.root.deiconify()
                self.render(force=True)
            else:
                self.root.withdraw()
        if cfg["always_on_top"] != self.topmost:
            self.topmost = bool(cfg["always_on_top"])
            self.root.wm_attributes("-topmost", self.topmost)
        self.cfg = cfg

        raw, since, timed_out = read_state()
        if raw != self.raw:
            # se festeja solo si termino de verdad (hook Stop). Si la sesion se
            # cayo y la descarto el timeout, el tiempo seria inventado
            if self.raw in ("working", "waiting") and raw == "idle" and not timed_out:
                self.celebrate_until = time.time() + CELEBRATE
                self.frozen = fmt(time.time() - self.since) if self.since else ""
                self.set_visual("termino")
            self.raw = raw
        self.since = since

        if time.time() >= self.celebrate_until:
            self.set_visual({"working": "cocinando", "waiting": "esperando"}.get(raw, "dormido"))
        self.root.after(POLL_MS, self.poll)

    def set_visual(self, name):
        if name != self.visual:
            self.visual = name
            self.frame_i = 0

    # --- dibujo ---
    def label(self):
        if self.visual == "termino":
            return f"Listo  {self.frozen}".strip() if self.cfg["timer"] else "Listo"
        base = TEXTO.get(self.visual, "")
        if base and self.since and self.cfg["timer"]:
            return f"{base}  {fmt(time.time() - self.since)}"
        return base

    def compose(self, txt):
        img = self.frames[self.visual][self.frame_i].copy()
        if txt:
            # el texto se dibuja a mitad de escala y se agranda con NEAREST:
            # queda pixelado a juego con el sprite
            hw, hh = self.w // 2, (self.h - self.text_y) // 2
            tmp = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
            d = ImageDraw.Draw(tmp)
            bb = d.textbbox((0, 0), txt, font=self.font)
            tx = (hw - (bb[2] - bb[0])) // 2 - bb[0]
            ty = (hh - (bb[3] - bb[1])) // 2 - bb[1]
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx or dy:
                        d.text((tx + dx, ty + dy), txt, font=self.font, fill=(18, 12, 8, 240))
            d.text((tx, ty), txt, font=self.font, fill=(255, 246, 236, 255))
            img.alpha_composite(tmp.resize((hw * 2, hh * 2), Image.NEAREST), (0, self.text_y))
        return premultiply(img)

    def render(self, force=False):
        txt = self.label()
        key = (self.visual, self.frame_i, txt)
        if force or key != self._cache[0]:
            self._cache = (key, self.compose(txt))
        if self.visible:
            self.layer.blit(self._cache[1], self.x, self.y)

    def animate(self):
        info = self.manifest["states"][self.visual]
        if self.visual == "termino":
            self.frame_i = min(info["frames"] - 1, self.frame_i + 1)  # una sola pasada
        else:
            self.frame_i = (self.frame_i + 1) % info["frames"]
        self.render()
        self.root.after(int(1000 / info["fps"]), self.animate)

    def slide(self):
        """El desplazamiento va en su propio loop, mas rapido que la animacion:
        a 3-4 fps el viaje al centro se veria a los saltos."""
        before = (self.x, self.y)
        self.glide()
        if self.visible and (self.x, self.y) != before and self._cache[1] is not None:
            self.layer.blit(self._cache[1], self.x, self.y)
        self.root.after(16, self.slide)

    def run(self):
        self.root.mainloop()


class Tray:
    """Icono en la bandeja del sistema.

    pystray corre su propio loop en un hilo aparte, asi que NINGUN callback
    puede tocar tkinter directo: hay que reenviarlos al hilo principal con
    root.after(0, ...). Hacerlo desde el hilo de pystray cuelga o revienta.
    """

    def __init__(self, app):
        self.app = app
        self.icon = None

    def start(self):
        try:
            import pystray
        except ImportError:
            return
        img = Image.open(core.resource_path("assets", "esperando", "00.png")).convert("RGBA")
        bb = img.split()[3].point(lambda v: 255 if v > 16 else 0).getbbox()
        img = img.crop(bb)
        side = max(img.size)
        sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        sq.alpha_composite(img, ((side - img.width) // 2, (side - img.height) // 2))
        # NEAREST: es pixel art, un filtro suave lo emborrona
        sq = sq.resize((64, 64), Image.NEAREST)

        def later(fn):
            return lambda *_: self.app.root.after(0, fn)

        self.icon = pystray.Icon(
            "bichito", sq, "Bichito",
            menu=pystray.Menu(
                pystray.MenuItem("Abrir panel", later(self.app.open_panel), default=True),
                pystray.MenuItem("Mostrar bichito", later(self.app.toggle_pet),
                                 checked=lambda _: bool(core.load_config()["pet"])),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Salir", later(self.app.quit_all)),
            ),
        )
        threading.Thread(target=self.icon.run, daemon=True).start()

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass


def run():
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))  # se libera solo si el proceso muere
        lock.listen(1)
    except OSError:
        return  # ya hay un bichito dando vueltas
    Bichito().run()
