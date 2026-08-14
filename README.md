# Bichito

Mascota flotante para Claude Code: te muestra si está trabajando, si te está
esperando o si ya terminó, sin que tengas que mirar la terminal. Viene con un
panel para prender y apagar cada cosa.

**Hay un bichito por sesión de Claude abierta**, en fila y con el nombre del
proyecto abajo. Con seis Claude a la vez, saber que "algo" te está esperando no
sirve de nada: lo que necesitás es saber **cuál**. El que te espera queda a todo
color y el resto se apaga; clickeando uno vas a la terminal de esa sesión.

Se achican solos según cuántos haya (uno solo se ve a tamaño completo) y cuando
cerrás un Claude, su bichito se va: el hook borra el archivo de estado en
`SessionEnd`.

| Estado | Se ve | Lo dispara |
|---|---|---|
| **Cocinando** + cronómetro | saltea un panqueque | `UserPromptSubmit`, `Pre/PostToolUse` |
| **Te espera** + voz | salta al centro de la pantalla | te hace una pregunta, o pide permiso |
| **Listo** + tiempo congelado | festeja con chispas (~2,4s) | `Stop` |
| dormido, sin texto | achatado, con zzz | después del festejo |
| solo voz, no se mueve | se queda donde está | terminó un agente en segundo plano |

El salto al centro es **solo para cuando Claude está frenado esperándote**. Que
termine un agente, que se autentique o que pasen 60s sin que escribas no lo
mueven de su lugar. Y si igual te estorba, arrastralo: mientras dure esa espera
se queda donde lo dejaste.

## Instalar

```bash
dist\Bichito\Bichito.exe
```

Abrís el exe y tocás **Instalar en Claude Code**. Se copia a
`%LOCALAPPDATA%\Bichito\app`, deja un acceso directo en el menú Inicio y escribe
los hooks. Aplica al **Desktop, a cmd y a PowerShell** a la vez, porque los tres
leen el mismo `%USERPROFILE%\.claude\settings.json`.

**WSL queda afuera**: tiene su propio `~/.claude` en el sistema de archivos Linux
y un comando `C:/...` no se ejecutaría ahí.

## Usar el bichito

- **Icono en la bandeja del sistema**: desde ahí abrís el panel, mostrás o
  escondés la ventanita, o salís del todo. En Windows 11 los iconos nuevos
  arrancan en el **desbordamiento** (la flechita ⌃ al lado del reloj): arrastralo
  fuera para dejarlo fijo.
- **Arrastrar** con el botón izquierdo lo mueve, y ahí queda para la próxima.
- **Un click** (sin arrastrar) sobre un bichito trae al frente la terminal de
  **esa** sesión, aunque esté minimizada. Si tus sesiones son pestañas de la
  misma ventana de terminal, va a levantar esa ventana pero no puede cambiar de
  pestaña: Windows no expone las pestañas como ventanas.
  El hook guarda la cadena de procesos hasta la terminal en el archivo de
  estado; el bichito se queda con el primer ancestro que tenga ventana visible,
  porque Claude Code y la shell no tienen ventana propia adentro de Windows
  Terminal o de VS Code.
- **Botón derecho** sobre el bichito abre el mismo menú.
- La ventana no tiene entrada en la barra de tareas ni sale con alt-tab: la
  bandeja es la puerta de entrada.
- Los clicks sobre las zonas transparentes pasan de largo a la ventana de atrás.

## Qué dice la voz

Dos mensajes distintos, editables desde el panel:

| Cuándo | Por defecto |
|---|---|
| termina | `El proyecto {proyecto} terminó` |
| te pregunta o pide permiso | `El proyecto {proyecto} está esperando tu respuesta` |

`{proyecto}` se reemplaza por el nombre de la carpeta. Si dejás un campo vacío
vuelve al de fábrica.

El audio se cachea por **hash del texto**, no por proyecto: si la clave fuera el
proyecto, el primer cambio de mensaje seguiría reproduciendo el audio viejo para
siempre.

## El panel

Ocho interruptores: general, bichito flotante, voz, cronómetro, saltar al centro,
siempre encima, arranque automático y dos de % de uso del plan (5h y semanal).

Todos tienen efecto **al instante**, sin reiniciar Claude Code. Eso es a propósito
y define la arquitectura: `settings.json` se escribe **una sola vez** al instalar,
con hooks fijos que siempre apuntan a `bichito-hook.exe`. Los interruptores viven
en `%LOCALAPPDATA%\Bichito\config.json` y el hook los lee en cada llamada. Así:

