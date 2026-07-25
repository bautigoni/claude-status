"""Prepara los sprites del bichito a partir de los PNG originales.

Los frames vienen generados por separado, asi que el personaje cambia de
posicion Y de tamano entre uno y otro. Si se recortaran todos con el mismo
encuadre la animacion bailotearia. Entonces, por cada frame:

  1. Se detecta el CUERPO (la componente naranja conectada mas grande). Asi se
     ignoran la sarten, el fuego, el panqueque, las chispas y las zzz.
  2. Se escala el frame para que el cuerpo mida siempre lo mismo. Se normaliza
     por la media geometrica de ancho y alto, no por el ancho solo, para no
     aplastar las poses que ya vienen achatadas a proposito (el salto, dormir).
  3. Se pega anclando el cuerpo por abajo y al centro: las patas quedan
     plantadas y lo que se mueve son los accesorios.

Se baja de escala con BOX (promedio de area), no NEAREST: el original ya viene
con antialias y NEAREST se comeria las lineas de 1px del contorno.

    python prepare_sprites.py   ->   assets/<estado>/NN.png + assets/manifest.json
"""
import json
import os
import shutil
from collections import deque

from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(BASE, "source")
ASSETS = os.path.join(BASE, "assets")
DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")

# estado -> (carpeta original, modo de reproduccion, frames a usar o None=todos)
SOURCES = {
    "cocinando": (os.path.join(DOWNLOADS, "Cooking"), "pingpong", None),
    "esperando": (os.path.join(DOWNLOADS, "Esperando"), "pingpong", None),
    "termino": (os.path.join(DOWNLOADS, "Festejo"), "once", None),
    # el frame 3 trae un artefacto suelto (una linea gris abajo a la izquierda)
    "dormido": (os.path.join(DOWNLOADS, "ZZZ"), "pingpong", [1, 2, 4, 5]),
}

# termino son 5 frames y se reproducen una sola vez: a 3fps el festejo dura
# ~1.7s y llena la ventana de CELEBRATE en vez de quedarse congelado
FPS = {"cocinando": 4, "esperando": 4, "termino": 3, "dormido": 3}

TARGET_BODY = 66     # media geometrica del cuerpo, en px finales
ALPHA_MIN = 16       # por debajo de esto es basura casi transparente
MARGIN = 3
TEXT_H = 30          # franja de abajo reservada para el cronometro


# ------------------------------------------------------- deteccion del cuerpo
def orange_mask(img):
    """Mascara de los pixeles naranjas opacos del personaje."""
    w, h = img.size
    px = img.load()
    mask = Image.new("L", (w, h), 0)
    mp = mask.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a > 128 and r > 120 and r - g > 30 and g >= b:
                mp[x, y] = 255
    return mask


def largest_blob_bbox(mask, step=8):
    """bbox de la componente conectada mas grande, buscada en baja resolucion."""
    w, h = mask.size
    sw, sh = w // step, h // step
    small = mask.resize((sw, sh), Image.BOX).point(lambda v: 1 if v > 70 else 0)
    grid = bytearray(small.tobytes())

    best, best_n = None, 0
    seen = bytearray(sw * sh)
    for start in range(sw * sh):
        if not grid[start] or seen[start]:
            continue
        q = deque([start])
        seen[start] = 1
        cells, n = [], 0
        while q:
            i = q.popleft()
            cells.append(i)
            n += 1
            x, y = i % sw, i // sw
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < sw and 0 <= ny < sh:
                    j = ny * sw + nx
                    if grid[j] and not seen[j]:
                        seen[j] = 1
                        q.append(j)
        if n > best_n:
            best_n, best = n, cells

    if not best:
        return mask.getbbox()
    xs = [i % sw for i in best]
    ys = [i // sw for i in best]
    # se vuelve a resolucion completa con un margen y se afina ahi
    pad = 2
    region = (max(0, (min(xs) - pad) * step), max(0, (min(ys) - pad) * step),
              min(w, (max(xs) + 1 + pad) * step), min(h, (max(ys) + 1 + pad) * step))
    sub = mask.crop(region).getbbox()
    if sub is None:
        return region
    return (region[0] + sub[0], region[1] + sub[1], region[0] + sub[2], region[1] + sub[3])


def content_bbox(img):
    return img.split()[3].point(lambda v: 255 if v > ALPHA_MIN else 0).getbbox()


# ------------------------------------------------------------------ tuberia
def collect(folder, keep):
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".png"))
    if keep:
        files = [files[i - 1] for i in keep]
    return [os.path.join(folder, f) for f in files]


