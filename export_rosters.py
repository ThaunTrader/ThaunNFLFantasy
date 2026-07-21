"""
export_rosters.py — Genera docs/data/rosters.json con la valoración del
roster de cada equipo, en 3 niveles: general -> hueco (Titular/Banco/
Taxi/IR) -> posición -> jugador. Usa el rankDraft.rating que da la
propia Fleaflicker (temporada actual y anterior).

AVISO: probado contra un JSON real de FetchRoster (temporada 2026), pero
la llamada con season=2025 no se ha podido verificar. Revisar el
resultado de la primera ejecución.

Requiere variable de entorno: FLEAFLICKER_USER_ID
"""

import json
import os
from datetime import datetime, timezone

import requests

BASE_URL = "https://www.fleaflicker.com/api"
USER_ID = os.environ["FLEAFLICKER_USER_ID"]
OUTPUT = "docs/data/rosters.json"
SEASON_ACTUAL = int(os.environ.get("SEASON", "2026"))
SEASON_ANTERIOR = SEASON_ACTUAL - 1

RATING_VALOR = {
    "RATING_VERY_GOOD": 4,
    "RATING_GOOD": 3,
    "RATING_AVERAGE": 2,  # no confirmado en datos reales, por si existiera
    "RATING_BAD": 1,
    "RATING_VERY_BAD": 0,
}

HUECOS = ["START", "BENCH", "TAXI", "INJURED"]


def get(endpoint, params):
    params["sport"] = "NFL"
    r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def extraer_slots(nodo, grupo=None):
    """Recorre recursivamente FetchRoster y devuelve (grupo, leaguePlayer)
    para cada jugador, determinando el hueco por el 'position' del slot."""
    if isinstance(nodo, dict):
        pos = nodo.get("position")
        if isinstance(pos, dict):
            g = pos.get("group")
            label = pos.get("label")
            if isinstance(g, str):
                grupo = g
            elif label == "BN":
                grupo = "BENCH"
            elif label == "IR":
                grupo = "INJURED"
            elif label == "TAXI":
                grupo = "TAXI"
        elif isinstance(nodo.get("group"), str):
            grupo = nodo["group"]
        if "leaguePlayer" in nodo and isinstance(nodo["leaguePlayer"], dict):
            yield grupo, nodo["leaguePlayer"]
        for v in nodo.values():
            yield from extraer_slots(v, grupo)
    elif isinstance(nodo, list):
        for item in nodo:
            yield from extraer_slots(item, grupo)


def rank_por_jugador(roster_json):
    """Devuelve {jugador_id: rankDraft} para una respuesta de FetchRoster,
    usada para cruzar la temporada anterior con la actual."""
    resultado = {}
    for _, lp in extraer_slots(roster_json):
        pid = (lp.get("proPlayer") or {}).get("id")
        if pid is not None and "rankDraft" in lp:
            resultado[pid] = lp["rankDraft"]
    return resultado


def nota(valores):
    """Convierte una lista de valores 0-4 en {letra, valor, n}."""
    vals = [v for v in valores if v is not None]
    if not vals:
        return {"letra": None, "valor": None, "n": 0}
    media = sum(vals) / len(vals)
    if media >= 3.5:
        letra = "A+"
    elif media >= 3.0:
        letra = "A"
    elif media >= 2.5:
        letra = "B+"
    elif media >= 2.0:
        letra = "B"
    elif media >= 1.0:
        letra = "C"
    else:
        letra = "D"
    return {"letra": letra, "valor": round(media, 2), "n": len(vals)}


def procesar_equipo(league_id, team_id):
    roster_actual = get(
        "FetchRoster",
        {"league_id": league_id, "team_id": team_id, "season": SEASON_ACTUAL},
    )
    try:
        roster_anterior = get(
            "FetchRoster",
            {"league_id": league_id, "team_id": team_id, "season": SEASON_ANTERIOR},
        )
        ranks_anteriores = rank_por_jugador(roster_anterior)
    except requests.RequestException:
        ranks_anteriores = {}

    huecos = {h: {"posiciones": {}, "valores": []} for h in HUECOS}
    valores_generales = []

    vistos = set()
    for grupo, lp in extraer_slots(roster_actual):
        pp = lp.get("proPlayer") or {}
        pid = pp.get("id")
        if pid is None or (grupo, pid) in vistos:
            continue
        vistos.add((grupo, pid))

        if grupo not in huecos:
            continue

        rank_draft = lp.get("rankDraft") or {}
        posiciones = rank_draft.get("positions") or []
        rank_ant = ranks_anteriores.get(pid, {})
        posiciones_ant = {
            p.get("position", {}).get("label"): p
            for p in (rank_ant.get("positions") or [])
        }

        if not posiciones:
            continue

        valor_principal = RATING_VALOR.get(posiciones[0].get("rating"))
        valores_generales.append(valor_principal)
        huecos[grupo]["valores"].append(valor_principal)

        for pos_info in posiciones:
            label = pos_info.get("position", {}).get("label", "?")
            valor = RATING_VALOR.get(pos_info.get("rating"))
            ant = posiciones_ant.get(label)

            bucket = huecos[grupo]["posiciones"].setdefault(
                label, {"jugadores": [], "valores": []}
            )
            bucket["valores"].append(valor)
            bucket["jugadores"].append(
                {
                    "nombre": pp.get("nameFull", ""),
                    "equipo_nfl": pp.get("proTeamAbbreviation", ""),
                    "rank_actual": pos_info.get("formatted", ""),
                    "rating_actual": pos_info.get("rating"),
                    "rank_anterior": ant.get("formatted") if ant else None,
                    "rating_anterior": ant.get("rating") if ant else None,
                }
            )

    for h in HUECOS:
        for label, bucket in huecos[h]["posiciones"].items():
            bucket["nota"] = nota(bucket["valores"])
            del bucket["valores"]
            bucket["jugadores"].sort(key=lambda j: j["nombre"])
        huecos[h]["nota"] = nota(huecos[h]["valores"])
        del huecos[h]["valores"]

    return {
        "nota_general": nota(valores_generales),
        "huecos": huecos,
    }


def main():
    data = get("FetchUserLeagues", {"user_id": USER_ID})
    leagues = data.get("leagues", [])

    resultado = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "season": SEASON_ACTUAL,
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
        }
        try:
            entrada.update(procesar_equipo(league_id, team_id))
        except requests.RequestException as e:
            entrada["error"] = str(e)

        resultado["ligas"].append(entrada)
        print(f"{entrada['liga']}: nota general {entrada.get('nota_general', {}).get('letra')}")

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=1)
    print(f"Escrito {OUTPUT}")


if __name__ == "__main__":
    main()
