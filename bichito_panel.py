"""Panel de control del bichito (pywebview: la UI es HTML/CSS).

Los interruptores escriben config.json y nada mas. bichito-hook.exe lo relee en
cada llamada y el bichito en cada vuelta de su loop, asi que todo tiene efecto
al instante, sin reiniciar Claude Code.
"""
import base64
import io
import json
import os
import subprocess
import sys

import bichito_core as core
import bichito_install as installer

TOGGLES = [
    ("enabled", "Interruptor general", "Apaga todo de una. Los hooks quedan puestos pero no hacen nada."),
    ("pet", "Bichito flotante", "La ventanita que cocina, espera y festeja."),
    ("voice", "Voz al terminar", "Te avisa hablando cuando termina o cuando necesita algo."),
    ("timer", "Cronometro", "El tiempo debajo del bichito."),
    ("center_on_wait", "Saltar al centro", "Cuando te hace una pregunta se va al medio de la pantalla, y despues vuelve."),
    ("always_on_top", "Siempre encima", "Que no se lo tapen otras ventanas."),
    ("autostart", "Arranque automatico", "Levantarlo solo al abrir Claude Code."),
]


def sprite_strip():
    """Tira horizontal con los frames de 'cocinando', en base64.

    Se anima en CSS con steps(): el encabezado del panel muestra al bichito
    cocinando de verdad, sin meter un GIF ni un canvas.
    """
    try:
        from PIL import Image
        with open(core.resource_path("assets", "manifest.json"), encoding="utf-8") as fh:
            man = json.load(fh)
        n = man["states"]["cocinando"]["frames"]
        w, h = man["size"]
        strip = Image.new("RGBA", (w * n, h), (0, 0, 0, 0))
        for i in range(n):
            fr = Image.open(core.resource_path("assets", "cocinando", f"{i:02d}.png"))
            strip.alpha_composite(fr.convert("RGBA"), (i * w, 0))
        # se recorta la franja del reloj: en el panel solo interesa el bicho
        strip = strip.crop((0, 0, w * n, man["text_y"]))
        buf = io.BytesIO()
        strip.save(buf, "PNG")
        return base64.b64encode(buf.getvalue()).decode(), n, w, man["text_y"]
    except Exception:
        return "", 1, 176, 176


# La ventana va en un global, NO como atributo de Api: pywebview recorre los
# atributos del objeto js_api para exponerlos, y al toparse con la ventana
# nativa se mete en una recursion infinita (window.native.AccessibilityObject
# .Bounds.Empty.Empty.Empty...) que deja el puente JS muerto sin explicacion.
_WINDOW = None


class Api:
    """Lo que el HTML puede llamar (pywebview.api.*)."""

    def state(self):
        cfg = core.load_config()
        return {
            "config": {k: bool(cfg.get(k, True)) for k, _, _ in TOGGLES},
            "installed": installer.is_installed(),
            "voice_script": cfg.get("voice_script", ""),
            "data_dir": core.data_dir(),
            "msg_done": cfg.get("msg_done", core.DEFAULTS["msg_done"]),
            "msg_waiting": cfg.get("msg_waiting", core.DEFAULTS["msg_waiting"]),
        }

    def set_text(self, key, value):
        if key not in ("msg_done", "msg_waiting"):
            return self.state()
        cfg = core.load_config()
        # vacio -> se vuelve al de fabrica, si no quedaria mudo o diciendo basura
        cfg[key] = (value or "").strip() or core.DEFAULTS[key]
        core.save_config(cfg)
        return self.state()

    def set_toggle(self, key, value):
        cfg = core.load_config()
        cfg[key] = bool(value)
        core.save_config(cfg)
        # prender el bichito lo levanta en el acto; apagarlo lo cierra solo
        # (su propio loop detecta el cambio en la proxima vuelta)
        if key in ("enabled", "pet") and cfg.get("enabled") and cfg.get("pet"):
            self.launch_pet()
        return self.state()

    def install(self):
        try:
            installer.install()
            cfg = core.load_config()
            if cfg.get("enabled") and cfg.get("pet"):
                self.launch_pet()
            return {"ok": True, **self.state()}
        except Exception as exc:
            return {"ok": False, "error": str(exc), **self.state()}

    def uninstall(self):
        try:
            installer.uninstall()
            return {"ok": True, **self.state()}
        except Exception as exc:
            return {"ok": False, "error": str(exc), **self.state()}

    def launch_pet(self):
        exe = installer.app_exe()
        try:
            if getattr(sys, "frozen", False) and os.path.exists(exe):
                subprocess.Popen([exe, "--pet"], creationflags=0x00000008)
            else:
                import threading
                import bichito_pet
                threading.Thread(target=bichito_pet.run, daemon=True).start()
        except Exception:
            pass
        return True

    def open_folder(self):
        os.startfile(core.data_dir())  # noqa: S606
        return True

    def close(self):
        if _WINDOW:
            _WINDOW.destroy()


