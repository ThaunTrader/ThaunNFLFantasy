"""
export_calendar.py — Genera docs/data/calendario.json con el calendario
completo de la temporada (semanas 1-17) y los resultados ya disputados,
para cada liga del usuario.

Pensado para ejecutarse UNA VEZ POR SEMANA (no en tiempo real): los
marcadores no son "en directo".

AVISO: se añade un User-Agent de navegador porque Fleaflicker devolvía
403 Forbidden a las peticiones hechas desde GitHub Actions (con el
User-Agent por defecto de la librería requests), pero la misma URL
funcionaba con normalidad desde un navegador. Es una hipótesis
razonable, no confirmada al 100% — verificar tras la primera ejecución.

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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


def get(endpoint, params, reintentos=2):
    params["sport"] = "NFL"
    for intento in range(reintentos + 1):
        r = requests.get(
            f"{BASE_URL}/{endpoint}", params=params, headers=HEADERS, timeout=15
        )
        if r.status_code in (403, 429) and intento < reintentos:
            time.sleep(6 * (intento + 1))  # backoff: 6s, 12s...
            continue
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


def semana_actual_desde(sb):
    """Busca en eligibleSchedulePeriods la semana marcada como actual
    (containsNow / low.isNow). Devuelve None si no se encuentra, en
    cuyo caso el llamador debe usar un tope por defecto."""
    for periodo in sb.get("eligibleSchedulePeriods", []):
        if periodo.get("containsNow") or (periodo.get("low") or {}).get("isNow"):
            return periodo.get("ordinal")
    return None


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

    rec_rival = rival.get("recordOverall") or {}

    return {
        "rival": rival.get("name", "Rival"),
        "rival_record": rec_rival.get("formatted"),  # p.ej. "2-1"
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

    # Determinar la semana actual UNA sola vez (es la misma para todas
    # las ligas, es el calendario NFL) usando la primera liga disponible.
    # Si falla, se usa 17 como tope por defecto (comportamiento anterior).
    tope_semanas = 17
    if leagues:
        primera = leagues[0]
        primer_league_id = primera.get("id")
        try:
            sb_prueba = get(
                "FetchLeagueScoreboard",
                {"league_id": primer_league_id, "season": SEASON, "scoring_period": 1},
            )
            detectada = semana_actual_desde(sb_prueba)
            if detectada:
                tope_semanas = min(detectada, 17)
                print(f"Semana actual detectada: {detectada} (se piden semanas 1-{tope_semanas})")
        except requests.RequestException as e:
            print(f"No se pudo detectar la semana actual ({e}), usando tope 17")

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

        for semana in range(1, tope_semanas + 1):
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

            time.sleep(1.5)  # más margen que antes, para evitar el 403 por volumen

        resultado["ligas"].append(entrada)
        jugadas = sum(1 for s in entrada["semanas"] if s.get("jugado"))
        print(f"{entrada['liga']}: {jugadas} semana(s) jugada(s) de {len(entrada['semanas'])}")
        time.sleep(1.0)  # pausa extra entre ligas

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=1)
    print(f"Escrito {OUTPUT}")


if __name__ == "__main__":
    main()
