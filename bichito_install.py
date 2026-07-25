"""Instalar / desinstalar el bichito en Claude Code.

Idea central: settings.json se escribe UNA sola vez, al instalar, con un juego
fijo de hooks que apuntan siempre a bichito-hook.exe. Los interruptores del
panel NO tocan settings.json: viven en config.json y el hook los lee en cada
llamada. Ventajas:

  - prender y apagar cosas es instantaneo, sin reiniciar Claude Code
  - no hay que hacer cirugia sobre un archivo que tambien es del usuario
  - la voz se puede apagar sin borrar el hook de nadie

Los hooks ajenos se respetan: al instalar solo se sacan los mios y el llamado
directo al script de voz (que pasa a estar gobernado por el panel), y al
desinstalar ese llamado se restituye tal cual estaba.

Desktop, cmd y PowerShell leen todos el mismo %USERPROFILE%\\.claude\\settings.json,
asi que una instalacion cubre los tres. WSL queda afuera: tiene su propio
~/.claude en el sistema de archivos Linux y un comando C:/... no correria ahi.
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time

import bichito_core as core

SETTINGS = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
INSTALL_DIR = core.data_path("app")
MARKER = "bichito-hook.exe"          # con esto se reconocen los hooks propios
LEGACY = "bichito_state.py"          # de la version anterior, en Python suelto

# evento -> lista de (matcher o None, argumentos para bichito-hook.exe)
HOOKS = {
    "SessionStart": [(None, "idle --launch")],
    "UserPromptSubmit": [(None, "working")],
    "PreToolUse": [
        ("*", "working"),
        # Cuando Claude te hace una pregunta NO dispara Notification (no es un
        # pedido de permiso ni un fin de turno), asi que sin esto se queda
        # cocinando y en silencio mientras te espera.
        ("AskUserQuestion|ExitPlanMode", "waiting --voice"),
    ],
    "PostToolUse": [("*", "working")],
    "Notification": [(None, "waiting --voice")],
    "Stop": [(None, "idle --voice")],
    "SessionEnd": [(None, "idle")],
}


def hook_exe():
    return os.path.join(INSTALL_DIR, "bichito-hook.exe")


def app_exe():
    return os.path.join(INSTALL_DIR, "Bichito.exe")


def source_dir():
    """Carpeta desde la que se esta corriendo ahora."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def is_installed():
    return os.path.exists(hook_exe()) and MARKER in _read_settings_text()


def _read_settings_text():
    try:
        with open(SETTINGS, encoding="utf-8-sig") as fh:
            return fh.read()
    except OSError:
        return ""


def read_settings():
    try:
        with open(SETTINGS, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_settings(data):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    if os.path.exists(SETTINGS) and not os.path.exists(SETTINGS + ".bak-bichito"):
        shutil.copy2(SETTINGS, SETTINGS + ".bak-bichito")
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, SETTINGS)


def _short_path(path):
    """Nombre corto 8.3, para sacarle los espacios a una ruta."""
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024)
    return buf.value if n else path


def _command(args):
    """Linea de hook valida en cmd, PowerShell y sh a la vez.

    La regla (verificada en los tres): el primer token tiene que ir SIN comillas
    y sin espacios. PowerShell trata una linea que arranca con comillas como un
    string y no la ejecuta; cmd y sh necesitan las comillas si hay espacios.
    Si el usuario tiene espacios en su carpeta (C:/Users/Juan Perez/...), se usa
    el nombre corto 8.3; y si el volumen lo tiene deshabilitado, se cae a cmd /c.
    """
    exe = hook_exe()
    if " " in exe:
        exe = _short_path(exe)
    if " " in exe:
        return f'cmd /c ""{exe}" {args}"'
    # barras normales, no invertidas: en sh la barra invertida es caracter de
    # escape y C:\Users\... se convierte en C:Users...
    return f"{exe.replace(chr(92), '/')} {args}"


def _is_mine(entry):
    cmd = entry.get("command", "")
    return MARKER in cmd or LEGACY in cmd


def _is_voice(entry, voice_script):
    if not voice_script:
        return False
    name = os.path.basename(voice_script)
    return name and name in entry.get("command", "")


def detect_voice_script(hooks):
    """Busca un .ps1 llamado desde los hooks actuales: es la voz que ya tenia."""
    for groups in hooks.values():
        for group in groups or []:
            for entry in group.get("hooks", []) or []:
                cmd = entry.get("command", "")
                if ".ps1" in cmd.lower():
                    for token in cmd.replace('"', " ").split():
                        if token.lower().endswith(".ps1"):
                            return token.replace("\\", "/")
    return ""


