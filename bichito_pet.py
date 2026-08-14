"""Los bichitos: una ventanita flotante POR SESION de Claude Code abierta.

Cada una es independiente: la arrastras a donde quieras y ahi se queda (se
recuerda por proyecto, asi que la proxima sesion de ese proyecto nace en el
mismo lugar), la escondes sola si molesta, y clickeandola vas a la terminal de
esa sesion. Con seis Claude a la vez, saber que "algo" te espera no sirve: lo
que hace falta es saber cual, y donde.

Lee state/*.json (uno por sesion, los escribe bichito-hook.exe) y anima el
sprite que corresponde. Se dibuja con UpdateLayeredWindow para tener alfa real
por pixel: con la transparencia normal de tkinter (-transparentcolor) los bordes
suavizados del sprite quedarian con un halo del color de fondo.

  arrastrar con el boton izquierdo  -> mover ese bichito (se guarda)
  arrastrar con Ctrl                -> moverlos a todos juntos
  click                             -> traer al frente esa terminal
  boton derecho                     -> menu (tamano, mover todos juntos,
                                      acomodarlos en fila, ocultar este, salir)
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

LOCK_PORT = 50519       # instancia unica: si el puerto esta tomado, ya hay uno
POLL_MS = 250           # cada cuanto se relee state/ y la config
ANIM_MS = 50            # tick de animacion; cada uno avanza a SU propio fps
DESLIZ_MS = 16          # tick del viaje al centro
WORKING_TIMEOUT = 900   # s sin latido -> la sesion murio (Esc, cierre)
DORMIDO_TIMEOUT = 7200  # s dormida sin novedades -> se la da por cerrada
STALE = 86400           # s -> archivo de sesion viejo, se borra
CELEBRATE = 2.4         # s de festejo antes de dormirse
BANDA_USO = 22          # px de la franja del % del plan
ARRASTRE_MIN = 4        # px que hay que moverse para que cuente como arrastre
HUECO = 14              # px entre un bichito y el siguiente al acomodarlos solos

TEXTO = {"cocinando": "Cocinando", "esperando": "Te espera",
         "termino": "Listo", "dormido": ""}

# tamanos del menu de boton derecho; "auto" los achica segun cuantos haya
ESCALAS = {"grande": 1.0, "mediano": 0.8, "chico": 0.62}

CREMA = (255, 246, 236, 255)
ARCILLA = (233, 150, 110, 255)   # el que te espera se distingue por color
APAGADO = (200, 186, 172, 235)
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


def limites():
    """El escritorio entero, contando todos los monitores (el de la izquierda
    tiene x negativa)."""
    return (user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))


def acotar(x, y, w, h):
    """Deja una ventana adentro del escritorio."""
    vx, vy, vw, vh = limites()
    return (min(max(x, vx), vx + vw - w), min(max(y, vy), vy + vh - h))


class Layered:
    """Superficie RGBA dibujada directamente por Windows.

    Se redimensiona porque el tamano cambia con el nombre del proyecto y con
    cuantas sesiones haya abiertas.
    """

    def __init__(self, ventana, w, h):
        self.hwnd = toplevel_hwnd(ventana)
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
    borra en SessionEnd. Lo que se cayo sin avisar se descarta por antiguedad y
    su bichito desaparece, que es lo que corresponde: nadie festeja por una
    sesion que murio.
    """
    sesiones = []
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
            continue
        # dormida hace horas y sin SessionEnd: se la da por cerrada
        if estado == "idle" and edad > DORMIDO_TIMEOUT:
            continue
        sesiones.append({
            "sid": name[:-5],
            "estado": estado,
            "since": data.get("since"),
            "focus": [p for p in (data.get("focus") or []) if isinstance(p, int)],
            "proyecto": (data.get("proyecto") or "").strip() or "Claude",
        })
    sesiones.sort(key=lambda s: (s["proyecto"].lower(), s["sid"]))
    return sesiones


def fmt(seconds):
    s = int(max(0, seconds))
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


