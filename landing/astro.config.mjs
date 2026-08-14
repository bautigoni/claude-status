// @ts-check
import { defineConfig } from "astro/config";
import tailwindcss from "@tailwindcss/vite";

// Tailwind v4 entra como plugin de Vite, no como integracion de Astro:
// la integracion @astrojs/tailwind quedo para la v3.
export default defineConfig({
  site: "https://bichito.bauhub.online",
  vite: { plugins: [tailwindcss()] },
});
