"""El bichito: la ventana flotante que muestra que esta haciendo Claude Code.

Hay UNO POR SESION ABIERTA, en fila y con el nombre del proyecto abajo. Con seis
Claude a la vez, un solo bichito te decia que "algo" estaba esperando pero no
cual: la fila resuelve eso, y clickeando uno vas a la terminal de esa sesion.

Lee state/*.json (uno por sesion, los escribe bichito-hook.exe) y anima el sprite
que corresponde. Se dibuja con UpdateLayeredWindow para tener alfa real por
pixel: con la transparencia normal de tkinter (-transparentcolor) los bordes
suavizados del sprite quedarian con un halo del color de fondo de la ventana.

  arrastrar con el boton izquierdo  -> mover (la posicion se guarda)
  click sobre uno                   -> traer al frente esa terminal
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
ANIM_MS = 50           # tick de animacion; cada bichito avanza a SU propio fps
WORKING_TIMEOUT = 900  # s sin latido -> se asume que la sesion murio (Esc, cierre)
DORMIDO_TIMEOUT = 7200  # s dormida sin novedades -> se la da por cerrada
STALE = 86400          # s -> archivo de sesion viejo, se borra
CELEBRATE = 2.4        # s de festejo antes de dormirse
BANDA_USO = 22         # px de la franja del % del plan, debajo de la fila
ARRASTRE_MIN = 4       # px que hay que moverse para que cuente como arrastre
SEPARACION = 8         # px entre un bichito y el siguiente

TEXTO = {"cocinando": "Cocinando", "esperando": "Te espera",
         "termino": "Listo", "dormido": ""}

CREMA = (255, 246, 236, 255)
ARCILLA = (233, 150, 110, 255)   # el que te espera se distingue por color
TOSTADO = (217, 170, 120, 255)   # el % del plan
BORDE = (18, 12, 8, 235)

# ------------------------------------------------------------------- win32
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

ULW_ALPHA = 0x02
AC_SRC_OVER, AC_SRC_ALPHA = 0x00, 0x01
WS_EX_LAYERED, WS_EX_TOOLWINDOW = 0x00080000, 0x00000080
GWL_EXSTYLE = -20
GW_OWNER = 4
SW_RESTORE = 9
# escritorio virtual: con dos monitores, el de la izquierda tiene x negativa
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
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
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
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


def atenuar(img, factor):
    """Baja el alfa: los bichitos que no te estan esperando quedan de fondo."""
    r, g, b, a = img.split()
    return Image.merge("RGBA", (r, g, b, a.point(lambda v: int(v * factor))))


class Layered:
    """Superficie RGBA dibujada directamente por Windows.

    Se puede redimensionar porque la ventana crece y se achica con la cantidad
    de sesiones abiertas.
    """

    def __init__(self, root, w, h):
        self.hwnd = toplevel_hwnd(root)
        # TOOLWINDOW la saca del alt-tab. Nada de NOACTIVATE: una ventana que no
        # se puede activar rompe el menu de boton derecho, que es la unica
        # manera de cerrar el bichito.
        style = _get_long(self.hwnd, GWL_EXSTYLE)
        _set_long(self.hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TOOLWINDOW)
        self.screen_dc = user32.GetDC(None)
        self.mem_dc = None
        self.hbmp = None
        self.w = self.h = 0
        self.resize(w, h)

    def resize(self, w, h):
        if (w, h) == (self.w, self.h):
            return
        self._soltar()
        self.w, self.h = w, h
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

    def _soltar(self):
        """Suelta el bitmap y el DC viejos: sin esto, cada vez que se abre o se
        cierra un Claude quedaria colgado un objeto GDI."""
        if self.hbmp:
            gdi32.DeleteObject(self.hbmp)
            self.hbmp = None
        if self.mem_dc:
            gdi32.DeleteDC(self.mem_dc)
            self.mem_dc = None

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
    """Una entrada por sesion abierta, en orden estable (proyecto y despues id).

    Que el archivo exista significa que esa sesion sigue abierta: el hook lo
    borra en SessionEnd. Lo que se cayo sin avisar se descarta por antiguedad, y
    eso vuelve como `timed_out` para no festejar un final inventado.
    """
    sesiones, timed_out = [], False
    ahora = time.time()
    sdir = core.state_dir()
    for name in os.listdir(sdir):
        if not name.endswith(".json") or name == "usage.json":
            continue
        path = os.path.join(sdir, name)
        try:
            with open(path, encoding="utf-8-sig") as fh:
                data = json.load(fh)
            edad = ahora - float(data.get("ts", 0))
        except (OSError, ValueError):
            continue  # escritura a medio terminar; se reintenta en el proximo poll
        if edad > STALE:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        estado = data.get("state", "idle")
        # sin latido por mucho rato: la sesion murio sin disparar Stop
        if estado in ("working", "waiting") and edad > WORKING_TIMEOUT:
            timed_out = True
            continue
        # dormida hace horas y sin SessionEnd: se la da por cerrada
        if estado == "idle" and edad > DORMIDO_TIMEOUT:
            continue
        sesiones.append({
            "sid": name[:-5],
            "estado": estado,
            "since": data.get("since"),
            "focus": [p for p in (data.get("focus") or []) if isinstance(p, int)],
            "proyecto": (data.get("proyecto") or "").strip(),
        })
    sesiones.sort(key=lambda s: (s["proyecto"].lower(), s["sid"]))
    return sesiones, timed_out


def fmt(seconds):
    s = int(max(0, seconds))
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


# ---------------------------------------------------------------------- app
class Bichito:
    def __init__(self):
        with open(core.resource_path("assets", "manifest.json"), encoding="utf-8") as fh:
            self.manifest = json.load(fh)
        self.w, self.h = self.manifest["size"]
        self.text_y = self.manifest["text_y"]      # donde termina el arte
        self.frames = {
            name: [Image.open(core.resource_path("assets", name, f"{i:02d}.png")).convert("RGBA")
                   for i in range(info["frames"])]
            for name, info in self.manifest["states"].items()
        }
        self._fuentes = {}

        self.cfg = core.load_config()
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.topmost = bool(self.cfg["always_on_top"])
        self.root.wm_attributes("-topmost", self.topmost)

        self.usage = None       # el % del plan; lo refresca poll()
        self.sesiones = []      # lo ultimo que se leyo de state/
        self.slots = {}         # sid -> como se esta viendo ese bichito
        self.escala = 1.0
        self.ancho_slot = self.w
        self.ww, self.wh = self.w, self.text_y + 40   # se recalcula en medir()
        self.medir()

        # home = donde vive; x,y = donde esta dibujado ahora. Se separan porque
        # mientras espera se va al centro y despues tiene que volver.
        self.home = self.load_pos()
        self.x, self.y = self.home
        self.fx, self.fy = float(self.x), float(self.y)
        self.root.geometry(f"{self.ww}x{self.wh}+{self.x}+{self.y}")
        self.root.update_idletasks()
        self.layer = Layered(self.root, self.ww, self.wh)

        self.visible = True
        self.usage = None
        self._cache = (None, None)
        self._drag = None
        self._press = (0, 0)
        self._moved = False
        # lo arrastraste durante esta espera: el centro deja de tironear hasta
        # que deje de esperar
        self.pinned = False

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
            return self.root.winfo_screenwidth() - self.ww - 40, 120

    def save_pos(self):
        try:
            with open(core.data_path("pet_pos.json"), "w", encoding="utf-8") as fh:
                json.dump({"x": self.home[0], "y": self.home[1]}, fh)
        except OSError:
            pass

    def acotar(self, x, y):
        """Deja la ventana adentro del escritorio (contando todos los monitores).

        Hace falta porque la fila crece y se achica sola: si estaba pegada a un
        borde y se abre otro Claude, sin esto la mitad quedaria afuera.
        """
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        x = min(max(x, vx), vx + vw - self.ww)
        y = min(max(y, vy), vy + vh - self.wh)
        return x, y

    @property
    def center(self):
        return ((self.root.winfo_screenwidth() - self.ww) // 2,
                (self.root.winfo_screenheight() - self.wh) // 2)

    def glide(self):
        """Acerca la posicion dibujada al destino. Mientras alguna sesion espera
        el destino es el centro de la pantalla; el resto del tiempo, su lugar.

        Si lo agarraste con el mouse manda tu mano: sin eso el centro lo vuelve a
        chupar en el proximo tick (16ms) y la ventanita queda inmovible justo
        cuando mas estorba, en el medio de la pantalla.
        """
        al_centro = (self.esperando() and self.cfg["center_on_wait"] and not self.pinned)
        tx, ty = self.center if al_centro else self.home
        if abs(tx - self.fx) < 1 and abs(ty - self.fy) < 1:
            self.fx, self.fy = float(tx), float(ty)
        else:
            self.fx += (tx - self.fx) * 0.22
            self.fy += (ty - self.fy) * 0.22
        self.x, self.y = round(self.fx), round(self.fy)

    def esperando(self):
        return any(s["visual"] == "esperando" for s in self.slots.values())

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
        click limpio, te lleva a la terminal DEL BICHITO QUE CLICKEASTE."""
        self._drag = None
        if self._moved:
            self.save_pos()
        else:
            self.focus_session(self.slot_en(e.x_root))

    def slot_en(self, x_root):
        """Que bichito de la fila cae bajo ese x de pantalla."""
        if not self.sesiones:
            return None
        i = int((x_root - self.x) // self.ancho_slot)
        i = max(0, min(i, len(self.sesiones) - 1))
        return self.sesiones[i]

    def focus_session(self, sesion):
        """Trae al frente la ventana de esa sesion.

        Los PID los escribe el hook (la cadena de procesos hasta la terminal).
        Los primeros suelen no tener ventana -o ya ni existir, como los procesos
        cortitos que lanza cada herramienta-, asi que se prueba en orden y gana
        el primero que tenga una.

        Windows solo deja cambiar el primer plano al proceso que ya lo tiene, y
        el click sobre el bichito nos lo acaba de dar: por eso no hace falta
        ninguno de los trucos con AttachThreadInput.
        """
        if not sesion:
            return False
        for pid in sesion.get("focus") or []:
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

        sesiones, timed_out = read_state()
        self.sesiones = sesiones
        ahora = time.time()
        vivos = set()

        for s in sesiones:
            sid = s["sid"]
            vivos.add(sid)
            slot = self.slots.get(sid)
            if slot is None:
                slot = self.slots[sid] = {"visual": "dormido", "frame_i": 0,
                                          "proximo": 0.0, "raw": s["estado"],
                                          "festeja_hasta": 0.0, "congelado": "",
                                          "since": s["since"]}
            if s["estado"] != slot["raw"]:
                # se festeja solo si termino de verdad (hook Stop). Si la sesion
                # se cayo y la descarto el timeout, el tiempo seria inventado
                if slot["raw"] in ("working", "waiting") and s["estado"] == "idle" \
                        and not timed_out:
                    slot["festeja_hasta"] = ahora + CELEBRATE
                    slot["congelado"] = fmt(ahora - slot["since"]) if slot["since"] else ""
                    self.set_visual(slot, "termino")
                slot["raw"] = s["estado"]
            slot["since"] = s["since"]
            if ahora >= slot["festeja_hasta"]:
                self.set_visual(slot, {"working": "cocinando",
                                       "waiting": "esperando"}.get(s["estado"], "dormido"))

        # las sesiones que se cerraron se van con su bichito
        for sid in list(self.slots):
            if sid not in vivos:
                del self.slots[sid]

        if not self.esperando():
            self.pinned = False   # la proxima espera vuelve a ir al centro

        self.medir()
        self.root.after(POLL_MS, self.poll)

    def set_visual(self, slot, name):
        if name != slot["visual"]:
            slot["visual"] = name
            slot["frame_i"] = 0
            slot["proximo"] = 0.0

    # --- medidas ---
    def fuente(self, px):
        f = self._fuentes.get(px)
        if f is None:
            try:
                f = ImageFont.truetype("segoeuib.ttf", px)
            except OSError:
                f = ImageFont.load_default()
            self._fuentes[px] = f
        return f

    def medir(self):
        """Escala y tamano de la ventana segun cuantas sesiones haya abiertas.

        Uno solo se ve como siempre. A partir de dos se achican, porque seis
        bichitos a tamano real ocuparian media pantalla.
        """
        n = max(1, len(self.sesiones))
        if n == 1:
            esc = 1.0
        elif n <= 3:
            esc = 0.7
        elif n <= 6:
            esc = 0.55
        else:
            esc = 0.44
        arte_w = max(1, int(self.w * esc))
        slot = arte_w + SEPARACION
        # que la fila no se coma la pantalla: si no entra, se achica mas
        limite = int(self.root.winfo_screenwidth() * 0.85)
        if slot * n > limite:
            esc *= limite / (slot * n)
            arte_w = max(1, int(self.w * esc))
            slot = arte_w + SEPARACION

        self.escala = esc
        self.arte = (arte_w, max(1, int(self.text_y * esc)))
        self.ancho_slot = slot
        self.px_proyecto = max(9, int(round(12 * esc)))
        self.px_estado = max(9, int(round(11 * esc)))
        alto_texto = self.px_proyecto + self.px_estado + 8
        ww = slot * n
        wh = self.arte[1] + alto_texto + (BANDA_USO if self._usage_text() else 0)
        if (ww, wh) != (self.ww, self.wh):
            self.ww, self.wh = ww, wh
            # la primera medicion pasa antes de que existan la capa y la
            # posicion: ahi solo hay que dejar el tamano calculado
            if getattr(self, "layer", None) is not None:
                self.home = self.acotar(*self.home)
                self.x, self.y = self.acotar(self.x, self.y)
                self.fx, self.fy = float(self.x), float(self.y)
                self.layer.resize(ww, wh)
                self.root.geometry(f"{ww}x{wh}+{self.x}+{self.y}")
                self._cache = (None, None)

    # --- dibujo ---
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

    def etiqueta(self, slot):
        """Las dos lineas de abajo de un bichito: proyecto y en que anda."""
        if slot["visual"] == "termino":
            estado = f"Listo  {slot['congelado']}".strip() if self.cfg["timer"] else "Listo"
        else:
            base = TEXTO.get(slot["visual"], "")
            if base and slot["since"] and self.cfg["timer"]:
                estado = f"{base}  {fmt(time.time() - slot['since'])}"
            else:
                estado = base
        return estado

    def _recortar(self, txt, font, ancho):
        """Achica el nombre del proyecto hasta que entre en su columna."""
        d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        if d.textlength(txt, font=font) <= ancho:
            return txt
        while txt and d.textlength(txt + "…", font=font) > ancho:
            txt = txt[:-1]
        return txt + "…" if txt else ""

    def _linea(self, img, txt, font, cx, top, fill):
        """Una linea centrada en cx, con contorno oscuro para que se lea contra
        cualquier fondo. Se dibuja en una capa aparte porque ImageDraw pisa el
        alfa en vez de mezclarlo."""
        if not txt:
            return
        d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        ancho = int(d0.textlength(txt, font=font)) + 4
        alto = font.size + 6
        capa = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
        d = ImageDraw.Draw(capa)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    d.text((2 + dx, 2 + dy), txt, font=font, fill=BORDE)
        d.text((2, 2), txt, font=font, fill=fill)
        img.alpha_composite(capa, (max(0, int(cx - ancho / 2)), int(top)))

    def compose(self):
        img = Image.new("RGBA", (self.ww, self.wh), (0, 0, 0, 0))
        hay_espera = self.esperando()
        aw, ah = self.arte
        fp = self.fuente(self.px_proyecto)
        fe = self.fuente(self.px_estado)

        for i, s in enumerate(self.sesiones):
            slot = self.slots.get(s["sid"])
            if not slot:
                continue
            arte = self.frames[slot["visual"]][slot["frame_i"]]
            # se recorta la banda de texto del sprite: las etiquetas ahora se
            # dibujan aparte, y con dos lineas por bichito
            arte = arte.crop((0, 0, self.w, self.text_y))
            if (aw, ah) != (self.w, self.text_y):
                arte = arte.resize((aw, ah), Image.NEAREST)
            espera = slot["visual"] == "esperando"
            if hay_espera and not espera:
                arte = atenuar(arte, 0.58)   # el que te llama se distingue solo
            x = i * self.ancho_slot + (self.ancho_slot - aw) // 2
            img.alpha_composite(arte, (x, 0))

            cx = i * self.ancho_slot + self.ancho_slot / 2
            proyecto = self._recortar(s["proyecto"] or "Claude", fp, self.ancho_slot - 4)
            self._linea(img, proyecto, fp, cx, ah,
                        ARCILLA if espera else CREMA)
            self._linea(img, self.etiqueta(slot), fe, cx, ah + self.px_proyecto + 4,
                        CREMA if espera else (200, 186, 172, 235))

        uso = self._usage_text()
        if uso:
            f = self.fuente(max(9, int(round(11 * self.escala))))
            ancho = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textlength(uso, font=f)
            self._linea(img, uso, f, self.ww - ancho / 2 - 6,
                        self.wh - BANDA_USO + 2, TOSTADO)
        return premultiply(img)

    def clave(self):
        """Todo lo que, si cambia, obliga a redibujar."""
        return (self.ww, self.wh, self._usage_text(),
                tuple((s["sid"], self.slots.get(s["sid"], {}).get("visual"),
                       self.slots.get(s["sid"], {}).get("frame_i"),
                       s["proyecto"], self.etiqueta(self.slots[s["sid"]])
                       if s["sid"] in self.slots else "")
                      for s in self.sesiones))

    def render(self, force=False):
        k = self.clave()
        if force or k != self._cache[0]:
            self._cache = (k, self.compose())
        if self.visible:
            self.layer.blit(self._cache[1], self.x, self.y)

    def animate(self):
        """Cada bichito avanza a su propio fps: cocinar y dormir no van al mismo
        ritmo, y con varias sesiones en pantalla se notaria."""
        ahora = time.time()
        for slot in self.slots.values():
            info = self.manifest["states"][slot["visual"]]
            if ahora < slot["proximo"]:
                continue
            slot["proximo"] = ahora + 1 / info["fps"]
            if slot["visual"] == "termino":
                slot["frame_i"] = min(info["frames"] - 1, slot["frame_i"] + 1)
            else:
                slot["frame_i"] = (slot["frame_i"] + 1) % info["frames"]
        self.render()
        self.root.after(ANIM_MS, self.animate)

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