- prender y apagar no requiere cirugía sobre un archivo que también es tuyo;
- la voz se apaga sin borrarle el hook a nadie;
- desinstalar es una sola operación bien definida.

Los hooks ajenos se respetan. Al instalar solo se sacan los propios y el llamado
directo al script de voz (que pasa a estar gobernado por el panel), y al
desinstalar ese llamado se restituye tal cual estaba. Hay backup en
`~/.claude/settings.json.bak-bichito`.

## % del plan (5h y semanal)

Dos interruptores independientes. Cada uno muestra el % de su ventana, con la
barra coloreada (`warn` desde 70%, `crit` desde 90%) y la cuenta regresiva al
reset (`Reinicia en 2h 14m`). Apagados por defecto: si no los prendés, Bichito
sigue comportándose exactamente como antes.

| Dónde | Qué se ve |
|---|---|
| **Panel** | dos tarjetas grandes, lado a lado cuando ambas están prendidas, full-width si solo una |
| **Mascota** | una línea chiquita arriba del label principal (`5h 45%  sem 28%`), en color del bichito. Si el principal está vacío (dormido), el % ocupa todo el espacio |

**De dónde sale el dato.** Igual que
[Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter) (1.9k stars, el repo
canónico para esto): se manda una llamada mínima a `api.anthropic.com/v1/messages`
con Haiku (`max_tokens: 1`, **cuesta un token, básicamente gratis**) y se leen los
headers `anthropic-ratelimit-unified-5h-utilization` y `-7d-utilization` de la
respuesta. Es el mismo método que el `/usage` de Claude Code usa por dentro; no
es zona gris, no consulta endpoints no oficiales.

**Token.** Se lee solo de `~/.claude/.credentials.json` (el OAuth de `claude
login`). No se sube a ningún lado, no se loguea, no se persiste fuera de la
llamada HTTP que ya hiciste. Si el token no está o expiró, las tarjetas del
panel muestran el hint correspondiente:

| Error | Mensaje |
|---|---|
| `no_token` | `Hace falta claude login para leer el plan.` |
| `http_401` | `Token expirado. Volve a claude login.` |
| `no_headers` | `Tu plan no expone los headers de uso (enterprise u overage).` |
| `network` | `Sin conexion con Anthropic.` |

La mascota, en cambio, **prefiere callar** en esos casos: muestra el % solo
cuando hay dato fresco válido. Si el plan no expone esos headers (planes
enterprise u overage) las tarjetas del panel lo aclaran explícitamente; ahí
queda en manos del usuario.

**Polling.** Un thread en background dentro de bichito (`bichito_usage.Poller`)
hace un fetch cada 60s y escribe `state/usage.json` de forma atómica. El panel
y la mascota leen ese archivo. Si los dos procesos están abiertos a la vez hay
dos threads haciendo fetch, pero es un request cada 60s y el archivo es chico;
no vale la pena un proceso separado.

## Cómo está armado

```
hooks de Claude Code  ->  bichito-hook.exe  ->  state/<sesión>.json  ->  el bichito
                              (Rust)                                    (lee cada 250ms)
```

**Por qué dos binarios.** El hook corre en cada llamada a herramienta, cientos de
veces por sesión. Medido en esta máquina:

| | mediana |
|---|---|
| script Python suelto | 53 ms |
| el mismo, empaquetado con PyInstaller | 249 ms |
| `bichito-hook.exe` (Rust, 266 KB) | **20 ms** |

Por eso el camino caliente es Rust y el resto (panel + ventana flotante) va en
Python empaquetado, que arranca una vez por sesión y no paga ese costo.

**Alfa real por píxel.** La ventana se dibuja con `UpdateLayeredWindow` vía
ctypes. Con la transparencia normal de tkinter (`-transparentcolor`) los bordes
suavizados del sprite quedarían con un halo del color de fondo. Requiere alfa
premultiplicado: sin premultiplicar aparece un borde negro.

**Un archivo de estado por sesión.** Con dos Claude abiertos, que uno termine no
apaga al bichito si el otro sigue trabajando.

**Guard anti-colgado.** Si apretás Esc o cerrás la terminal, `Stop` no dispara.
Por eso `Pre/PostToolUse` funcionan de latido: si pasan 15 minutos sin señal, el
bichito se duerme solo en vez de quedar cocinando para siempre. Ese caso no
festeja: el tiempo sería inventado.

