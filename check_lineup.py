import os
import requests

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = "https://www.fleaflicker.com/api"
USER_ID = os.environ["FLEAFLICKER_USER_ID"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

ALERT_STATUSES = {"QUESTIONABLE", "DOUBTFUL", "OUT", "IR"}

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

        if league_alerts:
            block = f"🏈 <b>{league_name}</b>\n" + "\n".join(league_alerts)
            alerts.append(block)

    # Enviar Telegram
    if alerts:
        message = "🚨 <b>Alerta de jugadores en tu lineup</b>\n\n" + "\n\n".join(alerts)
        send_telegram(message)
        print("Alerta enviada.")
    else:
        print("Todos los jugadores titulares están sanos. No se envía notificación.")


if __name__ == "__main__":
    main()
        print("Todos los jugadores titulares están sanos. No se envía notificación.")


if __name__ == "__main__":
    main()
