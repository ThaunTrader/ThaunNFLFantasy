"""
export_rosters.py — Genera docs/data/rosters.json con la valoración del
roster de cada equipo, en niveles: general -> posición (todo el roster)
/ hueco (Titular/Banco/Taxi/IR) -> posición dentro del hueco -> jugador.
Usa el rankDraft.rating que da la propia Fleaflicker.

Nota: se descartó comparar con la temporada anterior (2025) porque
Fleaflicker no devolvía ese dato de forma fiable para esta liga.

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

RATING_VALOR = {
    "RATING_VERY_GOOD": 4,
    "RATING_GOOD": 3,
    "RATING_AVERAGE": 2,  # no confirmado en datos reales, por si existiera
    "RATING_BAD": 1,
    "RATING_VERY_BAD": 0,
}

HUECOS = ["START", "BENCH", "TAXI", "INJURED"]

# Orden fijo de posiciones pedido por el usuario. Cualquier posición no
# listada aquí (p.ej. si aparecen datos de K o P, que de momento no se
# han visto con rankDraft) se añade al final por orden alfabético.
ORDEN_POSICIONES = ["QB", "RB", "WR", "TE", "K", "P", "CB", "S", "EDR", "IL", "LB"]


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


def letra_desde_media(media):
    if media >= 3.5:
        return "A+"
    if media >= 3.0:
        return "A"
    if media >= 2.5:
        return "B+"
    if media >= 2.0:
        return "B"
    if media >= 1.0:
        return "C"
    return "D"


def nota(valores):
    """Convierte una lista de valores 0-4 en {letra, valor, n}."""
    vals = [v for v in valores if v is not None]
    if not vals:
        return {"letra": None, "valor": None, "n": 0}
    media = sum(vals) / len(vals)
    return {"letra": letra_desde_media(media), "valor": round(media, 2), "n": len(vals)}


def orden_key(label):
    try:
        return (0, ORDEN_POSICIONES.index(label))
    except ValueError:
        return (1, label)


def ordenar_posiciones(dic_posiciones):
    """Lista de (label, bucket) ordenada según ORDEN_POSICIONES, con las
    no listadas al final por orden alfabético."""
    return sorted(dic_posiciones.items(), key=lambda kv: orden_key(kv[0]))


def procesar_equipo(league_id, team_id):
    roster = get(
        "FetchRoster",
        {"league_id": league_id, "team_id": team_id, "season": SEASON_ACTUAL},
    )

    huecos = {h: {"posiciones": {}, "valores": []} for h in HUECOS}
    posiciones_equipo = {}  # agregación de TODO el roster, por posición
    valores_generales = []

    vistos = set()
    for grupo, lp in extraer_slots(roster):
        pp = lp.get("proPlayer") or {}
        pid = pp.get("id")
        if pid is None or (grupo, pid) in vistos:
            continue
        vistos.add((grupo, pid))

        if grupo not in huecos:
            continue

        rank_draft = lp.get("rankDraft") or {}
        posiciones = rank_draft.get("positions") or []
        if not posiciones:
            continue

        valor_principal = RATING_VALOR.get(posiciones[0].get("rating"))
        valores_generales.append(valor_principal)
        huecos[grupo]["valores"].append(valor_principal)

        for pos_info in posiciones:
            label = pos_info.get("position", {}).get("label", "?")
            valor = RATING_VALOR.get(pos_info.get("rating"))
            ordinal = pos_info.get("ordinal")

            jugador_data = {
                "nombre": pp.get("nameFull", ""),
                "equipo_nfl": pp.get("proTeamAbbreviation", ""),
                "rank": pos_info.get("formatted", ""),
                "rank_ordinal": ordinal,
                "rating": pos_info.get("rating"),
                "nota_letra": letra_desde_media(valor) if valor is not None else None,
                "hueco": grupo,
            }

            bucket_hueco = huecos[grupo]["posiciones"].setdefault(
                label, {"jugadores": [], "valores": []}
            )
            bucket_hueco["valores"].append(valor)
            bucket_hueco["jugadores"].append(jugador_data)

            bucket_equipo = posiciones_equipo.setdefault(
                label, {"jugadores": [], "valores": []}
            )
            bucket_equipo["valores"].append(valor)
            bucket_equipo["jugadores"].append(jugador_data)

    def orden_jugador(j):
        # Menor ordinal = mejor ranking. Sin ordinal -> al final.
        return (j["rank_ordinal"] is None, j["rank_ordinal"] if j["rank_ordinal"] is not None else 0)

    for h in HUECOS:
        for label, bucket in huecos[h]["posiciones"].items():
            bucket["nota"] = nota(bucket["valores"])
            del bucket["valores"]
            bucket["jugadores"].sort(key=orden_jugador)
        huecos[h]["nota"] = nota(huecos[h]["valores"])
        del huecos[h]["valores"]

    for label, bucket in posiciones_equipo.items():
        bucket["nota"] = nota(bucket["valores"])
        del bucket["valores"]
        bucket["jugadores"].sort(key=orden_jugador)

    posiciones_equipo_ordenado = {
        label: bucket for label, bucket in ordenar_posiciones(posiciones_equipo)
    }

    return {
        "nota_general": nota(valores_generales),
        "posiciones": posiciones_equipo_ordenado,
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
