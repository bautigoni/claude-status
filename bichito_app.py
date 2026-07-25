"""Punto de entrada unico de la app.

    Bichito.exe            -> panel de control
    Bichito.exe --pet      -> la ventanita flotante
    Bichito.exe --install  -> instala sin abrir la UI

Los imports pesados van adentro de cada rama a proposito: nada que no se use en
el camino elegido llega a cargarse.
"""
import sys
import traceback


def main():
    args = sys.argv[1:]
    if "--pet" in args:
        import bichito_pet
        bichito_pet.run()
    elif "--install" in args:
        import bichito_install
        bichito_install.install()
    elif "--uninstall" in args:
        import bichito_install
        bichito_install.uninstall()
    else:
        import bichito_panel
        bichito_panel.run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import time
        import bichito_core as core
        with open(core.data_path("bichito.log"), "a", encoding="utf-8") as fh:
            fh.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} argv={sys.argv}\n")
            traceback.print_exc(file=fh)
        sys.exit(1)
