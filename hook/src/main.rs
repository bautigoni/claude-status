//! Puente entre los hooks de Claude Code y el bichito.
//!
//! Los hooks de settings.json se escriben UNA vez al instalar y no se tocan mas.
//! Todos los interruptores viven en config.json y se leen aca, en cada llamada:
//! asi el panel puede prender y apagar cosas al instante, sin reiniciar Claude
//! Code y sin editar un archivo que tambien es del usuario.
//!
//!     bichito-hook.exe working|waiting|idle|notify|fin [--launch] [--voice]
//!
//! `fin` borra el archivo de estado: la sesion se cerro y su bichito se va.
//!
//! `notify` es el del evento Notification: ahi el estado no lo decide argv sino
//! el campo `notification_type` del payload, porque bajo el mismo evento entran
//! cosas muy distintas (te pide permiso / termino un agente / se autentico).

#![windows_subsystem = "windows"] // sin consola: si no, parpadea una ventana negra

use std::fs;
use std::io::{Read, Write};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const DETACHED_PROCESS: u32 = 0x0000_0008;
const STDIN_TIMEOUT: Duration = Duration::from_millis(400);
/// Segundos que un "waiting" recien escrito resiste que lo pisen con "working".
const WAITING_HOLD: f64 = 3.0;

// ------------------------------------------------------- la ventana del turno
// Para que hacerle click al bichito te lleve a donde Claude te esta preguntando.
// El payload del hook no dice una palabra de la ventana, pero la cadena de
// procesos si: a este exe lo lanzo Claude Code, a Claude Code la shell, y a la
// shell la terminal. Se guardan los PID de los ancestros y el bichito, al click,
// se queda con el primero que tenga una ventana visible.
const TH32CS_SNAPPROCESS: u32 = 0x0000_0002;
const INVALID_HANDLE_VALUE: isize = -1;
const MAX_ANCESTROS: usize = 8;
/// Duenos del escritorio: si la cadena llega hasta aca, cortar. Hacerle click al
/// bichito para que te abra el explorador de archivos no le sirve a nadie.
const RAIZ: [&str; 5] = [
    "explorer.exe",
    "services.exe",
    "wininit.exe",
    "winlogon.exe",
    "svchost.exe",
];

#[repr(C)]
struct ProcessEntry32 {
    dw_size: u32,
    cnt_usage: u32,
    th32_process_id: u32,
    th32_default_heap_id: usize,
    th32_module_id: u32,
    cnt_threads: u32,
    th32_parent_process_id: u32,
    pc_pri_class_base: i32,
    dw_flags: u32,
    sz_exe_file: [u8; 260],
}

extern "system" {
    fn CreateToolhelp32Snapshot(flags: u32, pid: u32) -> isize;
    fn Process32First(snap: isize, entry: *mut ProcessEntry32) -> i32;
    fn Process32Next(snap: isize, entry: *mut ProcessEntry32) -> i32;
    fn CloseHandle(h: isize) -> i32;
    fn GetCurrentProcessId() -> u32;
}

