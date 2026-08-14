"""Arma el paquete distribuible en dist/Bichito/.

    python build.py

Junta tres cosas:
  1. bichito-hook.exe  (Rust)  -> lo llaman los hooks en cada tool call
  2. Bichito.exe       (PyInstaller onedir) -> panel + bichito flotante
  3. assets/           -> los sprites

Onedir y no onefile a proposito: onefile descomprime todo el bundle en cada
arranque. Medido: 249ms de mediana por invocacion, contra 53ms del script
suelto. Por eso ademas el camino caliente lo hace el binario de Rust (19,7ms).
"""
import os
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(BASE, "dist", "Bichito")
HOOK_EXE = os.path.join(BASE, "hook", "target", "release", "bichito-hook.exe")


def run(cmd, cwd=None):
    print(f"  $ {' '.join(str(c) for c in cmd[:3])} ...")
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        raise SystemExit(f"fallo: {cmd[0]}")
    return r


def make_icon():
    """Icono de la app a partir de un frame del bichito."""
    from PIL import Image
    src = os.path.join(BASE, "assets", "esperando", "00.png")
    if not os.path.exists(src):
        return None
    img = Image.open(src).convert("RGBA")
    bb = img.split()[3].point(lambda v: 255 if v > 16 else 0).getbbox()
    img = img.crop(bb)
    side = max(img.size)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.alpha_composite(img, ((side - img.width) // 2, (side - img.height) // 2))
    out = os.path.join(BASE, "bichito.ico")
    # NEAREST en cada tamano: es pixel art, un filtro suave lo emborrona
    sq.save(out, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    return out


def main():
    if not os.path.isdir(os.path.join(BASE, "assets")):
        print("[1/4] generando sprites")
        run([sys.executable, os.path.join(BASE, "prepare_sprites.py")])
    else:
        print("[1/4] sprites ya generados")

    print("[2/4] compilando bichito-hook.exe (Rust)")
    run(["cargo", "build", "--release"], cwd=os.path.join(BASE, "hook"))

    print("[3/4] empaquetando Bichito.exe (PyInstaller)")
    icon = make_icon()
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onedir", "--windowed", "--name", "Bichito",
        "--add-data", f"{os.path.join(BASE, 'assets')}{os.pathsep}assets",
        "--collect-all", "webview",
        "--collect-all", "pystray",
        "--hidden-import", "pystray._win32",
        # bichito_app.py los importa dentro de funciones, asi que PyInstaller
        # no los ve. Sin esto el bundle sale sin bichito_panel/bichito_pet
        # (y por lo tanto sin bichito_usage, bichito_core y bichito_install).
        "--hidden-import", "bichito_pet",
        "--hidden-import", "bichito_panel",
        "--hidden-import", "bichito_install",
        "--hidden-import", "bichito_core",
        "--hidden-import", "bichito_usage",
        "--exclude-module", "numpy",
        "--exclude-module", "pytest",
    ]
    if icon:
        cmd += ["--icon", icon]
    cmd.append(os.path.join(BASE, "bichito_app.py"))
    run(cmd, cwd=BASE)

    print("[4/4] copiando el hook y la voz al paquete")
    shutil.copy2(HOOK_EXE, os.path.join(DIST, "bichito-hook.exe"))
    # voz.ps1 va suelto junto al exe, no dentro del bundle: el hook lo invoca
    # por ruta desde la carpeta de instalacion
    shutil.copy2(os.path.join(BASE, "voz.ps1"), os.path.join(DIST, "voz.ps1"))

    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(DIST) for f in fs)
    print(f"\nlisto -> {DIST}  ({size / 1024 / 1024:.1f} MB)")
    print("  Bichito.exe        panel de control")
    print("  bichito-hook.exe   lo llaman los hooks")


if __name__ == "__main__":
    main()
