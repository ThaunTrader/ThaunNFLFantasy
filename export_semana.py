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

ENFRENTAMIENTOS DECIDIDOS: para los partidos con actividad que Fleaflicker
aún no ha cerrado, se consulta FetchLeagueBoxscore y se lee la
probabilidad de victoria que calcula Fleaflicker (pointsAway/pointsHome
.winProbability). Si la de mi equipo es exactamente 1.0 -> "WIN"; si es
0.0 -> "LOSE". Es un juicio basado en proyecciones, no una certeza
matemática. Nota: el JSON omite los valores cero, así que un
winProbability ausente con isWinProbabilitySet=true significa 0.0.

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

# Probabilidad de victoria a partir de la cual se considera decidido el
# enfrentamiento. Con 1.0 exacto (y 0.0 el rival) coincide con lo observado
# en la app de Fleaflicker: 0.9997841 con un RB por jugar NO estaba decidido
# (mostraba 99%) y 1.0 sí. Si se viera alguna tarjeta mal clasificada,
# este es el valor a ajustar.
UMBRAL_DECIDIDO = 1.0


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
        "game_id": partido.get("id"),
        "rival": rival.get("name", "Rival"),
        "rival_record": rec_rival.get("formatted"),
        "local": es_local,
        "jugado": bool(partido.get("isFinalScore")),
        "mis_puntos": puntos(mi_score),
        "puntos_rival": puntos(rival_score),
        "resultado": resultado,
    }


def prob_victoria(bloque):
    """Probabilidad de victoria de un lado (pointsAway / pointsHome).
    - isWinProbabilitySet=true y winProbability presente -> ese valor.
    - isWinProbabilitySet=true y winProbability AUSENTE -> 0.0 (el JSON
      omite los ceros; confirmado con un enfrentamiento ya decidido).
    - isWinProbabilitySet falso o ausente -> None (desconocida)."""
    if not isinstance(bloque, dict) or not bloque.get("isWinProbabilitySet"):
        return None
    return float(bloque.get("winProbability", 0.0))


def analizar_boxscore(box, team_id, soy_local):
    """Extrae del boxscore: si está en juego, mi probabilidad de victoria y
    si el enfrentamiento está decidido ('WIN' / 'LOSE' / None)."""
    game = box.get("game") or {}
    home_id = (game.get("home") or {}).get("id")
    away_id = (game.get("away") or {}).get("id")
    if team_id is not None and team_id == home_id:
        lado = "home"
    elif team_id is not None and team_id == away_id:
        lado = "away"
    else:
        lado = "home" if soy_local else "away"

    mia = prob_victoria(box.get("pointsHome" if lado == "home" else "pointsAway"))
    decidido = None
    if mia is not None:
        if mia >= UMBRAL_DECIDIDO:
            decidido = "WIN"
        elif mia <= 1.0 - UMBRAL_DECIDIDO:
            decidido = "LOSE"
    return {
        "en_juego": bool(game.get("isInProgress")),  # ausente = false
        "win_prob": mia,
        "decidido": decidido,
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
    fallos_boxscore = set()

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

        # Probabilidad de victoria / decidido: solo si el partido tiene
        # actividad y Fleaflicker no lo ha cerrado (ahorra llamadas).
        r = entrada["partido"]
        if (
            r
            and not r["jugado"]
            and ((r["mis_puntos"] or 0) > 0 or (r["puntos_rival"] or 0) > 0)
            and r.get("game_id")
        ):
            time.sleep(PAUSA)
            try:
                box = get(
                    "FetchLeagueBoxscore",
                    {"league_id": league_id, "fantasy_game_id": r["game_id"]},
                )
                r.update(analizar_boxscore(box, team_id, r["local"]))
            except requests.RequestException as e:
                fallos_boxscore.add(league_id)
                print(f"[{entrada['liga']}] boxscore no disponible: {e}")

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

        # Boxscore fallido: mantener lo último que se sabía del MISMO partido
        por_liga = {e["league_id"]: e for e in ligas_out}
        for league_id in fallos_boxscore:
            nuevo = (por_liga.get(league_id) or {}).get("partido")
            antiguo = (previos.get(league_id) or {}).get("partido") or {}
            if nuevo and antiguo.get("game_id") == nuevo.get("game_id"):
                for campo in ("en_juego", "win_prob", "decidido"):
                    if campo in antiguo:
                        nuevo[campo] = antiguo[campo]

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