HTML = """
<!DOCTYPE html><html><head><meta charset="utf-8"><style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font: 14px/1.45 "Segoe UI", system-ui, sans-serif;
  background: #17140f; color: #f2e9df; user-select: none;
  -webkit-user-select: none;
  /* columna con pie fijo: los botones de instalar quedan SIEMPRE a la vista y
     lo que scrollea es el medio. Asi entra en cualquier pantalla sin depender
     de que la ventana mida lo justo. */
  display: flex; flex-direction: column; height: 100vh; overflow: hidden;
}
.scroll { flex: 1; overflow-y: auto; overflow-x: hidden; }
.scroll::-webkit-scrollbar { width: 8px; }
.scroll::-webkit-scrollbar-thumb { background: #3a3128; border-radius: 4px; }
.foot { padding: 12px 22px 16px; border-top: 1px solid #2b2419; background: #17140f; }
.foot .btn:first-child { margin-top: 0; }
.bar { height: 34px; display: flex; align-items: center; justify-content: flex-end;
       padding: 0 6px; }
.x { width: 28px; height: 26px; border: 0; border-radius: 7px;
     background: transparent; color: #8c7f72; font-size: 16px; cursor: pointer; }
.x:hover { background: #3a2a20; color: #f2e9df; }

.hero { text-align: center; padding: 0 24px 2px; }
.pet {
  width: PETWpx; height: PETHpx; margin: -10px auto -14px;
  background-repeat: no-repeat;
  /* tamano explicito, no "auto": redondear la altura desalinearia los pasos y
     la animacion iria derivando un pixel por vuelta */
  background-size: STRIPWpx PETHpx;
  image-rendering: pixelated;              /* que no se difumine el pixel art */
  animation: walk 2s steps(FRAMES) infinite;
}
@keyframes walk { from { background-position: 0 0; } to { background-position: -STRIPWpx 0; } }
h1 { font-size: 20px; font-weight: 650; letter-spacing: .2px; }
.sub { color: #9b8b7c; font-size: 12.5px; margin-top: 3px; }

.wrap { padding: 12px 22px 18px; }
.card { background: #211c16; border: 1px solid #322a21; border-radius: 14px; padding: 2px 16px; }

.row { display: flex; align-items: center; gap: 14px; padding: 10px 0;
       border-bottom: 1px solid #2b2419; }
.row:last-child { border-bottom: 0; }
.row.off .tt, .row.off .dd { opacity: .38; }
.txt { flex: 1; min-width: 0; }
.tt { font-weight: 560; font-size: 13.5px; }
.dd { color: #8c7f72; font-size: 11.5px; margin-top: 2px; }

.sw { width: 42px; height: 24px; border-radius: 12px; background: #3d342a; position: relative;
      cursor: pointer; flex: none; transition: background .18s; }
.sw.on { background: #d97757; }
.sw i { position: absolute; top: 3px; left: 3px; width: 18px; height: 18px; border-radius: 50%;
        background: #f2e9df; transition: left .18s; }
.sw.on i { left: 21px; }

.master { margin-bottom: 10px; padding: 10px 16px; }
.master .tt { font-size: 15px; }

.btn { width: 100%; margin-top: 14px; padding: 12px; border: 0; border-radius: 11px;
       font: 600 14px "Segoe UI"; cursor: pointer; transition: filter .15s; }
.btn:hover { filter: brightness(1.1); }
.go { background: #d97757; color: #1a120c; }
.un { background: transparent; color: #8c7f72; border: 1px solid #3a3128; margin-top: 9px;
      padding: 10px; font-size: 12.5px; }
.un:hover { color: #e08a6a; border-color: #6b4030; }

.msgs { margin-top: 12px; padding: 12px 16px 14px; }
.msgs h2 { font-size: 11px; font-weight: 650; letter-spacing: .07em; text-transform: uppercase;
           color: #8c7f72; margin-bottom: 10px; }
.fld { margin-bottom: 10px; }
.fld:last-child { margin-bottom: 0; }
.fld label { display: block; font-size: 11.5px; color: #9b8b7c; margin-bottom: 4px; }
.fld input {
  width: 100%; padding: 8px 10px; border-radius: 8px; background: #17140f;
  border: 1px solid #3a3128; color: #f2e9df; font: 13px "Segoe UI";
  user-select: text; -webkit-user-select: text;
}
.fld input:focus { outline: 0; border-color: #d97757; }
.hint { font-size: 10.5px; color: #6f645a; margin-top: 5px; }
.hint code { color: #d97757; font-family: Consolas, monospace; }

.note { text-align: center; font-size: 11.5px; color: #8c7f72; margin-top: 13px; line-height: 1.6; }
.note b { color: #d97757; font-weight: 600; }
.pill { display: inline-flex; align-items: center; gap: 6px; padding: 4px 11px; border-radius: 20px;
        font-size: 11.5px; background: #2a2119; color: #9b8b7c; }
.pill.ok { background: #22301f; color: #86c07a; }
.dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
a { color: #8c7f72; cursor: pointer; text-decoration: underline; }
</style></head><body>

<div class="scroll">
<div class="bar"><button class="x" onclick="pywebview.api.close()">&times;</button></div>

<div class="hero">
  <div class="pet" id="pet"></div>
  <h1>Bichito</h1>
  <div class="sub" id="status"><span class="pill">cargando...</span></div>
</div>

<div class="wrap">
  <div class="card master" id="master"></div>
  <div class="card" id="rows"></div>

  <div class="card msgs">
    <h2>Que dice la voz</h2>
    <div class="fld">
      <label>Cuando termina</label>
      <input id="m_done" spellcheck="false">
    </div>
    <div class="fld">
      <label>Cuando te necesita</label>
      <input id="m_wait" spellcheck="false">
    </div>
    <div class="hint">Usa <code>{proyecto}</code> para el nombre de la carpeta. Vacio vuelve al original.</div>
  </div>
</div>
</div>

<div class="foot">
  <button class="btn go" id="action"></button>
  <button class="btn un" id="unbtn" style="display:none">Desinstalar de Claude Code</button>
  <div class="note" id="note"></div>
</div>

<script>
const TOGGLES = TOGGLES_JSON;
let st = null;

function sw(key, on) {
  return `<div class="sw ${on?'on':''}" onclick="flip('${key}')"><i></i></div>`;
}
function row(key, title, desc, on, dim) {
  return `<div class="row ${dim?'off':''}">
    <div class="txt"><div class="tt">${title}</div><div class="dd">${desc}</div></div>
    ${sw(key, on)}</div>`;
}

function render(s) {
  st = s;
  const c = s.config, master = c.enabled;

  document.getElementById('master').innerHTML =
    row(TOGGLES[0][0], TOGGLES[0][1], TOGGLES[0][2], master, false);
  document.getElementById('rows').innerHTML =
    TOGGLES.slice(1).map(t => row(t[0], t[1], t[2], c[t[0]] && master, !master)).join('');

  document.getElementById('status').innerHTML = s.installed
    ? '<span class="pill ok"><span class="dot"></span>Instalado en Claude Code</span>'
    : '<span class="pill"><span class="dot"></span>Todavia no instalado</span>';

  const act = document.getElementById('action');
  act.textContent = s.installed ? 'Reinstalar / reparar' : 'Instalar en Claude Code';
  document.getElementById('unbtn').style.display = s.installed ? 'block' : 'none';

  // no se pisa lo que el usuario esta tipeando
  const md = document.getElementById('m_done'), mw = document.getElementById('m_wait');
  if (document.activeElement !== md) md.value = s.msg_done;
  if (document.activeElement !== mw) mw.value = s.msg_waiting;

  document.getElementById('note').innerHTML = s.installed
    ? 'Se guarda solo. <a onclick="pywebview.api.open_folder()">Abrir carpeta de datos</a>'
    : 'Aplica al Desktop y al CLI (cmd y PowerShell),<br>porque los tres leen el mismo settings.json.';
}

function bindText(el, key) {
  let t = null;
  el.addEventListener('input', () => {         // se guarda solo, con respiro
    clearTimeout(t);
    t = setTimeout(() => pywebview.api.set_text(key, el.value), 500);
  });
  el.addEventListener('blur', () => pywebview.api.set_text(key, el.value).then(render));
}

function flip(key) {
  if (key !== 'enabled' && !st.config.enabled) return;   // todo apagado
  pywebview.api.set_toggle(key, !st.config[key]).then(render);
}

document.getElementById('action').onclick = function () {
  this.textContent = 'Instalando...';
  pywebview.api.install().then(r => {
    render(r);
    if (!r.ok) document.getElementById('note').innerHTML = 'Error: ' + r.error;
    else document.getElementById('note').innerHTML =
      'Listo. Si Claude Code ya estaba abierto,<br>reinicialo para que tome los hooks.';
  });
};
document.getElementById('unbtn').onclick = function () {
  pywebview.api.uninstall().then(render);
};

window.addEventListener('pywebviewready', () => {
  document.getElementById('pet').style.backgroundImage = 'url(data:image/png;base64,SPRITE)';
  bindText(document.getElementById('m_done'), 'msg_done');
  bindText(document.getElementById('m_wait'), 'msg_waiting');
  pywebview.api.state().then(render);
});
</script></body></html>
"""


