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
