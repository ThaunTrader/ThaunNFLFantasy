"""
export_semana.py — Genera docs/data/semana.json con el partido de la
SEMANA EN CURSO de cada liga, con marcadores provisionales mientras se
juega (no solo resultados cerrados). Es ligero: una llamada por liga.

Pensado para ejecutarse varias veces al día. Solo reescribe el fichero si
algo ha cambiado (sin contar la marca "generado"), para no producir
commits vacíos. Por eso "generado" indica el ÚLTIMO CAMBIO de marcador,
no la última comprobación.

Si una liga falla (p.ej. un 403 puntual), se conserva su último partido
conocido de esa misma semana en vez de dejarlo en blanco.

Requiere variable de entorno: FLEAFLICKER_USER_ID
"""

import json
import os
import time
from datetime import datetime, timezone

import requests

BASE_URL = "https://www.fleaflicker.com/api"
USER_ID = os.environ["FLEAFLICKER_USER_ID"]
OUTPUT = "docs/data/semana.json"
SEASON = int(os.environ.get("SEASON", "2026"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

PAUSA = 1.5  # segundos entre peticiones (valor que funciona con Fleaflicker)


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
    """Semana marcada como actual (containsNow / low.isNow) en
    eligibleSchedulePeriods, o None si no aparece."""
    for periodo in sb.get("eligibleSchedulePeriods", []):
        if periodo.get("containsNow") or (periodo.get("low") or {}).get("isNow"):
            return periodo.get("ordinal")
    return None


def puntos(score):
    """Extrae el valor numérico de un homeScore/awayScore."""
    if not isinstance(score, dict):
        return None
    s = score.get("score")
    if isinstance(s, dict):
        return s.get("value")
    return score.get("value")


def resumen_partido(partido, team_id):
    home = partido.get("home", {}) or {}
    away = partido.get("away", {}) or {}
    es_local = home.get("id") == team_id
    es_visitante = away.get("id") == team_id
    if not (es_local or es_visitante):
        return None

    rival = away if es_local else home
    mi_score = partido.get("homeScore") if es_local else partido.get("awayScore")
    rival_score = partido.get("awayScore") if es_local else partido.get("homeScore")
    resultado = partido.get("homeResult") if es_local else partido.get("awayResult")
    rec_rival = rival.get("recordOverall") or {}

    return {
        "rival": rival.get("name", "Rival"),
        "rival_record": rec_rival.get("formatted"),
        "local": es_local,
        "jugado": bool(partido.get("isFinalScore")),
        "mis_puntos": puntos(mi_score),
        "puntos_rival": puntos(rival_score),
        "resultado": resultado,
    }


def sin_generado(d):
    return {k: v for k, v in (d or {}).items() if k != "generado"}


def cargar_previo():
    try:
        with open(OUTPUT, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main():
    data = get("FetchUserLeagues", {"user_id": USER_ID})
    leagues = data.get("leagues", [])

    previo = cargar_previo()
    semana = None
    ligas_out = []
    huecos_previos = {}

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
            "partido": None,
        }

        try:
            sb = get("FetchLeagueScoreboard", {"league_id": league_id})
            periodo = (sb.get("schedulePeriod") or {}).get("ordinal")
            if semana is None:
                semana = semana_actual_desde(sb) or periodo
            # Si la respuesta por defecto no es de la semana actual, se pide
            # la semana concreta para no mezclar semanas.
            if semana is not None and periodo is not None and periodo != semana:
                time.sleep(PAUSA)
                sb = get(
                    "FetchLeagueScoreboard",
                    {"league_id": league_id, "season": SEASON, "scoring_period": semana},
                )
            for p in extraer_partidos(sb):
                r = resumen_partido(p, team_id)
                if r:
                    entrada["partido"] = r
                    break
        except requests.RequestException as e:
            entrada["error"] = str(e)
            huecos_previos[league_id] = entrada  # se rellenará con el dato previo

        ligas_out.append(entrada)
        time.sleep(PAUSA)

    if semana is None:
        raise SystemExit("No se pudo determinar la semana actual; no se escribe nada.")

    # Para las ligas que han fallado, conservar su último partido conocido
    # de ESTA misma semana (así un 403 puntual no deja la tarjeta vacía).
    if previo and previo.get("semana") == semana:
        previos = {l.get("league_id"): l for l in previo.get("ligas", [])}
        for league_id, entrada in huecos_previos.items():
            antiguo = previos.get(league_id)
            if antiguo and antiguo.get("partido"):
                entrada["partido"] = antiguo["partido"]

    resultado = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "season": SEASON,
        "semana": semana,
        "ligas": ligas_out,
    }

    if previo and sin_generado(previo) == sin_generado(resultado):
        print(f"Semana {semana}: sin cambios en los marcadores, no se reescribe.")
        return

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=1)
    en_juego = sum(
        1 for l in ligas_out if l["partido"] and not l["partido"]["jugado"]
    )
    print(f"Semana {semana}: {len(ligas_out)} liga(s), {en_juego} sin cerrar. Escrito {OUTPUT}")


if __name__ == "__main__":
    main()