## Detalles que costaron encontrar

- **`AskUserQuestion` sí emite `PreToolUse`**, pero no emite `Notification`. Sin
  el hook con matcher específico, Claude te preguntaba algo y el bichito seguía
  cocinando en silencio. `Notification` solo dispara para pedidos de permiso o
  tras 60s de inactividad, demasiado tarde para servir de aviso.
- **Bajo `Notification` entran cosas muy distintas** y el evento solo no alcanza
  para saber cuál. El payload trae `notification_type`: `permission_prompt`,
  `worker_permission_prompt`, `agent_needs_input`, `elicitation_dialog`,
  `elicitation_url_dialog` son «te necesito»; `agent_completed` es «terminó un
  agente» (solo voz); `idle_prompt`, `auth_success`, `computer_use_enter/exit`,
  `elicitation_complete`, `elicitation_response` y `push_notification` son ruido.
  Sin esa separación, terminar un agente te plantaba el bichito en el medio de la
  pantalla diciendo que esperaba una respuesta que nadie había pedido.
  La clasificación vive en el hook y **no** en un `matcher` de `settings.json`:
  Claude Code matchea `Notification` contra el `notification_type`, pero eso es
  reciente, y con una versión anterior un hook con matcher no dispararía nunca.
- **La forma del comando de hook** tiene que ser válida en cmd, PowerShell y sh a
  la vez: el primer token va **sin comillas y sin espacios**, y con **barras
  normales**. PowerShell trata una línea que arranca con comillas como un string;
  en sh la barra invertida es carácter de escape y `C:\Users\...` se destruye.
  Si tu carpeta tuviera espacios se usa el nombre corto 8.3, y si el volumen lo
  tiene deshabilitado se cae a `cmd /c`.
- **Leer stdin cuelga el proceso** si el shell hereda un stdin que nunca cierra.
  Va con timeout, si no cada hook dejaría un zombie.
- **`speak-waiting.ps1` lee el JSON del hook por stdin** para nombrar el proyecto,
  así que hay que reenviárselo. Y el pipe no funciona si se combinan
  `DETACHED_PROCESS` con `CREATE_NO_WINDOW`: son ambos flags de consola y se
  pisan.

## Archivos

| Archivo | Qué hace |
|---|---|
| `bichito_app.py` | entrada única: sin argumentos abre el panel, `--pet` la ventanita, `--install` / `--uninstall` |
| `bichito_pet.py` | la ventana flotante (layered window, cronómetro, arrastre, menú) |
| `bichito_panel.py` | el panel, UI en HTML/CSS sobre pywebview |
| `bichito_install.py` | escribe y revierte los hooks |
| `bichito_core.py` | rutas y config compartidas |
| `bichito_usage.py` | % del plan (5h y semanal): fetchea `api.anthropic.com`, escribe `state/usage.json` |
| `hook/` | el crate de Rust del camino caliente |
| `voz.ps1` | sintetiza y reproduce; toma el texto del JSON de stdin |
| `landing/` | sitio de presentación (Astro + Tailwind), ver su propio README |
| `prepare_sprites.py` | procesa los PNG originales -> `assets/` |
| `source/` | los PNG originales, copiados acá para no depender de `Downloads` |
| `build.py` | arma todo en `dist/Bichito/` |

## Recompilar

```bash
python build.py
```

Necesita Rust (`cargo`) y PyInstaller. Después hay que volver a tocar **Instalar**
en el panel para copiar la versión nueva.

## Regenerar los sprites

Los originales vienen generados por separado, así que el personaje cambia de
posición **y de tamaño** entre frames. `prepare_sprites.py` detecta el cuerpo (la
componente naranja conectada más grande, así ignora sartén, fuego, chispas y
zzz), lo escala a un tamaño fijo y lo ancla por las patas. Sin eso la animación
bailotea.

Para cambiar velocidades o descartar un frame feo, tocá `FPS` y `SOURCES` arriba
de ese script (el frame 3 de `ZZZ` está excluido: trae un artefacto suelto).
Revisá `assets/_preview.png`: tiene la mitad del fondo clara y la otra oscura,
para chequear que se lea en cualquier escritorio.

## Detalle conocido

Las "z" del estado dormido son gris oscuro en el arte original, así que sobre un
escritorio muy negro casi no se ven. El estado igual se distingue por el cuerpo
achatado y los ojos cerrados. Si te molesta, pintalas más claras en
`source/dormido/` y volvé a correr `prepare_sprites.py`.
