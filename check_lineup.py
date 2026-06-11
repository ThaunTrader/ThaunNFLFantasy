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


# ── Main logic ────────────────────────────────────────────────────────────────
def main():
    # 1. Obtener ligas activas del usuario
    data = get("FetchUserLeagues", {"user_id": USER_ID})
    leagues = data.get("leagues", [])

    if not leagues:
        print("No se encontraron ligas activas.")
        return

    alerts = []

    for league in leagues:
        league_id = league["id"]
        league_name = league.get("name", f"Liga {league_id}")

        # Identificar el team_id del usuario en esta liga
        owned_team = league.get("ownedTeam") or league.get("owned_team")
        if not owned_team:
            continue
        team_id = owned_team["id"]

        # 2. Obtener scoreboard para encontrar el fantasy_game_id del usuario
        scoreboard = get("FetchLeagueScoreboard", {"league_id": league_id})
        games = scoreboard.get("games", [])

        game_id = None
        for game in games:
            home_id = game.get("home", {}).get("id")
            away_id = game.get("away", {}).get("id")
            if team_id in (home_id, away_id):
                game_id = game.get("id")
                break

        if not game_id:
            print(f"[{league_name}] No se encontró partido activo para el equipo {team_id}.")
            continue

        # 3. Obtener boxscore del partido
        boxscore = get("FetchLeagueBoxscore", {
            "league_id": league_id,
            "fantasy_game_id": game_id
        })

        # 4. Identificar qué lado es el usuario (home o away)
        home_lineup = boxscore.get("homeTeamLineup", {})
        away_lineup = boxscore.get("awayTeamLineup", {})

        home_team = boxscore.get("homeTeam", {})
        away_team = boxscore.get("awayTeam", {})

        if home_team.get("id") == team_id:
            my_lineup = home_lineup
        else:
            my_lineup = away_lineup

        slots = my_lineup.get("slots", [])

        league_alerts = []
        for slot in slots:
            # Solo titulares (no bench)
            slot_name = slot.get("slotName") or slot.get("label") or ""
            if "BN" in slot_name.upper() or "BENCH" in slot_name.upper():
                continue

            league_player = slot.get("leaguePlayer") or slot.get("player") or {}
            pro_player = league_player.get("proPlayer") or league_player.get("pro_player") or {}

            name = pro_player.get("nameFull") or pro_player.get("name_full") or pro_player.get("nameShort") or "Desconocido"
            position = pro_player.get("position", "")

            injury = pro_player.get("injury") or {}
            severity = injury.get("severity") or injury.get("typeAbbreviation") or ""

            if severity.upper() in ALERT_STATUSES:
                description = injury.get("description") or injury.get("typeFull") or severity
                league_alerts.append(f"  ⚠️ <b>{name}</b> ({position}) — {severity}: {description}")

        if league_alerts:
            block = f"🏈 <b>{league_name}</b>\n" + "\n".join(league_alerts)
            alerts.append(block)

    # 5. Enviar Telegram
    if alerts:
        message = "🚨 <b>Alerta de jugadores en tu lineup</b>\n\n" + "\n\n".join(alerts)
        send_telegram(message)
        print("Alerta enviada.")
    else:
        print("Todos los jugadores titulares están sanos. No se envía notificación.")


if __name__ == "__main__":
    main()