# --------------------------------------------------------------- un bichito
class Mini:
    """Un bichito: su ventana, su lugar y el estado de su sesion."""

    def __init__(self, app, sesion, indice):
        self.app = app
        self.sid = sesion["sid"]
        self.proyecto = sesion["proyecto"]
        self.sesion = sesion

        self.visual = "dormido"
        self.raw = sesion["estado"]
        self.since = sesion["since"]
        self.frame_i = 0
        self.proximo = 0.0
        self.festeja_hasta = 0.0
        self.congelado = ""
        self.pinned = False
        self.lleva_uso = False
        self._cache = (None, None)
        self._drag = None
        self._press = (0, 0)
        self._moved = False

        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.wm_attributes("-topmost", app.topmost)
        self.ww, self.wh = 10, 10
        self.medir()

        pos = app.posicion_guardada(self.proyecto)
        if pos is None:
            pos = app.lugar_por_defecto(indice, self.ww)
        self.x, self.y = acotar(pos[0], pos[1], self.ww, self.wh)
        self.home = (self.x, self.y)
        self.fx, self.fy = float(self.x), float(self.y)
        self.win.geometry(f"{self.ww}x{self.wh}+{self.x}+{self.y}")
        self.win.update_idletasks()
        self.layer = Layered(self.win, self.ww, self.wh)

        self.win.bind("<Button-1>", self.drag_start)
        self.win.bind("<B1-Motion>", self.drag_move)
        self.win.bind("<ButtonRelease-1>", self.drag_end)
        self.win.bind("<Button-3>", self.popup)

    # --- ciclo de vida ---
    def destruir(self):
        self.layer._soltar()
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def visible(self):
        return self.app.visible and self.proyecto not in self.app.ocultos

    def aplicar_visibilidad(self):
        if self.visible():
            self.win.deiconify()
            self.render(force=True)
        else:
            self.win.withdraw()

    # --- estado ---
    def actualizar(self, sesion, ahora):
        self.sesion = sesion
        self.proyecto = sesion["proyecto"]
        if sesion["estado"] != self.raw:
            # se festeja cuando la sesion termina de verdad (hook Stop). Si se
            # cayo, su archivo envejece y el bichito desaparece sin festejo.
            if self.raw in ("working", "waiting") and sesion["estado"] == "idle":
                self.festeja_hasta = ahora + CELEBRATE
                self.congelado = fmt(ahora - self.since) if self.since else ""
                self.set_visual("termino")
            self.raw = sesion["estado"]
        self.since = sesion["since"]
        if ahora >= self.festeja_hasta:
            self.set_visual({"working": "cocinando",
                             "waiting": "esperando"}.get(sesion["estado"], "dormido"))
        if self.visual != "esperando":
            self.pinned = False   # la proxima espera vuelve a ir al centro

    def set_visual(self, name):
        if name != self.visual:
            self.visual = name
            self.frame_i = 0
            self.proximo = 0.0

    def animar(self, ahora):
        info = self.app.manifest["states"][self.visual]
        if ahora < self.proximo:
            return
        self.proximo = ahora + 1 / info["fps"]
        if self.visual == "termino":
            self.frame_i = min(info["frames"] - 1, self.frame_i + 1)  # una pasada
        else:
            self.frame_i = (self.frame_i + 1) % info["frames"]

    # --- posicion ---
    def deslizar(self):
        """Solo el que te espera se va al centro. Los demas se quedan donde los
        pusiste: si saltaran todos, taparian la pantalla entera."""
        if self.visual == "esperando" and self.app.cfg["center_on_wait"] and not self.pinned:
            tx, ty = ((self.app.root.winfo_screenwidth() - self.ww) // 2,
                      (self.app.root.winfo_screenheight() - self.wh) // 2)
        else:
            tx, ty = self.home
        antes = (self.x, self.y)
        if abs(tx - self.fx) < 1 and abs(ty - self.fy) < 1:
            self.fx, self.fy = float(tx), float(ty)
        else:
            self.fx += (tx - self.fx) * 0.22
            self.fy += (ty - self.fy) * 0.22
        self.x, self.y = round(self.fx), round(self.fy)
        if (self.x, self.y) != antes and self.visible() and self._cache[1] is not None:
            self.layer.blit(self._cache[1], self.x, self.y)

    def drag_start(self, e):
        self._drag = (e.x_root - self.x, e.y_root - self.y)
        self._press = (e.x_root, e.y_root)
        self._moved = False
        # con Ctrl apretado -o con el modo prendido en el menu- se arrastra la
        # banda entera, conservando las distancias entre ellos
        self._grupo = bool(e.state & 0x0004) or bool(self.app.cfg.get("mover_juntos"))
        self._inicio = ({m.sid: m.home for m in self.app.minis.values()}
                        if self._grupo else None)

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
        if self._grupo:
            dx = e.x_root - self._press[0]
            dy = e.y_root - self._press[1]
            # el borde frena a la banda entera y no a cada uno por su cuenta: si
            # se acotara de a uno, el que topa se queda y el grupo se deforma
            vx, vy, vw, vh = limites()
            for m in self.app.minis.values():
                ix, iy = self._inicio.get(m.sid, m.home)
                dx = min(max(dx, vx - ix), vx + vw - m.ww - ix)
                dy = min(max(dy, vy - iy), vy + vh - m.wh - iy)
            for m in self.app.minis.values():
                ix, iy = self._inicio.get(m.sid, m.home)
                m.x, m.y = ix + dx, iy + dy
                m.fx, m.fy = float(m.x), float(m.y)
                m.home = (m.x, m.y)
                m.pinned = True
                if m._cache[1] is not None:
                    m.layer.blit(m._cache[1], m.x, m.y)
                else:
                    m.render()
            return
        self.x = e.x_root - self._drag[0]
        self.y = e.y_root - self._drag[1]
        self.fx, self.fy = float(self.x), float(self.y)
        self.home = (self.x, self.y)   # arrastrarlo redefine SU lugar
        # arrastrar gana: mientras dure esta espera, el centro deja de tironear
        self.pinned = True
        if self._cache[1] is not None:
            self.layer.blit(self._cache[1], self.x, self.y)
        else:
            self.render()

    def drag_end(self, e):
        """Si lo moviste, se guarda donde quedo (por proyecto, asi la proxima
        sesion de ese proyecto nace ahi); si venias arrastrando el grupo, se
        guardan todos. Si fue un click limpio, te lleva a la terminal de ESTA
        sesion."""
        self._drag = None
        if not self._moved:
            self.focus()
            return
        if self._grupo:
            for m in self.app.minis.values():
                self.app.lugares[m.proyecto] = {"x": int(m.home[0]), "y": int(m.home[1])}
            self.app.base = (int(self.home[0]), int(self.home[1]))
            self.app.guardar_lugares()
        else:
            self.app.guardar_posicion(self.proyecto, self.home)

    def focus(self):
        """Trae al frente la ventana de esta sesion.

        Los PID los escribe el hook (la cadena de procesos hasta la terminal).
        Los primeros suelen no tener ventana -o ya ni existir, como los procesos
        cortitos que lanza cada herramienta-, asi que se prueba en orden y gana
        el primero que tenga una.

        Windows solo deja cambiar el primer plano al proceso que ya lo tiene, y
        el click sobre el bichito nos lo acaba de dar: por eso no hace falta
        ninguno de los trucos con AttachThreadInput.
        """
        for pid in self.sesion.get("focus") or []:
            hwnd = ventana_principal(pid)
            if not hwnd:
                continue
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            return True
        return False

    def popup(self, e):
        self.app.abrir_menu(self, e)

    # --- dibujo ---
    def medir(self):
        """Tamano de SU ventana: el arte escalado, y abajo las dos lineas. El
        ancho lo puede mandar el nombre del proyecto, no siempre el sprite."""
        app = self.app
        esc = app.escala
        aw = max(1, int(app.w * esc))
        ah = max(1, int(app.text_y * esc))
        px_p = max(9, int(round(12 * esc)))
        px_e = max(9, int(round(11 * esc)))
        fp, fe = app.fuente(px_p), app.fuente(px_e)
        d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        ancho_txt = max(d.textlength(self.proyecto, font=fp),
                        d.textlength(self.etiqueta() or "Cocinando 00m 00s", font=fe))
        ww = int(max(aw, ancho_txt + 12))
        wh = ah + px_p + px_e + 10 + (BANDA_USO if self.lleva_uso else 0)
        self.arte = (aw, ah)
        self.px_p, self.px_e = px_p, px_e
        if (ww, wh) != (self.ww, self.wh):
            self.ww, self.wh = ww, wh
            if getattr(self, "layer", None) is not None:
                self.x, self.y = acotar(self.x, self.y, ww, wh)
                self.home = acotar(self.home[0], self.home[1], ww, wh)
                self.fx, self.fy = float(self.x), float(self.y)
                self.layer.resize(ww, wh)
                self.win.geometry(f"{ww}x{wh}+{self.x}+{self.y}")
                self._cache = (None, None)

    def etiqueta(self):
        if self.visual == "termino":
            return f"Listo  {self.congelado}".strip() if self.app.cfg["timer"] else "Listo"
        base = TEXTO.get(self.visual, "")
        if base and self.since and self.app.cfg["timer"]:
            return f"{base}  {fmt(time.time() - self.since)}"
        return base

    def compose(self):
        app = self.app
        img = Image.new("RGBA", (self.ww, self.wh), (0, 0, 0, 0))
        aw, ah = self.arte
        arte = app.frames[self.visual][self.frame_i]
        # se recorta la banda de texto del sprite: las etiquetas se dibujan
        # aparte, y son dos lineas
        arte = arte.crop((0, 0, app.w, app.text_y))
        if (aw, ah) != (app.w, app.text_y):
            arte = arte.resize((aw, ah), Image.NEAREST)
        img.alpha_composite(arte, ((self.ww - aw) // 2, 0))

        espera = self.visual == "esperando"
        cx = self.ww / 2
        app.linea(img, self.proyecto, app.fuente(self.px_p), cx, ah,
                  ARCILLA if espera else CREMA)
        app.linea(img, self.etiqueta(), app.fuente(self.px_e), cx,
                  ah + self.px_p + 4, CREMA if espera else APAGADO)
        if self.lleva_uso:
            uso = app.texto_uso()
            app.linea(img, uso, app.fuente(max(9, int(round(11 * app.escala)))),
                      cx, self.wh - BANDA_USO + 4, TOSTADO)
        return premultiply(img)

    def clave(self):
        return (self.ww, self.wh, self.visual, self.frame_i, self.proyecto,
                self.etiqueta(), self.app.texto_uso() if self.lleva_uso else "")

    def render(self, force=False):
        k = self.clave()
        if force or k != self._cache[0]:
            self._cache = (k, self.compose())
        if self.visible():
            self.layer.blit(self._cache[1], self.x, self.y)


# ---------------------------------------------------------------------- app
class Bichito:
    """La aplicacion: la bandeja, la config y los bichitos que haya que tener."""

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
        # la raiz no se ve: solo sostiene las ventanitas, la bandeja y los menus
        self.root = tk.Tk()
        self.root.withdraw()
        self.topmost = bool(self.cfg["always_on_top"])

        guardado = self.leer_lugares()
        self.base = (guardado.get("x"), guardado.get("y"))
        if self.base[0] is None:
            self.base = (self.root.winfo_screenwidth() - self.w - 40, 120)
        self.lugares = guardado.get("por_proyecto") or {}
        self.ocultos = set(guardado.get("ocultos") or [])

        self.visible = True
        self.usage = None
        self.escala = 1.0
        self.minis = {}      # sid -> Mini
        self.orden = []      # sids, en el orden en que se dibujan

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu_tam = tk.Menu(self.menu, tearoff=0)
        self.objetivo = None
        self.armar_menu()

        self.tray = Tray(self)
        self.tray.start()

        # poller de usage: mantiene state/usage.json fresco para que esto solo
        # (sin el panel) pueda mostrar el %.
        self.poller = bichito_usage.Poller()
        self.poller.start()

        self.poll()
        self.animate()
        self.slide()

    # --- menus ---
    def armar_menu(self):
        """El menu se arma una vez; los indices se guardan porque las etiquetas
        cambian segun a que bichito le hiciste click."""
        for nombre in ("auto", "grande", "mediano", "chico"):
            self.menu_tam.add_command(
                label=nombre.capitalize(),
                command=lambda n=nombre: self.set_cfg("tamano", n))
        self.var_juntos = tk.BooleanVar(value=bool(self.cfg.get("mover_juntos")))
        self.idx = {}

        def agregar(clave, *args, **kw):
            self.menu.add_command(*args, **kw)
            self.idx[clave] = self.menu.index("end")

        agregar("terminal", label="Ir a esta terminal",
                command=lambda: self.objetivo and self.objetivo.focus())
        agregar("panel", label="Abrir panel", command=self.open_panel)
        self.menu.add_cascade(label="Tamano", menu=self.menu_tam)
        agregar("encima", label="Siempre encima", command=self.toggle_top)
        self.menu.add_separator()
        # arrastrar uno lleva a todos, sin perder las distancias entre ellos
        self.menu.add_checkbutton(label="Mover todos juntos", variable=self.var_juntos,
                                  command=self.toggle_juntos)
        self.idx["juntos"] = self.menu.index("end")
        agregar("fila", label="Acomodarlos en fila", command=self.acomodar_fila)
        self.menu.add_separator()
        agregar("ocultar", label="Ocultar este", command=self.ocultar_este)
        agregar("mostrar", label="Mostrar todos", command=self.mostrar_todos)
        agregar("apagar", label="Ocultar todos", command=self.toggle_pet)
        agregar("salir", label="Salir del todo", command=self.quit_all)

    def abrir_menu(self, mini, e):
        self.objetivo = mini
        self.var_juntos.set(bool(self.cfg.get("mover_juntos")))
        self.menu.entryconfigure(self.idx["terminal"],
                                 label=f"Ir a la terminal de {mini.proyecto}")
        self.menu.entryconfigure(self.idx["encima"],
                                 label="Siempre encima  " + ("si" if self.topmost else "no"))
        self.menu.entryconfigure(self.idx["ocultar"], label=f"Ocultar {mini.proyecto}")
        self.menu.entryconfigure(self.idx["mostrar"],
                                 state="normal" if self.ocultos else "disabled")
        self.menu.entryconfigure(self.idx["fila"],
                                 state="normal" if len(self.minis) > 1 else "disabled")
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()  # si no, el menu se puede comer el mouse

    def toggle_juntos(self):
        self.set_cfg("mover_juntos", bool(self.var_juntos.get()))

    def acomodar_fila(self):
        """Los pone a todos en fila desde el ancla, por si quedaron desparramados."""
        for i, sid in enumerate(self.orden):
            mini = self.minis.get(sid)
            if not mini:
                continue
            x, y = acotar(*self.lugar_por_defecto(i, mini.ww), mini.ww, mini.wh)
            mini.home = (x, y)
            mini.pinned = False
            self.lugares[mini.proyecto] = {"x": x, "y": y}
        self.guardar_lugares()

    def set_cfg(self, clave, valor):
        self.cfg = core.save_config({**core.load_config(), clave: valor})

    def ocultar_este(self):
        if not self.objetivo:
            return
        self.ocultos.add(self.objetivo.proyecto)
        self.guardar_lugares()
        self.objetivo.aplicar_visibilidad()

    def mostrar_todos(self):
        self.ocultos.clear()
        self.guardar_lugares()
        for m in self.minis.values():
            m.aplicar_visibilidad()

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

    def toggle_top(self):
        self.topmost = not self.topmost
        for m in self.minis.values():
            m.win.wm_attributes("-topmost", self.topmost)
        self.cfg = core.save_config({**core.load_config(), "always_on_top": self.topmost})

    def quit_all(self):
        self.tray.stop()
        self.poller.stop()
        self.root.destroy()

    # --- lugares (se recuerdan por proyecto, no por sesion: los id cambian) ---
    def leer_lugares(self):
        try:
            with open(core.data_path("pet_pos.json"), encoding="utf-8-sig") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def guardar_lugares(self):
        datos = {"x": self.base[0], "y": self.base[1],
                 "por_proyecto": self.lugares, "ocultos": sorted(self.ocultos)}
        try:
            with open(core.data_path("pet_pos.json"), "w", encoding="utf-8") as fh:
                json.dump(datos, fh, indent=2)
        except OSError:
            pass

    def posicion_guardada(self, proyecto):
        p = self.lugares.get(proyecto)
        if isinstance(p, dict) and "x" in p and "y" in p:
            return int(p["x"]), int(p["y"])
        return None

    def guardar_posicion(self, proyecto, pos):
        self.lugares[proyecto] = {"x": int(pos[0]), "y": int(pos[1])}
        self.base = (int(pos[0]), int(pos[1]))   # el ultimo que moviste manda
        self.guardar_lugares()

    def lugar_por_defecto(self, indice, ancho):
        """Los que nunca moviste se acomodan en fila desde el ancla, para el
        lado donde haya pantalla."""
        bx, by = self.base
        hacia_izquierda = bx > self.root.winfo_screenwidth() / 2
        paso = (ancho + HUECO) * indice
        return (bx - paso if hacia_izquierda else bx + paso, by)

    # --- textos ---
    def fuente(self, px):
        f = self._fuentes.get(px)
        if f is None:
            try:
                f = ImageFont.truetype("segoeuib.ttf", px)
            except OSError:
                f = ImageFont.load_default()
            self._fuentes[px] = f
        return f

    def linea(self, img, txt, font, cx, top, fill):
        """Una linea centrada en cx, con contorno oscuro para que se lea contra
        cualquier fondo. Va en una capa aparte porque ImageDraw pisa el alfa en
        vez de mezclarlo."""
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

    def texto_uso(self):
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

    # --- loop ---
    def calcular_escala(self, n):
        """Cuanto miden. A mano manda el menu; en automatico se achican recien
        cuando son varios, y sin pasarse: si no se leen, no sirven."""
        manual = ESCALAS.get(str(self.cfg.get("tamano", "auto")).lower())
        if manual:
            return manual
        if n <= 2:
            return 1.0
        if n <= 4:
            return 0.85
        if n <= 8:
            return 0.72
        return 0.6

    def poll(self):
        # la config se relee en cada vuelta: asi los interruptores del panel
        # tienen efecto al instante, sin reiniciar nada
        cfg = core.load_config()
        visible = bool(cfg["enabled"] and cfg["pet"])
        cambio_visible = visible != self.visible
        self.visible = visible
        if cfg["always_on_top"] != self.topmost:
            self.topmost = bool(cfg["always_on_top"])
            for m in self.minis.values():
                m.win.wm_attributes("-topmost", self.topmost)
        self.cfg = cfg
        self.usage = bichito_usage.read()

        sesiones = read_state()
        self.orden = [s["sid"] for s in sesiones]
        escala = self.calcular_escala(len(sesiones))
        cambio_escala = escala != self.escala
        self.escala = escala

        ahora = time.time()
        for i, s in enumerate(sesiones):
            mini = self.minis.get(s["sid"])
            if mini is None:
                mini = self.minis[s["sid"]] = Mini(self, s, i)
                mini.aplicar_visibilidad()
            mini.actualizar(s, ahora)
            # el % del plan es de la cuenta, no de una sesion: lo lleva el
            # primero, y no los seis
            lleva = (i == 0) and bool(self.texto_uso())
            if lleva != mini.lleva_uso or cambio_escala:
                mini.lleva_uso = lleva
                mini.medir()

        for sid in list(self.minis):
            if sid not in self.orden:
                self.minis.pop(sid).destruir()

        if cambio_visible:
            for m in self.minis.values():
                m.aplicar_visibilidad()

        self.root.after(POLL_MS, self.poll)

    def animate(self):
        ahora = time.time()
        for m in self.minis.values():
            m.animar(ahora)
            m.medir()      # el nombre o el cronometro pueden cambiar el ancho
            m.render()
        self.root.after(ANIM_MS, self.animate)

    def slide(self):
        """El desplazamiento va en su propio loop, mas rapido que la animacion:
        a 3-4 fps el viaje al centro se veria a los saltos."""
        for m in self.minis.values():
            m.deslizar()
        self.root.after(DESLIZ_MS, self.slide)

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
                pystray.MenuItem("Mostrar bichitos", later(self.app.toggle_pet),
                                 checked=lambda _: bool(core.load_config()["pet"])),
                pystray.MenuItem("Mostrar los que escondiste", later(self.app.mostrar_todos)),
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
