"""
export_calendar.py — Genera docs/data/calendario.json con el calendario
completo de la temporada (semanas 1-17) y los resultados ya disputados,
para cada liga del usuario.

Pensado para ejecutarse UNA VEZ POR SEMANA (no en tiempo real): los
marcadores no son "en directo".

AVISO: el parseo de FetchLeagueScoreboard es defensivo, no se ha podido
verificar contra la API real. Revisar el resultado de la primera ejecución.

Requiere variable de entorno: FLEAFLICKER_USER_ID
"""

import json
import os
import time
from datetime import datetime, timezone

import requests

BASE_URL = "https://www.fleaflicker.com/api"
USER_ID = os.environ["FLEAFLICKER_USER_ID"]
OUTPUT = "docs/data/calendario.json"
SEASON = int(os.environ.get("SEASON", "2026"))
SEMANAS = range(1, 18)  # 17 semanas de temporada regular NFL


def get(endpoint, params):
    params["sport"] = "NFL"
    r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def extraer_partidos(nodo, encontrados=None):
    """Busca recursivamente objetos que parezcan FantasyGame (home/away)."""
    if encontrados is None:
        encontrados = []
    if isinstance(nodo, dict):
        if "home" in nodo and "away" in nodo:
            encontrados.append(nodo)
        else:
            for v in nodo.values():
                extraer_partidos(v, encontrados)
    elif isinstance(nodo, list):
        for item in nodo:
            extraer_partidos(item, encontrados)
    return encontrados


def resumen_partido(partido, team_id):
    home = partido.get("home", {}) or {}
    away = partido.get("away", {}) or {}
    es_local = home.get("id") == team_id
    es_visitante = away.get("id") == team_id
    if not (es_local or es_visitante):
        return None

    rival = away if es_local else home
    mi_score = (partido.get("homeScore") or {}) if es_local else (partido.get("awayScore") or {})
    rival_score = (partido.get("awayScore") or {}) if es_local else (partido.get("homeScore") or {})

    resultado = partido.get("homeResult") if es_local else partido.get("awayResult")

    return {
        "rival": rival.get("name", "Rival"),
        "local": es_local,
        "jugado": bool(partido.get("isFinalScore")),
        "mis_puntos": mi_score.get("score", {}).get("value") if isinstance(mi_score.get("score"), dict) else mi_score.get("value"),
        "puntos_rival": rival_score.get("score", {}).get("value") if isinstance(rival_score.get("score"), dict) else rival_score.get("value"),
        "resultado": resultado,
    }


def main():
    data = get("FetchUserLeagues", {"user_id": USER_ID})
    leagues = data.get("leagues", [])

    resultado = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "season": SEASON,
        "ligas": [],
    }

    for lg in leagues:
        team = lg.get("ownedTeam") or {}
        league_id = lg.get("id")
        team_id = team.get("id")
        if not league_id or not team_id:
            continue

        entrada = {
            "league_id": league_id,
            "liga": lg.get("name", ""),
            "equipo": team.get("name", ""),
            "semanas": [],
        }

        for semana in SEMANAS:
            try:
                sb = get(
                    "FetchLeagueScoreboard",
                    {
                        "league_id": league_id,
                        "season": SEASON,
                        "scoring_period": semana,
                    },
                )
            except requests.RequestException as e:
                entrada["semanas"].append({"semana": semana, "error": str(e)})
                continue

            partidos = extraer_partidos(sb)
            mi_partido = None
            for p in partidos:
                r = resumen_partido(p, team_id)
                if r:
                    mi_partido = r
                    break

            if mi_partido:
                mi_partido["semana"] = semana
                entrada["semanas"].append(mi_partido)

            time.sleep(0.15)  # cortesía con la API, evitar rate limiting

        resultado["ligas"].append(entrada)
        jugadas = sum(1 for s in entrada["semanas"] if s.get("jugado"))
        print(f"{entrada['liga']}: {jugadas} semana(s) jugada(s) de {len(entrada['semanas'])}")

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=1)
    print(f"Escrito {OUTPUT}")


if __name__ == "__main__":
    main()