def run():
    import webview

    b64, n, w, h = sprite_strip()
    # el sprite entero no entra: se muestra al 60%, y el ancho de la tira se
    # deriva del ancho ya redondeado para que cada paso avance exacto un frame
    pet_w = round(w * 0.6)
    pet_h = round(h * 0.6)
    html = (HTML
            .replace("TOGGLES_JSON", json.dumps(TOGGLES))
            .replace("FRAMES", str(n))
            .replace("STRIPW", str(pet_w * n))
            .replace("PETW", str(pet_w))
            .replace("PETH", str(pet_h))
            .replace("SPRITE", b64))

    # pywebview devuelve ~40px menos de lo pedido (borde de WebView2), asi que se
    # pide de mas para que el boton de instalar entre entero. Pero se recorta a
    # la pantalla: en un portatil de 1366x768 una ventana de 888 se sale por
    # abajo, y como no es redimensionable el boton quedaria inalcanzable (el
    # scroll del body no ayuda, lo que sobresale es la ventana misma).
    try:
        screen_h = webview.screens[0].height
    except (IndexError, AttributeError):
        screen_h = 1080
    height = max(560, min(888, screen_h - 90))

    global _WINDOW
    _WINDOW = webview.create_window(
        "Bichito", html=html, js_api=Api(),
        width=446, height=height, resizable=False,
        frameless=True, easy_drag=True, background_color="#17140F",
    )
    webview.start()