/// PID de los ancestros, del mas cercano al mas lejano.
///
/// Toolhelp y no NtQueryInformationProcess: el snapshot no necesita abrir cada
/// proceso, asi que tambien funciona si la terminal corre elevada. Cuesta unos
/// ms, por eso solo se llama al escribir "waiting", que es raro; el camino
/// caliente (working, en cada tool call) hereda la cadena que ya estaba escrita.
fn ancestros() -> Vec<u32> {
    let mut tabla: Vec<(u32, u32, String)> = Vec::new();
    unsafe {
        let snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snap == INVALID_HANDLE_VALUE {
            return Vec::new();
        }
        let mut e: ProcessEntry32 = std::mem::zeroed();
        // sin dwSize, Process32First devuelve FALSE y no se entera nadie
        e.dw_size = std::mem::size_of::<ProcessEntry32>() as u32;
        let mut hay = Process32First(snap, &mut e);
        while hay != 0 {
            let fin = e.sz_exe_file.iter().position(|&c| c == 0).unwrap_or(0);
            let nombre = String::from_utf8_lossy(&e.sz_exe_file[..fin]).to_lowercase();
            tabla.push((e.th32_process_id, e.th32_parent_process_id, nombre));
            hay = Process32Next(snap, &mut e);
        }
        CloseHandle(snap);
    }

    let mut cadena = Vec::new();
    let mut vistos = Vec::new();
    let mut pid = unsafe { GetCurrentProcessId() };
    for _ in 0..MAX_ANCESTROS {
        let Some((_, padre, _)) = tabla.iter().find(|(p, _, _)| *p == pid) else {
            break;
        };
        let padre = *padre;
        // el 0 no existe, y un PID repetido seria un ciclo por reuso de numero
        if padre == 0 || vistos.contains(&padre) {
            break;
        }
        let nombre = tabla
            .iter()
            .find(|(p, _, _)| *p == padre)
            .map(|(_, _, n)| n.as_str())
            .unwrap_or("");
        if RAIZ.contains(&nombre) {
            break;
        }
        vistos.push(padre);
        cadena.push(padre);
        pid = padre;
    }
    cadena
}

fn data_dir() -> PathBuf {
    let base = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| ".".into());
    Path::new(&base).join("Bichito")
}

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Lee el JSON que Claude Code manda por stdin. UNA sola vez: el payload se
/// reusa para el session_id y ademas se le reenvia al script de voz, que lo
/// necesita para nombrar el proyecto.
///
/// La lectura va en un hilo con timeout a proposito: si el shell nos hereda un
/// stdin abierto que nunca cierra, un read directo cuelga el proceso para
/// siempre y cada hook dejaria un zombie. (Ya paso con la version en Python.)
fn read_stdin() -> String {
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut buf = String::new();
        let _ = std::io::stdin().read_to_string(&mut buf);
        let _ = tx.send(buf);
    });
    rx.recv_timeout(STDIN_TIMEOUT).unwrap_or_default()
}

fn session_id(payload: &str) -> String {
    let sid = serde_json::from_str::<serde_json::Value>(payload)
        .ok()
        .and_then(|v| v.get("session_id")?.as_str().map(String::from))
        .unwrap_or_else(|| "default".into());

    let clean: String = sid
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .take(64)
        .collect();
    if clean.is_empty() {
        "default".into()
    } else {
        clean
    }
}

/// Que hacer con un evento Notification.
///
/// Todos llegan por el mismo hook, pero no significan lo mismo: el que Claude
/// te pida permiso es motivo para saltar al centro de la pantalla, y que
/// termine un agente en segundo plano no.
enum Kind {
    /// Claude esta frenado esperandote: bichito al centro y voz de espera.
    Waiting,
    /// Termino algo. Solo se habla: NO se toca el estado, porque la
    /// notificacion viene con el session_id de la sesion principal y esa puede
    /// estar todavia trabajando. Escribir "idle" le cortaria el cronometro y le
    /// haria festejar de mentira.
    Done,
    /// Ruido para el bichito: ni estado ni voz.
    Ignore,
}

/// Clasifica por el campo `notification_type` del payload.
///
/// Devuelve None si no viene (Claude Code viejo, o el hook no es Notification):
/// ahi manda el estado que llego por argv, o sea el comportamiento de siempre.
fn notification_kind(payload: &str) -> Option<Kind> {
    let tipo = serde_json::from_str::<serde_json::Value>(payload)
        .ok()
        .and_then(|v| v.get("notification_type")?.as_str().map(String::from))?;
    Some(match tipo.as_str() {
        // "<agente> finished": avisar y nada mas
        "agent_completed" => Kind::Done,
        // idle_prompt lo dispara Claude Code 60s despues de que te quedaste sin
        // escribir. No es una pregunta: el Stop ya te aviso que habia terminado,
        // y saltar al centro un minuto tarde es puro susto.
        "idle_prompt"
        | "auth_success"
        | "computer_use_enter"
        | "computer_use_exit"
        | "elicitation_complete"
        | "elicitation_response"
        | "push_notification" => Kind::Ignore,
        // permission_prompt, worker_permission_prompt, agent_needs_input,
        // elicitation_dialog, elicitation_url_dialog y lo que Claude Code sume
        // despues: se asume que te necesita, que es el default historico.
        _ => Kind::Waiting,
    })
}

