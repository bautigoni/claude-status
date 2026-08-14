# Landing del bichito

Sitio estático con Astro 5 + Tailwind 4.

```bash
npm install
npm run dev      # desarrollo en localhost:4321
npm run build    # sale en dist/
```

Los sprites de `public/sprites/` los genera el proyecto padre a partir de
`assets/`: son las mismas tiras horizontales que usa el panel de la app, y se
animan en CSS con `steps()`. Si regenerás los sprites, volvé a copiarlos.

`meta.json` lleva la cantidad de frames y los fps de cada estado; el componente
`Sprite.astro` calcula de ahí el ancho de la tira. El ancho se deriva del ancho
del frame ya escalado y no de `background-size: auto`, porque redondear la altura
desalinearía los pasos y la animación iría derivando un píxel por vuelta.

Tailwind 4 entra como plugin de Vite (`@tailwindcss/vite`), no como integración
de Astro: esa quedó para la v3.

## Publicar

El sitio vive en `bichito.bauhub.online`, que sirve **Caddy** desde
`/var/www/bichito` en la VM de Oracle (host `oracle` del `~/.ssh/config`). No hay
CI: se sube a mano.

```bash
npm run build
tar -czf /tmp/sitio.tgz --exclude=Bichito.zip -C dist .
scp /tmp/sitio.tgz oracle:/tmp/sitio.tgz
ssh oracle 'rm -rf /tmp/nuevo && mkdir /tmp/nuevo &&
  tar -xzf /tmp/sitio.tgz -C /tmp/nuevo &&
  rm -rf /var/www/bichito/_astro /var/www/bichito/fonts &&
  cp -a /tmp/nuevo/. /var/www/bichito/'
```

`_astro/` y `fonts/` se borran antes de copiar porque los nombres llevan hash y
si no se van apilando los viejos. `Bichito.zip` va aparte (son 30 MB) y conviene
subirlo con otro nombre y recién ahí moverlo, para que nadie se baje un archivo
a medio subir:

```bash
scp dist/Bichito.zip oracle:/var/www/bichito/Bichito.zip.nuevo
ssh oracle 'mv /var/www/bichito/Bichito.zip.nuevo /var/www/bichito/Bichito.zip'
```

El `.zip` se rearma desde el paquete de la app (`dist/Bichito/` del proyecto
padre, con todo colgando de `Bichito/`) y se guarda en `public/Bichito.zip`: el
peso que muestra la página lo lee del archivo en tiempo de build.
