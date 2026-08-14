"""Rutas y configuracion compartidas por el bichito y el panel.

Dos raices distintas a proposito:

  resource_path()  -> de donde se LEE (sprites). Cambia al empaquetar: en un
                      bundle de PyInstaller no existe __file__ como uno espera.
  data_dir()       -> donde se ESCRIBE (config, estado, log). Siempre
                      %LOCALAPPDATA%\\Bichito, porque una vez instalada la app
                      su carpeta puede no tener permiso de escritura.

El mismo config.json lo lee bichito-hook.exe (Rust) en cada llamada a
herramienta, asi que los interruptores tienen efecto al instante: el panel
escribe el archivo y listo, sin reiniciar Claude Code ni tocar settings.json.
"""
import json
import os
import sys

APP_NAME = "Bichito"

DEFAULTS = {
    "enabled": True,          # interruptor general
    "pet": True,              # bichito flotante
    "voice": True,            # voz al terminar
    "timer": True,            # cronometro debajo del bichito
    "always_on_top": True,
    "autostart": True,        # levantarlo solo al abrir Claude Code
    "center_on_wait": True,   # saltar al centro de la pantalla mientras espera
    "voice_script": "",       # .ps1 que habla (lo pone el instalador)
    # {proyecto} se reemplaza por el nombre de la carpeta del proyecto
    "msg_done": "El proyecto {proyecto} terminó",
    "msg_waiting": "El proyecto {proyecto} está esperando tu respuesta",
    # usage del plan: apagados por defecto. Prenderlos hace que el bichito y
    # el panel muestren el % de sesion 5h y/o semanal. El token se lee solo
    # de ~/.claude/.credentials.json; si no esta, se muestra un hint pidiendo
    # `claude login` y nada se rompe.
    "plan_5h": False,
    "plan_weekly": False,
}


def resource_path(*parts):
    """Ruta a un recurso de solo lectura (sprites), congelado o no."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


def data_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def data_path(*parts):
    return os.path.join(data_dir(), *parts)


def state_dir():
    d = data_path("state")
    os.makedirs(d, exist_ok=True)
    return d


def config_path():
    return data_path("config.json")


def load_config():
    cfg = dict(DEFAULTS)
    try:
        # utf-8-sig: si el archivo se toco desde PowerShell viene con BOM y un
        # utf-8 pelado lo rechazaria, perdiendo toda la configuracion
        with open(config_path(), encoding="utf-8-sig") as fh:
            cfg.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    merged = dict(DEFAULTS)
    merged.update(cfg)
    tmp = config_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2)
    os.replace(tmp, config_path())  # atomico: el hook nunca lee un archivo a medias
    return merged