def sequence(n, mode):
    if mode == "pingpong" and n > 2:
        return list(range(n)) + list(range(n - 2, 0, -1))
    return list(range(n))


def main():
    for d in (SOURCE, ASSETS):
        if os.path.isdir(d):
            shutil.rmtree(d)

    measured = {}
    for state, (folder, _mode, keep) in SOURCES.items():
        if not os.path.isdir(folder):
            raise SystemExit(f"No encuentro los originales en {folder}")
        os.makedirs(os.path.join(SOURCE, state), exist_ok=True)
        frames = []
        for p in collect(folder, keep):
            shutil.copy2(p, os.path.join(SOURCE, state, os.path.basename(p)))
            img = Image.open(p).convert("RGBA")
            body = largest_blob_bbox(orange_mask(img))
            bw, bh = body[2] - body[0], body[3] - body[1]
            scale = TARGET_BODY / ((bw * bh) ** 0.5)
            anchor = ((body[0] + body[2]) / 2 * scale, body[3] * scale)
            cb = content_bbox(img)
            rel = (cb[0] * scale - anchor[0], cb[1] * scale - anchor[1],
                   cb[2] * scale - anchor[0], cb[3] * scale - anchor[1])
            frames.append({"img": img, "scale": scale, "anchor": anchor, "rel": rel})
            print(f"  {os.path.basename(p):20} cuerpo={bw}x{bh} escala={scale:.3f}")
        measured[state] = frames
        print(f"{state}: {len(frames)} frames")

    # lienzo comun: la union de todo lo que sobresale respecto del ancla
    allrel = [f["rel"] for fs in measured.values() for f in fs]
    left = min(r[0] for r in allrel) - MARGIN
    top = min(r[1] for r in allrel) - MARGIN
    right = max(r[2] for r in allrel) + MARGIN
    bottom = max(r[3] for r in allrel) + MARGIN

    ow = int(round(right - left))
    oh = int(round(bottom - top)) + TEXT_H
    ax, ay = -left, -top
    print(f"\nlienzo {ow}x{oh}  ancla=({ax:.1f},{ay:.1f})  (incluye {TEXT_H}px de reloj)")

    manifest = {"size": [ow, oh], "text_y": int(round(bottom - top)) + 2, "states": {}}
    preview = []
    for state, frames in measured.items():
        out = os.path.join(ASSETS, state)
        os.makedirs(out, exist_ok=True)
        rendered = []
        for f in frames:
            img, s = f["img"], f["scale"]
            sc = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))),
                            Image.BOX)
            canvas = Image.new("RGBA", (ow, oh), (0, 0, 0, 0))
            canvas.alpha_composite(sc, (int(round(ax - f["anchor"][0])),
                                        int(round(ay - f["anchor"][1]))))
            rendered.append(canvas)

        order = sequence(len(rendered), SOURCES[state][1])
        for i, idx in enumerate(order):
            rendered[idx].save(os.path.join(out, f"{i:02d}.png"))
        manifest["states"][state] = {
            "frames": len(order), "fps": FPS[state], "mode": SOURCES[state][1],
        }
        preview.append((state, [rendered[i] for i in order]))

    with open(os.path.join(ASSETS, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    cols = max(len(f) for _, f in preview)
    sheet = Image.new("RGBA", (cols * ow, len(preview) * oh), (26, 26, 30, 255))
    ImageDraw.Draw(sheet).rectangle([0, 0, cols * ow // 2, len(preview) * oh],
                                    fill=(246, 246, 248, 255))
    for r, (_, frames) in enumerate(preview):
        for c, fr in enumerate(frames):
            sheet.alpha_composite(fr, (c * ow, r * oh))
    sheet.save(os.path.join(ASSETS, "_preview.png"))
    print("hoja de contacto -> assets/_preview.png")


if __name__ == "__main__":
    main()
