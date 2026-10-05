import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = "https://www.fleaflicker.com/api"
USER_ID = os.environ["FLEAFLICKER_USER_ID"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

ALERT_STATUSES = {"QUESTIONABLE", "DOUBTFUL", "OUT", "IR"}

# Franjas (hora española) en las que se envía, como máximo, UNA
# confirmación de "sin lesionados". Formato: (día, (h, m) inicio, (h, m) fin)
# con lunes=0 ... domingo=6. Solo afecta a la confirmación: los avisos de
# lesionados se envían siempre, en todas las ejecuciones.
VENTANAS_CONFIRMACION = [
    (6, (14, 30), (16, 30)),  # domingo
    (6, (17, 0), (19, 0)),    # domingo
    (6, (20, 30), (22, 0)),   # domingo
    (0, (1, 0), (2, 30)),     # madrugada domingo -> lunes
    (1, (1, 0), (2, 30)),     # madrugada lunes -> martes
    (3, (1, 0), (2, 30)),     # madrugada del jueves (SUPUESTO: igual que las demás)
]
# Las ejecuciones programadas son cada hora, así que solo la que cae en la
# primera hora de cada franja envía la confirmación.
ESPACIADO_EJECUCIONES = timedelta(minutes=60)


def toca_confirmacion(ahora=None):
    """True si esta ejecución debe enviar la confirmación de 'sin lesionados'.
    - Ejecución manual (workflow_dispatch): siempre, para poder comprobar
      que el bot funciona.
    - Programada: solo si cae en la primera hora de alguna franja.
    - Si no se puede calcular la hora española, se envía (mejor un
      mensaje de más que ninguno)."""
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        return True
    try:
        ahora = ahora or datetime.now(ZoneInfo("Europe/Madrid"))
    except Exception as e:
        print(f"No se pudo calcular la hora española ({e}); se envía confirmación.")
        return True

    for dia, (hi, mi), (hf, mf) in VENTANAS_CONFIRMACION:
        if ahora.weekday() != dia:
            continue
        inicio = ahora.replace(hour=hi, minute=mi, second=0, microsecond=0)
        fin = ahora.replace(hour=hf, minute=mf, second=0, microsecond=0)
        if inicio <= ahora < min(fin, inicio + ESPACIADO_EJECUCIONES):
            return True
    return False

# ── Helpers ───────────────────────────────────────────────────────────────────
def get(endpoint, params):
    params["sport"] = "NFL"
    r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    requests.post(url, json=payload, timeout=10)


def get_locked_ids(league_id, team_id):
    """IDs de los jugadores cuyo partido ya ha empezado (alineación
    bloqueada), según FetchRoster -> leaguePlayer.transactionStatus.
    isLineupStatusLocked. Si la llamada falla devuelve un set vacío
    (no se filtra nada), para no perder avisos por un fallo del filtro."""
    try:
        roster = get("FetchRoster", {"league_id": league_id, "team_id": team_id})
    except requests.RequestException as e:
        print(f"[{league_id}] No se pudo comprobar el bloqueo de jugadores: {e}")
        return set()

    locked = set()

    def recorrer(nodo):
        if isinstance(nodo, dict):
            pp = nodo.get("proPlayer")
            if isinstance(pp, dict) and (nodo.get("transactionStatus") or {}).get("isLineupStatusLocked"):
                pid = pp.get("id")
                if pid is not None:
                    locked.add(pid)
            for v in nodo.values():
                recorrer(v)
        elif isinstance(nodo, list):
            for item in nodo:
                recorrer(item)

    recorrer(roster)
    return locked


# ── Main logic ────────────────────────────────────────────────────────────────
def main():
    data = get("FetchUserLeagues", {"user_id": USER_ID})
    leagues = data.get("leagues", [])

    if not leagues:
        print("No se encontraron ligas activas.")
        return

    alerts = []
    total_skipped = 0

    for league in leagues:
        league_id = league["id"]
        league_name = league.get("name", f"Liga {league_id}")

        owned_team = league.get("ownedTeam") or league.get("owned_team") or {}
        team_id = owned_team.get("id")
        if not team_id:
            print(f"[{league_name}] No se encontró team_id.")
            continue

        # Buscar el partido del usuario en el scoreboard
        scoreboard = get("FetchLeagueScoreboard", {"league_id": league_id})
        games = scoreboard.get("games", [])

        game_id = None
        my_side = None
        for game in games:
            home_id = game.get("home", {}).get("id")
            away_id = game.get("away", {}).get("id")
            if team_id == home_id:
                game_id = game.get("id")
                my_side = "home"
                break
            elif team_id == away_id:
                game_id = game.get("id")
                my_side = "away"
                break

        if not game_id:
            print(f"[{league_name}] No se encontró partido activo para el equipo {team_id}.")
            continue

        # Obtener boxscore
        boxscore = get("FetchLeagueBoxscore", {
            "league_id": league_id,
            "fantasy_game_id": game_id
        })

        # Jugadores cuyo partido ya empezó: no se pueden cambiar, no se avisa
        locked_ids = get_locked_ids(league_id, team_id)
        skipped_locked = 0

        # Recorrer lineups — cada slot tiene "home" y "away" con el jugador
        lineups = boxscore.get("lineups", [])
        league_alerts = []

        for lineup_group in lineups:
            # Solo titulares (group START), ignorar BENCH
            if lineup_group.get("group", "").upper() != "START":
                continue

            for slot in lineup_group.get("slots", []):
                player_data = slot.get(my_side) or {}
                pro_player = player_data.get("proPlayer") or {}

                if not pro_player:
                    continue

                if pro_player.get("id") in locked_ids:
                    skipped_locked += 1
                    continue

                name = pro_player.get("nameFull") or pro_player.get("nameShort") or "Desconocido"
                position = pro_player.get("position", "")
                injury = pro_player.get("injury") or {}
                severity = (injury.get("severity") or "").upper()

                if severity in ALERT_STATUSES:
                    description = injury.get("typeFull") or injury.get("description") or severity
                    league_alerts.append(f"  ⚠️ <b>{name}</b> ({position}) — {description}")

        print(f"[{league_name}] bloqueados detectados: {len(locked_ids)} · titulares omitidos por bloqueo: {skipped_locked}")
        total_skipped += skipped_locked

        if league_alerts:
            block = f"🏈 <b>{league_name}</b>\n" + "\n".join(league_alerts)
            alerts.append(block)

    # Enviar Telegram
    if alerts:
        message = "🚨 <b>Alerta de jugadores en tu lineup</b>\n\n" + "\n\n".join(alerts)
        send_telegram(message)
        print("Alerta enviada.")
    elif toca_confirmacion():
        message = "✅ <b>No tienes jugadores lesionados</b> en tus alineaciones titulares."
        if total_skipped:
            message += (
                f"\n(No cuentan {total_skipped} jugador(es) con el partido ya empezado.)"
            )
        send_telegram(message)
        print("Sin alertas. Mensaje de confirmación enviado.")
    else:
        print("Sin alertas. Fuera de franja de confirmación: no se envía mensaje.")


if __name__ == "__main__":
    main()
