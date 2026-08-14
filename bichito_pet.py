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
import bichito_usage

LOCK_PORT = 50519      # instancia unica: si el puerto esta tomado, ya hay uno
POLL_MS = 250          # cada cuanto se relee state/ y la config
WORKING_TIMEOUT = 900  # s sin latido -> se asume que la sesion murio (Esc, cierre)
STALE = 86400          # s -> archivo de sesion viejo, se borra
CELEBRATE = 2.4        # s de festejo antes de dormirse
BANDA_USO = 24         # px de ventana extra debajo del sprite, para el % del plan
ARRASTRE_MIN = 4       # px que hay que moverse para que cuente como arrastre

TEXTO = {"cocinando": "Cocinando", "esperando": "Te espera",
         "termino": "Listo", "dormido": ""}

# ------------------------------------------------------------------- win32
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

ULW_ALPHA = 0x02
AC_SRC_OVER, AC_SRC_ALPHA = 0x00, 0x01
WS_EX_LAYERED, WS_EX_TOOLWINDOW = 0x00080000, 0x00000080
GWL_EXSTYLE = -20
GW_OWNER = 4
SW_RESTORE = 9
LONG_PTR = ctypes.c_ssize_t
# el escritorio y la barra de tareas son ventanas de explorer.exe: si la cadena
# de procesos llegara hasta ahi, traerlas al frente no seria traer nada
CLASES_ESCRITORIO = {"Progman", "Shell_TrayWnd", "WorkerW", "Button"}


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
# los restype van declarados si o si: por defecto ctypes asume int de 32 bits y
# en 64 bits un HWND devuelto asi vuelve truncado
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def ventana_principal(pid):
    """HWND de la ventana principal del proceso, o None.

    Principal = visible, sin dueno (los dialogos y los tooltips tienen dueno) y
    con titulo. Claude Code y la shell no tienen ventana propia cuando corren
    adentro de Windows Terminal o de VS Code, asi que en esa cadena la primera
    que aparece es justo la que hay que traer al frente.
    """
    hallada = []

    def visitar(hwnd, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, GW_OWNER) or user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value in CLASES_ESCRITORIO:
            return True
        hallada.append(hwnd)
        return False   # False corta la enumeracion

    user32.EnumWindows(ENUM_PROC(visitar), 0)
    return hallada[0] if hallada else None


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

    Devuelve tambien los PID de la sesion que gano, para que el click sepa a que
    ventana llevarte: con dos Claude abiertos, te lleva al que te esta esperando.
    """
    best, since, timed_out, focus = "idle", None, False, []
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
        pids = [p for p in (data.get("focus") or []) if isinstance(p, int)]
        if st == "waiting" and best != "waiting":
            best, since, focus = "waiting", data.get("since"), pids
        elif st == "working" and best == "idle":
            best, since, focus = "working", data.get("since"), pids
    return best, since, timed_out, focus


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
        # La ventana es mas alta que el sprite: el % del plan va en una franja
        # propia debajo. En la banda de texto del sprite no entran dos renglones
        # (son 28px y el label ya los usa), asi que compartirla dejaba el uso
        # dibujado ENCIMA del "Cocinando 29m 07s" y no se leia ninguno de los dos.
        # Con los toggles apagados la franja queda transparente, y una zona
        # transparente de una layered window no se come los clicks.
        self.wh = self.h + BANDA_USO
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
                       (self.root.winfo_screenheight() - self.wh) // 2)
        self.root.geometry(f"{self.w}x{self.wh}+{self.x}+{self.y}")
        self.root.update_idletasks()
        self.layer = Layered(self.root, self.w, self.wh)

        self.visible = True
        self.visual = "dormido"
        self.raw = "idle"
        self.since = None
        self.frame_i = 0
        self.celebrate_until = 0.0
        self.frozen = ""
        self.usage = None
        self._cache = (None, None)
        self._drag = None
        self._press = (0, 0)
        self._moved = False
        # lo arrastraste durante esta espera: el centro deja de tironear hasta
        # que deje de esperar
        self.pinned = False
        self.focus_pids = []   # cadena de procesos hasta la terminal de la sesion

        self.root.bind("<Button-1>", self.drag_start)
        self.root.bind("<B1-Motion>", self.drag_move)
        self.root.bind("<ButtonRelease-1>", self.drag_end)
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

        # poller de usage: mantiene state/usage.json fresco para que esta
        # ventanita sola (sin el panel) pueda mostrar el %. Cuando tambien esta
        # abierto el panel, hay dos poller corriendo pero el state file es el
        # mismo y el cache evita duplicar fetches utiles.
        self.poller = bichito_usage.Poller()
        self.poller.start()

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
        self.poller.stop()
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
        el centro de la pantalla; el resto del tiempo, su lugar de siempre.

        Si lo agarraste con el mouse manda tu mano: sin eso el centro lo vuelve a
        chupar en el proximo tick (16ms) y la ventanita queda inmovible justo
        cuando mas estorba, en el medio de la pantalla.
        """
        al_centro = (self.visual == "esperando" and self.cfg["center_on_wait"]
                     and not self.pinned)
        tx, ty = self.center if al_centro else self.home
        if abs(tx - self.fx) < 1 and abs(ty - self.fy) < 1:
            self.fx, self.fy = float(tx), float(ty)
        else:
            self.fx += (tx - self.fx) * 0.22
            self.fy += (ty - self.fy) * 0.22
        self.x, self.y = round(self.fx), round(self.fy)

    def drag_start(self, e):
        self._drag = (e.x_root - self.x, e.y_root - self.y)
        self._press = (e.x_root, e.y_root)
        self._moved = False

    def drag_move(self, e):
        if not self._drag:
            return
        # umbral: ningun click sale perfectamente quieto, y sin esto el temblor
        # de la mano contaria como arrastre y se comeria el click
        if not self._moved:
            if (abs(e.x_root - self._press[0]) < ARRASTRE_MIN
                    and abs(e.y_root - self._press[1]) < ARRASTRE_MIN):
                return
            self._moved = True
        self.x = e.x_root - self._drag[0]
        self.y = e.y_root - self._drag[1]
        self.fx, self.fy = float(self.x), float(self.y)
        self.home = (self.x, self.y)   # arrastrarlo redefine su lugar
        # arrastrar gana: mientras dure esta espera, el centro deja de tironear
        self.pinned = True
        # solo cambio la posicion: se reusa el bitmap ya premultiplicado en vez
        # de recomponerlo en cada evento de movimiento
        if self._cache[1] is not None:
            self.layer.blit(self._cache[1], self.x, self.y)
        else:
            self.render()

    def drag_end(self, e):
        """Soltar el boton: si lo moviste, se guarda donde quedo; si fue un
        click limpio, te lleva a donde Claude te esta esperando."""
        self._drag = None
        if self._moved:
            self.save_pos()
        else:
            self.focus_session()

    def focus_session(self):
        """Trae al frente la ventana de la sesion que manda el estado.

        Los PID los escribe el hook (la cadena de procesos hasta la terminal).
        Los primeros suelen no tener ventana -o ya ni existir, como los procesos
        cortitos que lanza cada herramienta-, asi que se prueba en orden y gana
        el primero que tenga una.

        Windows solo deja cambiar el primer plano al proceso que ya lo tiene, y
        el click sobre el bichito nos lo acaba de dar: por eso no hace falta
        ninguno de los trucos con AttachThreadInput.
        """
        for pid in self.focus_pids:
            hwnd = ventana_principal(pid)
            if not hwnd:
                continue
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            return True
        return False

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

        # el poller ya actualiza state/usage.json; lo leemos aca porque ya
        # estamos en el loop que re-renderiza cada 250ms y el read es barato
        self.usage = bichito_usage.read()

        raw, since, timed_out, focus = read_state()
        # la ultima cadena conocida se conserva: asi el click sigue llevandote a
        # la terminal aunque la sesion ya haya pasado a dormida
        if focus:
            self.focus_pids = focus
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
            if name != "esperando":
                self.pinned = False   # la proxima espera vuelve a ir al centro
            self.visual = name
            self.frame_i = 0

    # --- dibujo ---
    def label(self):
        """Devuelve (texto_principal, texto_de_usage).

        El principal se dibuja en el area grande de abajo (como hoy). El de
        usage, solo si los toggles estan prendidos, arriba en una linea mas
        chica. Si el principal queda vacio (dormido) el usage se centra
        verticalmente para no quedar raro arriba solo.
        """
        if self.visual == "termino":
            main = f"Listo  {self.frozen}".strip() if self.cfg["timer"] else "Listo"
        else:
            base = TEXTO.get(self.visual, "")
            if base and self.since and self.cfg["timer"]:
                main = f"{base}  {fmt(time.time() - self.since)}"
            else:
                main = base
        return main, self._usage_text()

    def _usage_text(self):
        cfg = self.cfg
        if not (cfg.get("plan_5h") or cfg.get("plan_weekly")):
            return ""
        u = self.usage
        if not u or not u.get("ok"):
            return ""          # sin dato fresco, no se muestra: prefiere callar
        parts = []
        if cfg.get("plan_5h"):
            sec = u.get("five_hour") or {}
            p = sec.get("pct")
            if isinstance(p, (int, float)):
                parts.append(f"5h {int(p)}%")
        if cfg.get("plan_weekly"):
            sec = u.get("seven_day") or {}
            p = sec.get("pct")
            if isinstance(p, (int, float)):
                parts.append(f"sem {int(p)}%")
        return "  ".join(parts)

    def _draw_text(self, img, txt, font, hh, top, fill=(255, 246, 236, 255)):
        """Dibuja una linea de texto centrada en la franja que arranca en `top`
        y mide `hh` de alto (a mitad de escala; se redimensiona 2x con NEAREST,
        que es lo que le conserva el aire de pixel art).

        `top` es un parametro y no una constante porque antes todas las lineas
        se pegaban en text_y: dibujar dos era dibujarlas una arriba de la otra.
        """
        hw = self.w // 2
        tmp = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
        d = ImageDraw.Draw(tmp)
        bb = d.textbbox((0, 0), txt, font=font)
        tx = (hw - (bb[2] - bb[0])) // 2 - bb[0]
        ty = (hh - (bb[3] - bb[1])) // 2 - bb[1]
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    d.text((tx + dx, ty + dy), txt, font=font, fill=(18, 12, 8, 240))
        d.text((tx, ty), txt, font=font, fill=fill)
        img.alpha_composite(tmp.resize((hw * 2, hh * 2), Image.NEAREST),
                            (0, top))

    def compose(self, txt, usage_txt):
        """Sprite arriba, label en la banda del sprite, % del plan en la franja
        de abajo. Cada uno en su renglon: no se estorban ni compiten por lugar,
        y el label principal queda exactamente donde estaba siempre."""
        img = Image.new("RGBA", (self.w, self.wh), (0, 0, 0, 0))
        img.alpha_composite(self.frames[self.visual][self.frame_i], (0, 0))
        if txt:
            self._draw_text(img, txt, self.font,
                            (self.h - self.text_y) // 2, self.text_y)
        if usage_txt:
            self._draw_text(img, usage_txt, self.font, BANDA_USO // 2, self.h,
                            fill=(217, 170, 120, 255))   # tono del bichito
        return premultiply(img)

    def render(self, force=False):
        txt, usage_txt = self.label()
        key = (self.visual, self.frame_i, txt, usage_txt)
        if force or key != self._cache[0]:
            self._cache = (key, self.compose(txt, usage_txt))
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
