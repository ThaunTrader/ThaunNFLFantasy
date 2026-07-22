"""
export_rosters.py — Genera docs/data/rosters.json con la valoración del
roster de cada equipo, en niveles: general -> posición (todo el roster)
/ hueco (Titular/Banco/Taxi/IR) -> posición dentro del hueco -> jugador.

CRITERIO DE VALORACIÓN (por jugador y posición):
1. rankFantasy.positions[].ordinal — posición real que ocupó ese
   jugador dentro de su posición en la temporada anterior (2025), según
   FetchPlayerListing. Es el dato preferente por ser rendimiento real.
2. Si no existe (típico en rookies) -> rankDraft.positions[].ordinal,
   de FetchRoster (proyección de pretemporada 2026).
3. Si tampoco existe -> letra "R". SUPOSICIÓN: se asume que esto
   corresponde a un rookie sin temporada anterior jugada; no se puede
   confirmar con certeza (podría ser p.ej. alguien en practice squad).

Tramos (12 jugadores por nivel, confirmados por el usuario):
  1-6   A+      13-18  B+      25-30  C+      37-42  D+      49-54  E+
  7-12  A       19-24  B       31-36  C       43-48  D       55-60  E
  61+   F (sin +)

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

HUECOS = ["START", "BENCH", "TAXI", "INJURED"]

# Orden fijo de posiciones pedido por el usuario. Cualquier posición no
# listada aquí se añade al final por orden alfabético.
ORDEN_POSICIONES = ["QB", "RB", "WR", "TE", "K", "P", "CB", "S", "EDR", "IL", "LB"]

# Valor numérico por letra, para poder promediar en hueco/general.
# Escala 0-10, un punto por cada tramo (confirmada con el usuario).
VALOR_LETRA = {
    "A+": 10, "A": 9, "B+": 8, "B": 7, "C+": 6, "C": 5,
    "D+": 4, "D": 3, "E+": 2, "E": 1, "F": 0,
}


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


def letra_desde_ordinal(ordinal):
    """Convierte un ordinal (1, 2, 3...) en letra + valor numérico,
    según tramos de 12 confirmados por el usuario."""
    if ordinal is None or ordinal < 1:
        return None, None
    idx_tramo = (ordinal - 1) // 12  # 0=A, 1=B, 2=C, 3=D, 4=E, 5+=F
    pos_en_tramo = (ordinal - 1) % 12  # 0..11 dentro del tramo
    letras_base = ["A", "B", "C", "D", "E"]
    if idx_tramo >= len(letras_base):
        letra = "F"
    else:
        letra = letras_base[idx_tramo] + ("+" if pos_en_tramo < 6 else "")
    return letra, VALOR_LETRA[letra]


def letra_desde_media(media):
    """Convierte una media 0-10 en letra, usando los puntos medios entre
    los valores discretos de VALOR_LETRA como cortes."""
    if media >= 9.5:
        return "A+"
    if media >= 8.5:
        return "A"
    if media >= 7.5:
        return "B+"
    if media >= 6.5:
        return "B"
    if media >= 5.5:
        return "C+"
    if media >= 4.5:
        return "C"
    if media >= 3.5:
        return "D+"
    if media >= 2.5:
        return "D"
    if media >= 1.5:
        return "E+"
    if media >= 0.5:
        return "E"
    return "F"


def nota(valores):
    """Convierte una lista de valores 0-10 (excluyendo None, i.e. "R")
    en {letra, valor, n}."""
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
    return sorted(dic_posiciones.items(), key=lambda kv: orden_key(kv[0]))


def obtener_rank_fantasy(league_id, player_ids):
    """Llama a FetchPlayerListing una vez con todos los ids del roster y
    devuelve {player_id: {label: ordinal}} con el rankFantasy (temporada
    anterior) de cada jugador. Tolerante a fallos: si la llamada falla,
    devuelve {} y todo cae al respaldo de rankDraft."""
    if not player_ids:
        return {}
    try:
        resp = get(
            "FetchPlayerListing",
            {
                "league_id": league_id,
                "sort": "SORT_LAST_X_SHORT",
                "filter.player_id": list(player_ids),
            },
        )
    except requests.RequestException:
        return {}

    resultado = {}
    for item in resp.get("players", []):
        pid = (item.get("proPlayer") or {}).get("id")
        if pid is None:
            continue
        rf = item.get("rankFantasy") or {}
        por_label = {}
        for p in rf.get("positions") or []:
            label = (p.get("position") or {}).get("label")
            if label:
                por_label[label] = p.get("ordinal")
        resultado[pid] = por_label
    return resultado


def procesar_equipo(league_id, team_id):
    roster = get(
        "FetchRoster",
        {"league_id": league_id, "team_id": team_id, "season": SEASON_ACTUAL},
    )

    # Primera pasada: recoger jugadores y sus ids para pedir rankFantasy
    # de todos de una vez (una sola llamada extra por equipo).
    capturados = []
    ids_para_fantasy = set()
    vistos = set()
    for grupo, lp in extraer_slots(roster):
        pp = lp.get("proPlayer") or {}
        pid = pp.get("id")
        if pid is None or (grupo, pid) in vistos:
            continue
        vistos.add((grupo, pid))
        if grupo not in HUECOS:
            continue
        posiciones_draft = (lp.get("rankDraft") or {}).get("positions") or []
        if not posiciones_draft:
            continue
        capturados.append((grupo, pp, posiciones_draft))
        ids_para_fantasy.add(pid)

    rank_fantasy = obtener_rank_fantasy(league_id, ids_para_fantasy)

    huecos = {h: {"posiciones": {}, "valores": []} for h in HUECOS}
    posiciones_equipo = {}
    valores_generales = []

    for grupo, pp, posiciones_draft in capturados:
        pid = pp.get("id")
        fantasy_jugador = rank_fantasy.get(pid, {})

        primero = True
        for pos_info in posiciones_draft:
            label = pos_info.get("position", {}).get("label", "?")
            ordinal_draft = pos_info.get("ordinal")
            ordinal_fantasy = fantasy_jugador.get(label)

            if ordinal_fantasy is not None:
                ordinal_final, fuente = ordinal_fantasy, "fantasy"
            elif ordinal_draft is not None:
                ordinal_final, fuente = ordinal_draft, "draft"
            else:
                ordinal_final, fuente = None, None

            if ordinal_final is not None:
                letra, valor = letra_desde_ordinal(ordinal_final)
                rank_str = f"{label}{ordinal_final}"
            else:
                letra, valor = "R", None
                rank_str = "—"

            if primero:
                valores_generales.append(valor)
                huecos[grupo]["valores"].append(valor)
                primero = False

            jugador_data = {
                "nombre": pp.get("nameFull", ""),
                "equipo_nfl": pp.get("proTeamAbbreviation", ""),
                "rank": rank_str,
                "rank_ordinal": ordinal_final,
                "nota_letra": letra,
                "fuente": fuente,  # 'fantasy' (2025 real) | 'draft' (2026 proyección) | None
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
        # Menor ordinal = mejor. Sin ordinal (R) al final.
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
