"""Punto de entrada unico de la app.

    Bichito.exe            -> panel de control
    Bichito.exe --pet      -> la ventanita flotante
    Bichito.exe --install  -> instala sin abrir la UI

Los imports pesados van adentro de cada rama a proposito: nada que no se use en
el camino elegido llega a cargarse.

El import explicito de los bichito_* en el top-level es para PyInstaller: con
ellos dentro de main() el analisis estatico no los ve y el bundle sale sin
panel/pet/install/core/usage. Los modulos ya no se cargan solos en el import
(gracias al branch de abajo), asi que el costo es solo unos milisegundos al
arranque y que todos queden disponibles en sys.modules.
"""
import sys
import traceback

import bichito_core
import bichito_install
import bichito_panel
import bichito_pet
import bichito_usage


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