struct Config {
    enabled: bool,
    pet: bool,
    voice: bool,
    autostart: bool,
    voice_script: String,
    msg_done: String,
    msg_waiting: String,
}

fn load_config(dir: &Path) -> Config {
    // Si falta el archivo o esta roto, todo prendido: mas vale que el bichito
    // aparezca a que se quede mudo sin explicacion.
    let mut cfg = Config {
        enabled: true,
        pet: true,
        voice: true,
        autostart: true,
        voice_script: String::new(),
        msg_done: "El proyecto {proyecto} terminó".into(),
        msg_waiting: "El proyecto {proyecto} está esperando tu respuesta".into(),
    };
    let Ok(txt) = fs::read_to_string(dir.join("config.json")) else {
        return cfg;
    };
    // Si el archivo se toco desde PowerShell viene con BOM y serde_json lo
    // rechaza entero: la config se perderia en silencio y esto se quedaria sin
    // voz. El lado Python ya se defiende con utf-8-sig.
    let txt = txt.trim_start_matches('\u{feff}');
    let Ok(v) = serde_json::from_str::<serde_json::Value>(txt) else {
        return cfg;
    };
    let flag = |k: &str, d: bool| v.get(k).and_then(|x| x.as_bool()).unwrap_or(d);
    cfg.enabled = flag("enabled", true);
    cfg.pet = flag("pet", true);
    cfg.voice = flag("voice", true);
    cfg.autostart = flag("autostart", true);
    let text = |k: &str| {
        v.get(k)
            .and_then(|x| x.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(String::from)
    };
    if let Some(s) = text("voice_script") {
        cfg.voice_script = s;
    }
    if let Some(s) = text("msg_done") {
        cfg.msg_done = s;
    }
    if let Some(s) = text("msg_waiting") {
        cfg.msg_waiting = s;
    }
    cfg
}

/// Arma la frase segun el estado y la mete en el JSON que se le reenvia al
/// script de voz.
///
/// El texto viaja por stdin, NUNCA por argv: los acentos se rompen por codepage
/// al pasar por la linea de comandos.
/// Nombre de la carpeta del proyecto, sacado del cwd del payload.
///
/// Es lo que dice la voz y, desde que hay un bichito por sesion, tambien lo que
/// se lee abajo de cada uno: con seis Claude abiertos, el estado sin nombre no
/// te dice a cual le esta hablando.
fn proyecto(v: &serde_json::Value) -> String {
    v.get("cwd")
        .and_then(|c| c.as_str())
        .map(|c| {
            c.trim_end_matches(['/', '\\'])
                .rsplit(['/', '\\'])
                .next()
                .unwrap_or("")
                .to_string()
        })
        .unwrap_or_default()
}

fn with_message(payload: &str, cfg: &Config, state: &str, fijado: Option<&str>) -> String {
    let mut v: serde_json::Value =
        serde_json::from_str(payload).unwrap_or_else(|_| serde_json::json!({}));

    // el nombre que ya tenia la sesion gana sobre el cwd de este momento: si no,
    // la voz diria "el proyecto dist termino" cuando el bichito dice otra cosa
    let proyecto = match fijado {
        Some(n) if !n.is_empty() => n.to_string(),
        _ => proyecto(&v),
    };

    let plantilla = if state == "idle" || state == "fin" {
        &cfg.msg_done
    } else {
        &cfg.msg_waiting
    };
    let texto = plantilla.replace("{proyecto}", &proyecto);
    // sin proyecto, "El proyecto  terminó" suena raro: se limpia el hueco
    let texto = texto.replace("  ", " ").trim().to_string();

    if !v.is_object() {
        v = serde_json::json!({});
    }
    v["bichito_texto"] = serde_json::Value::String(texto);
    v.to_string()
}

/// Escribe state/<sesion>.json de forma atomica (temp + rename): el bichito
/// nunca puede leer un archivo a medio escribir.
fn write_state(dir: &Path, state: &str, payload: &str) -> Option<String> {
    let states = dir.join("state");
    if fs::create_dir_all(&states).is_err() {
        return None;
    }
    let path = states.join(format!("{}.json", session_id(payload)));

    // Fin de sesion: se borra el archivo. Que exista significa "este Claude
    // sigue abierto", y de eso depende que el bichito muestre uno por sesion y
    // no un cementerio de sesiones viejas.
    if state == "fin" {
        let _ = fs::remove_file(&path);
        return None;
    }

    let payload_json: serde_json::Value =
        serde_json::from_str(payload).unwrap_or_else(|_| serde_json::json!({}));

    let t = now();
    let mut since = t;
    let mut focus: Vec<u32> = Vec::new();
    let mut habia_focus = false;
    // El nombre se fija en la primera escritura de la sesion y no se toca mas:
    // el cwd del payload es el de ese momento, asi que si Claude hace un cd la
    // sesion pasaria a llamarse "dist" o "src" y el bichito dejaria de servir
    // justo para lo que existe, que es saber cual es cual.
    let mut nombre = String::new();

    // El estado anterior se lee siempre (no solo en "working"): de ahi salen el
    // arranque del turno y la cadena hasta la terminal. Antes se leia solo al
    // trabajar, asi que el primer "idle" borraba la cadena y el click al bichito
    // dejaba de llevarte a ningun lado.
    if let Ok(prev) = fs::read_to_string(&path) {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&prev) {
            let prev_state = v.get("state").and_then(|s| s.as_str()).unwrap_or("");
            let prev_ts = v.get("ts").and_then(|s| s.as_f64()).unwrap_or(0.0);
            if let Some(f) = v.get("focus").and_then(|f| f.as_array()) {
                habia_focus = true;
                focus = f.iter().filter_map(|x| x.as_u64().map(|n| n as u32)).collect();
            }
            if let Some(p) = v.get("proyecto").and_then(|p| p.as_str()) {
                if !p.is_empty() {
                    nombre = p.to_string();
                }
            }
            if state == "working" {
                // Cuando Claude hace una pregunta, PreToolUse dispara DOS hooks
                // a la vez: el de matcher "*" con "working" y el especifico con
                // "waiting". Sin esta regla cual gana es azar y la pregunta se
                // perderia. "waiting" recien puesto no se pisa.
                if prev_state == "waiting" && t - prev_ts < WAITING_HOLD {
                    return None;
                }
                // si ya venia trabajando se conserva el arranque, para que el
                // cronometro cuente el turno entero y no se reinicie con cada
                // herramienta
                if prev_state == "working" {
                    if let Some(s) = v.get("since").and_then(|s| s.as_f64()) {
                        since = s;
                    }
                }
            }
        }
    }

    // El snapshot de procesos cuesta ~14ms, asi que se saca una sola vez por
    // sesion y se refresca en cada espera, que es cuando de verdad importa.
    if state == "waiting" || (!habia_focus && state != "idle") {
        focus = ancestros();
    }

    let nombre = if nombre.is_empty() {
        proyecto(&payload_json)
    } else {
        nombre
    };
    let body = serde_json::json!({
        "state": state,
        "ts": t,
        "since": since,
        "focus": focus,
        "proyecto": nombre,
    })
    .to_string();
    let tmp = path.with_extension("tmp");
    if fs::write(&tmp, body).is_ok() {
        let _ = fs::rename(&tmp, &path);
    }
    Some(nombre)
}

