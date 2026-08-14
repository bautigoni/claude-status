"""% de uso del plan de Claude: sesion 5h y semanal.

Tecnica (la misma que Clawdmeter, 1.9k stars): se manda una llamada minima a
api.anthropic.com/v1/messages con Haiku (cuesta 1 token, basicamente gratis) y
se leen los headers anthropic-ratelimit-unified-* de la respuesta. El endpoint
no expone limites, pero el rate limiter los cuenta y los devuelve en headers.

El token sale de ~/.claude/.credentials.json (OAuth). Si expira, se refresca
solo contra https://platform.claude.com/v1/oauth/token usando el refresh_token
del mismo archivo y se reescribe el credentials.json con los tokens nuevos.
El usuario no tiene que hacer `claude login` para mantener el % andando.

El poller escribe state/usage.json. El panel y la mascota lo leen de ahi, asi
no compiten por el puerto, no duplican requests mas que lo razonable, y la
mascota puede mostrar el dato aunque el panel este cerrado.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

import bichito_core as core

API_URL = "https://api.anthropic.com/v1/messages"
REFRESH_URL = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"   # el de Claude Code CLI
MODEL = "claude-haiku-4-5-20251001"
TIMEOUT = 15           # s, la llamada es chica pero el server puede tardar
INTERVAL = 60          # s entre fetches, igual que Clawdmeter
RETRY_COOLDOWN = 30    # s sin reintentar refresh si acaba de fallar (asi no spamea)

# Orden de busqueda del credentials.json. En Windows suele estar en el primero.
CRED_PATHS = [
    os.path.join(os.path.expanduser("~"), ".claude", ".credentials.json"),
    os.path.join(os.environ.get("LOCALAPPDATA", "") or "", "Claude", ".credentials.json"),
    os.path.join(os.environ.get("APPDATA", "") or "", "Claude", ".credentials.json"),
]

# Un refresh a la vez. Si dos poller arrancan a la vez y ambos ven 401, el
# segundo espera al primero en vez de invalidar el refresh_token que el primero
# acaba de rotar (los OAuth modernos rotan el refresh en cada uso).
_refresh_lock = threading.Lock()
_last_refresh_attempt = 0.0   # epoch del ultimo intento, para el cooldown


def usage_path():
    return core.data_path("state", "usage.json")


def _find_credentials_path():
    for p in CRED_PATHS:
        try:
            with open(p, encoding="utf-8-sig") as fh:
                json.load(fh)
            return p
        except (OSError, ValueError):
            continue
    return None


def _read_credentials():
    """Devuelve (path, dict) del credentials.json, o (None, None) si no existe."""
    path = _find_credentials_path()
    if not path:
        return None, None
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return path, json.load(fh)
    except (OSError, ValueError):
        return path, None


def _oauth_block(data):
    """Saca el sub-dict de claudeAiOauth sin importar en que nivel este."""
    if not isinstance(data, dict):
        return None
    for k in ("claudeAiOauth", "oauth", "credentials"):
        v = data.get(k)
        if isinstance(v, dict) and "accessToken" in v:
            return v
    return None


def _read_token():
    _, data = _read_credentials()
    if not data:
        return None
    oauth = _oauth_block(data)
    if oauth and isinstance(oauth.get("accessToken"), str):
        return oauth["accessToken"]
    if isinstance(data.get("accessToken"), str):
        return data["accessToken"]
    return None


def _refresh(refresh_token):
    """Pide un access_token nuevo. Devuelve el dict OAuth actualizado o None.

    El endpoint de Anthropic es de OAuth2 estandar, con grant_type=refresh_token.
    El body va en JSON (no form-encoded): la API rechaza lo otro. El client_id
    es el de Claude Code CLI.
    """
    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
    }).encode("utf-8")
    req = urllib.request.Request(REFRESH_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "User-Agent": "claude-code/2.1.5",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return None


def _looks_like_real_token(s):
    """Validacion minima: los tokens de Anthropic arrancan con sk-ant-oat01-
    (access) o sk-ant-ort01- (refresh). Si la respuesta del refresh no trae
    algo que arranque con esos prefijos, NO lo persistimos: seria tirar el
    credentials.json a la basura. Mejor devolver 401 persistente y que el
    usuario re-logee."""
    if not isinstance(s, str) or not s:
        return False
    return s.startswith("sk-ant-oat01-") or s.startswith("sk-ant-ort01-")


def _persist_refreshed(new_oauth):
    """Escribe los tokens nuevos al credentials.json preservando el resto.

    Se hace en dos pasos: relectura del archivo (pudo cambiar entre nuestro
    read y el refresh) + escritura. Asi no pisamos un cambio legitimo de
    Claude Code que se haya hecho en el medio.

    Antes de escribir valida que los tokens tengan el formato esperado de
    Anthropic. Si no, NO escribe nada y devuelve False: el credentials.json
    del usuario es sagrado y romperlo lo deja sin sesion.
    """
    new_access = new_oauth.get("access_token")
    new_refresh = new_oauth.get("refresh_token")
    if not _looks_like_real_token(new_access):
        return False
    # el refresh_token puede no venir en la respuesta (algunas implementaciones
    # no lo rotan en cada uso); si viene, validamos igual
    if new_refresh is not None and not _looks_like_real_token(new_refresh):
        return False
    path, data = _read_credentials()
    if not path or not isinstance(data, dict):
        return False
    oauth = _oauth_block(data)
    if oauth is None:
        return False
    oauth["accessToken"] = new_access
    if new_refresh:
        oauth["refreshToken"] = new_refresh
    expires_in = new_oauth.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        oauth["expiresAt"] = int(time.time() * 1000) + int(expires_in * 1000)
    refresh_expires_in = new_oauth.get("refresh_token_expires_in")
    if isinstance(refresh_expires_in, (int, float)) and refresh_expires_in > 0:
        oauth["refreshTokenExpiresAt"] = int(time.time() * 1000) + int(refresh_expires_in * 1000)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _maybe_refresh():
    """Si la ultima vez fallo el refresh, espera el cooldown y devuelve False.
    Asi, con un 401 persistente (p.ej. refresh_token muerto), no se hacen 60
    refresh por hora: 1 cada RETRY_COOLDOWN segundos."""
    global _last_refresh_attempt
    with _refresh_lock:
        if time.time() - _last_refresh_attempt < RETRY_COOLDOWN:
            return False
        _, data = _read_credentials()
        oauth = _oauth_block(data) if data else None
        if not oauth or not isinstance(oauth.get("refreshToken"), str):
            _last_refresh_attempt = time.time()
            return False
        new = _refresh(oauth["refreshToken"])
        _last_refresh_attempt = time.time()
        if not new or "access_token" not in new:
            return False
        return _persist_refreshed(new)


def fetch():
    """Devuelve el snapshot actual. ok=True con five_hour/seven_day, o ok=False
    con error en formato corto. Nunca lanza: cualquier excepcion se traduce a
    un error categorizado para que la UI pueda mostrar el hint correcto."""
    ts = time.time()
    token = _read_token()
    if not token:
        return {"ok": False, "error": "no_token", "ts": ts}

    def _do(token_):
        body = json.dumps({
            "model": MODEL,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }).encode("utf-8")
        req = urllib.request.Request(API_URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {token_}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20",
            "Content-Type": "application/json",
            "User-Agent": "claude-code/2.1.5",
        })
        return urllib.request.urlopen(req, timeout=TIMEOUT)

    try:
        try:
            resp = _do(token)
        except urllib.error.HTTPError as e:
            # 401 = token vencido o invalido. Si hay refresh_token, intentamos
            # refrescar y reintentar una vez. Si despues del refresh sigue 401,
            # ya es problema del token y reportamos el segundo error.
            if e.code == 401 and _maybe_refresh():
                token = _read_token()
                if token:
                    try:
                        resp = _do(token)
                    except urllib.error.HTTPError as e2:
                        return {"ok": False, "error": f"http_{e2.code}", "ts": ts}
                else:
                    return {"ok": False, "error": "no_token", "ts": ts}
            else:
                return {"ok": False, "error": f"http_{e.code}", "ts": ts}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return {"ok": False, "error": "network", "ts": ts, "detail": str(e)}
    except Exception as e:
        return {"ok": False, "error": "network", "ts": ts, "detail": str(e)}

    headers = resp.headers
    five_util = headers.get("anthropic-ratelimit-unified-5h-utilization")
    if not five_util:
        # plan enterprise u overage: Clawdmeter lo maneja con fallback, pero
        # Bichito solo soporta pro/max por ahora, asi que reportamos y listo
        return {"ok": False, "error": "no_headers", "ts": ts}

    def pct(h, default="0"):
        try:
            return int(round(float(headers.get(h, default)) * 100))
        except ValueError:
            return 0

    def reset(h, default="0"):
        try:
            return float(headers.get(h, default))
        except ValueError:
            return 0.0

    return {
        "ok": True,
        "ts": ts,
        "five_hour": {
            "pct": pct("anthropic-ratelimit-unified-5h-utilization"),
            "reset_at": reset("anthropic-ratelimit-unified-5h-reset"),
            "status": headers.get("anthropic-ratelimit-unified-5h-status", "unknown"),
        },
        "seven_day": {
            "pct": pct("anthropic-ratelimit-unified-7d-utilization"),
            "reset_at": reset("anthropic-ratelimit-unified-7d-reset"),
            "status": headers.get("anthropic-ratelimit-unified-7d-status", "unknown"),
        },
    }


def write(data):
    """Atomic write. El panel/mascota pueden estar leyendo en cualquier
    momento, asi que se escribe a un .tmp y se hace os.replace (atomico en
    la misma carpeta)."""
    try:
        os.makedirs(os.path.dirname(usage_path()), exist_ok=True)
        tmp = usage_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, usage_path())
    except OSError:
        pass


def read():
    try:
        with open(usage_path(), encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


class Poller:
    """Background thread que actualiza state/usage.json cada INTERVAL segundos.

    Lo levantan tanto el panel como la mascota al arrancar. Cuando los dos estan
    abiertos hay dos poller corriendo y hacen doble fetch, pero son 2 req cada
    60s y el state/usage.json es chico; no vale la pena un proceso separado.
    """

    def __init__(self):
        self._stop = threading.Event()
        self._t = None

    def start(self):
        if self._t and self._t.is_alive():
            return
        self._stop.clear()
        self._t = threading.Thread(target=self._loop, daemon=True, name="usage-poller")
        self._t.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                write(fetch())
            except Exception:
                # no se re-lanza: este thread no puede matar al proceso
                pass
            # sleep interrumpible: si stop() llega en medio, salimos ya
            self._stop.wait(INTERVAL)
