"""
export_rosters.py — Genera docs/data/rosters.json con la valoración del
roster de cada equipo, en niveles: general -> posición (todo el roster)
/ hueco (Titular/Banco/Taxi/IR) -> posición dentro del hueco -> jugador.

CRITERIO DE VALORACIÓN (por jugador y posición):
1. rankFantasy.positions[].ordinal — posición real que ocupa ese
   jugador dentro de su posición, según FetchPlayerListing. En
   pretemporada refleja la última temporada completa jugada; en
   temporada regular refleja el rendimiento real de la temporada en
   curso (Fleaflicker usa siempre los datos reales más recientes).
2. Si no existe -> rankDraft.positions[].ordinal, de FetchRoster
   (proyección de pretemporada). AVISO: se ha comprobado que este campo
   desaparece por completo una vez empieza la temporada regular, así
   que en la práctica solo se usa durante la pretemporada.
3. Si tampoco existe -> letra "R". SUPOSICIÓN: se asume que corresponde
   a un rookie o jugador sin datos suficientes; no se puede confirmar
   con certeza en todos los casos.

IMPORTANTE: las posiciones que cuentan para cada jugador se determinan
a partir de proPlayer.positionEligibility (siempre presente), NO a
partir de si rankDraft/rankFantasy existen — así no se pierde a ningún
jugador solo porque uno de los dos campos esté vacío.

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

ORDEN_POSICIONES = ["QB", "RB", "WR", "TE", "K", "P", "CB", "S", "EDR", "IL", "LB"]

VALOR_LETRA = {
    "A+": 10, "A": 9, "B+": 8, "B": 7, "C+": 6, "C": 5,
    "D+": 4, "D": 3, "E+": 2, "E": 1, "F": 0,
}

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
            import time
            time.sleep(6 * (intento + 1))
            continue
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
    if ordinal is None or ordinal < 1:
        return None, None
    idx_tramo = (ordinal - 1) // 12
    pos_en_tramo = (ordinal - 1) % 12
    letras_base = ["A", "B", "C", "D", "E"]
    if idx_tramo >= len(letras_base):
        letra = "F"
    else:
        letra = letras_base[idx_tramo] + ("+" if pos_en_tramo < 6 else "")
    return letra, VALOR_LETRA[letra]


def letra_desde_media(media):
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
    devuelve {player_id: {label: ordinal}}. Tolerante a fallos."""
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

    # Primera pasada: recoger jugadores. Las posiciones que cuentan para
    # cada uno salen de positionEligibility (SIEMPRE presente), no de
    # rankDraft (que desaparece en temporada regular) ni de rankFantasy
    # (que puede faltar para jugadores sin apenas uso).
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

        labels_elegibles = pp.get("positionEligibility") or []
        if not labels_elegibles:
            continue

        # ordinal de respaldo (rankDraft), si existiera, indexado por label
        rankdraft_por_label = {
            p.get("position", {}).get("label"): p.get("ordinal")
            for p in (lp.get("rankDraft") or {}).get("positions") or []
        }

        capturados.append((grupo, pp, labels_elegibles, rankdraft_por_label))
        ids_para_fantasy.add(pid)

    rank_fantasy = obtener_rank_fantasy(league_id, ids_para_fantasy)

    huecos = {h: {"posiciones": {}, "valores": []} for h in HUECOS}
    posiciones_equipo = {}
    valores_generales = []

    for grupo, pp, labels_elegibles, rankdraft_por_label in capturados:
        pid = pp.get("id")
        fantasy_jugador = rank_fantasy.get(pid, {})

        primero = True
        for label in labels_elegibles:
            ordinal_fantasy = fantasy_jugador.get(label)
            ordinal_draft = rankdraft_por_label.get(label)

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
                "fuente": fuente,  # 'fantasy' (real) | 'draft' (proyección) | None
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