fn speak(cfg: &Config, payload: &str) {
    if cfg.voice_script.is_empty() || !Path::new(&cfg.voice_script).exists() {
        return;
    }
    // El script saca el nombre del proyecto del JSON del hook (campo cwd), asi
    // que hay que reenviarselo: nosotros ya consumimos el stdin original y sin
    // esto diria "Claude esta esperando" en vez de "El proyecto X esta esperando".
    let child = Command::new("powershell")
        .args([
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            &cfg.voice_script,
        ])
        .stdin(Stdio::piped())
        // Solo CREATE_NO_WINDOW: combinarlo con DETACHED_PROCESS deja el pipe
        // inservible (los dos son flags de creacion de consola y se pisan), y
        // el script se quedaba sin payload. Igual sobrevive a que salgamos:
        // Windows no mata a los hijos cuando muere el padre.
        .creation_flags(CREATE_NO_WINDOW)
        .spawn();
    if let Ok(mut child) = child {
        if let Some(mut stdin) = child.stdin.take() {
            let _ = stdin.write_all(payload.as_bytes());
        } // al soltarlo se cierra el pipe y el script ve EOF
    }
}

fn launch_pet(dir: &Path) {
    // el propio bichito se protege con un lock de socket, asi que no se duplica
    let exe = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.join("Bichito.exe")));
    let Some(exe) = exe else { return };
    if !exe.exists() {
        return;
    }
    let _ = Command::new(exe)
        .arg("--pet")
        .current_dir(dir)
        .creation_flags(DETACHED_PROCESS)
        .spawn();
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some(state) = args.first() else { return };
    if !matches!(state.as_str(), "working" | "waiting" | "idle" | "notify" | "fin") {
        return;
    }
    let has = |f: &str| args.iter().any(|a| a == f);

    let dir = data_dir();
    let cfg = load_config(&dir);
    if !cfg.enabled {
        return; // interruptor general apagado: no se hace nada
    }

    let payload = read_stdin();

    // La decision se toma con el payload y no con matchers en settings.json a
    // proposito: los matchers de Notification necesitan una version reciente de
    // Claude Code, y settings.json se escribe una sola vez, al instalar.
    let (state, guardar) = match notification_kind(&payload) {
        Some(Kind::Ignore) => return,
        Some(Kind::Done) => ("idle", false), // "idle" -> la voz dice msg_done
        Some(Kind::Waiting) => ("waiting", true),
        // sin notification_type manda argv; "notify" cae en la espera de siempre
        None if state == "notify" => ("waiting", true),
        None => (state.as_str(), true),
    };

    let mut nombre = None;
    if cfg.pet && guardar {
        nombre = write_state(&dir, state, &payload);
    }
    if nombre.is_none() {
        // no se escribio (avisos que no tocan el estado, o el bichito apagado):
        // igual se busca el nombre que ya tenia la sesion, para que la voz diga
        // lo mismo que muestra la ventanita
        nombre = fs::read_to_string(dir.join("state").join(format!("{}.json", session_id(&payload))))
            .ok()
            .and_then(|t| serde_json::from_str::<serde_json::Value>(t.trim_start_matches('\u{feff}')).ok())
            .and_then(|v| v.get("proyecto")?.as_str().map(String::from))
            .filter(|s| !s.is_empty());
    }
    // El launch NO va atado a cfg.pet: ese proceso tambien sostiene el icono de
    // la bandeja, que tiene que estar aunque la ventanita flotante este apagada.
    if has("--launch") && cfg.autostart {
        launch_pet(&dir);
    }
    if has("--voice") && cfg.voice {
        speak(&cfg, &with_message(&payload, &cfg, state, nombre.as_deref()));
    }
}