def stop_running():
    """Cierra las instancias que corren DESDE la carpeta de instalacion.

    Sin esto, reinstalar con el bichito activo revienta con "el archivo esta
    siendo utilizado por otro proceso": los exe y las DLL estan bloqueados.
    Se excluye el proceso actual, que puede ser el propio panel.
    """
    ps = (f"Get-CimInstance Win32_Process -Filter \"Name='Bichito.exe'\" | "
          f"Where-Object {{ $_.ExecutablePath -like '{INSTALL_DIR}*' -and "
          f"$_.ProcessId -ne {os.getpid()} }} | "
          f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       creationflags=0x08000000, timeout=20, check=False)
        time.sleep(1.2)   # que Windows suelte los handles
    except (OSError, subprocess.SubprocessError):
        pass


def install():
    """Copia la app, escribe los hooks y deja la config lista."""
    src = source_dir()
    if os.path.normcase(src) != os.path.normcase(INSTALL_DIR):
        stop_running()
        os.makedirs(INSTALL_DIR, exist_ok=True)
        for item in os.listdir(src):
            s, d = os.path.join(src, item), os.path.join(INSTALL_DIR, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)

    data = read_settings()
    hooks = data.get("hooks") or {}
    cfg = core.load_config()

    # El .ps1 que el usuario ya tenia enganchado. Hay que recordarlo: sirve para
    # sacarle el llamado directo (la voz pasa a gobernarla el panel) y para
    # restituirselo al desinstalar. En una reinstalacion ya no se puede detectar
    # de los hooks, porque para entonces los reemplazamos por los nuestros.
    legacy = cfg.get("legacy_voice") or detect_voice_script(hooks)
    cfg["legacy_voice"] = legacy

    # snapshot del estado previo, una sola vez: es lo que permite desinstalar
    # dejando settings.json como estaba
    if "hooks_backup" not in cfg:
        cfg["hooks_backup"] = json.loads(json.dumps(hooks))

    for event, specs in HOOKS.items():
        groups = hooks.get(event) or []
        kept = []
        for group in groups:
            entries = [e for e in (group.get("hooks") or [])
                       if not _is_mine(e) and not _is_voice(e, legacy)]
            if entries:                       # se respetan los hooks ajenos
                kept.append({**group, "hooks": entries})
        for matcher, args in specs:
            mine = {"type": "command", "command": _command(args), "async": True}
            group = {"hooks": [mine]}
            if matcher:
                group["matcher"] = matcher
            kept.append(group)
        hooks[event] = kept

    data["hooks"] = hooks
    write_settings(data)

    # A partir de aca habla nuestro voz.ps1, que toma el texto del JSON de stdin
    # y por eso permite mensajes distintos y configurables. El del usuario queda
    # intacto en disco y se le devuelve el hook al desinstalar.
    cfg["voice_script"] = os.path.join(INSTALL_DIR, "voz.ps1").replace("\\", "/")
    core.save_config(cfg)
    make_shortcut()
    return True


def uninstall():
    """Saca los hooks propios y devuelve el llamado a la voz como estaba."""
    data = read_settings()
    hooks = data.get("hooks") or {}
    cfg = core.load_config()
    backup = cfg.get("hooks_backup") or {}

    for event in list(hooks):
        groups = []
        for group in hooks.get(event) or []:
            entries = [e for e in (group.get("hooks") or []) if not _is_mine(e)]
            if entries:
                groups.append({**group, "hooks": entries})
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)

    # restituir lo que habia antes y no sobrevivio (el llamado directo a la voz).
    # Se saltean los propios: el backup pudo tomarse cuando ya habia una version
    # anterior del bichito instalada, y restaurarla seria dejar basura colgada.
    for event, groups in backup.items():
        existing = json.dumps(hooks.get(event) or [])
        for group in groups or []:
            for entry in group.get("hooks") or []:
                if _is_mine(entry):
                    continue
                if entry.get("command", "") not in existing:
                    hooks.setdefault(event, []).append({"hooks": [entry]})

    data["hooks"] = hooks
    write_settings(data)
    cfg.pop("hooks_backup", None)
    core.save_config(cfg)
    return True


def make_shortcut():
    """Acceso directo en el menu Inicio, via WScript.Shell (sin dependencias)."""
    start = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                         "Start Menu", "Programs")
    if not os.path.isdir(start):
        return False
    lnk = os.path.join(start, "Bichito.lnk")
    ps = (f"$s=(New-Object -COM WScript.Shell).CreateShortcut('{lnk}');"
          f"$s.TargetPath='{app_exe()}';"
          f"$s.WorkingDirectory='{INSTALL_DIR}';$s.Save()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       creationflags=0x08000000, timeout=20, check=False)
        return os.path.exists(lnk)
    except (OSError, subprocess.SubprocessError):
        return False
