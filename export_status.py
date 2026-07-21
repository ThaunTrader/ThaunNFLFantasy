"""
export_status.py — Genera docs/data/status.json con el estado de lesionados
de todos los equipos del usuario en Fleaflicker.

No toca nada del sistema de alertas Telegram (check_lineup.py).

Requiere variable de entorno: FLEAFLICKER_USER_ID
"""

import json
import os
from datetime import datetime, timezone

import requests

BASE_URL = "https://www.fleaflicker.com/api"
USER_ID = os.environ["FLEAFLICKER_USER_ID"]
OUTPUT = "docs/data/status.json"

# Mapeo liga -> (tipo, formato), según IDPs.md / Classic.md / SuperFlex.md
# del proyecto, más ligas confirmadas manualmente por el usuario. Si una
# liga no aparece aquí, se marca como "no documentado" en vez de asumir
# un valor.
FORMATO_LIGAS = {
    332443: ("Dynasty", "IDP"),
    332475: ("Dynasty", "IDP"),
    332572: ("Dynasty", "IDP"),
    332766: ("Dynasty", "IDP"),
    332793: ("Dynasty", "IDP"),
    339833: ("Dynasty", "IDP"),
    344345: ("Dynasty", "IDP"),
    333726: ("Dynasty", "IDP"),  # Hot Rod Dynasty
    332573: ("Dynasty", "Classic"),
    332824: ("Dynasty", "Classic"),
    332571: ("Dynasty", "SuperFlex"),
    332993: ("Dynasty", "SuperFlex"),
    324737: ("Redraft", "Classic Flex-DP"),  # El Capologist
}

# Estados que generan alerta (mismo criterio que check_lineup.py)
ESTADOS_ALERTA = {"QUESTIONABLE", "DOUBTFUL", "OUT", "IR"}
# Estados rojos (no juega) vs ámbar (duda)
ESTADOS_ROJOS = {"OUT", "IR"}

ABREV_A_ESTADO = {
    "Q": "QUESTIONABLE",
    "D": "DOUBTFUL",
    "O": "OUT",
    "IR": "IR",
}


def get(endpoint, params):
    params["sport"] = "NFL"
    r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def extraer_jugadores(nodo, grupo=None):
    """Recorre recursivamente la respuesta de FetchRoster y devuelve
    tuplas (grupo, proPlayer). Tolerante a cambios de estructura."""
    if isinstance(nodo, dict):
        if isinstance(nodo.get("group"), str):
            grupo = nodo["group"]
        if "proPlayer" in nodo and isinstance(nodo["proPlayer"], dict):
            yield grupo, nodo["proPlayer"]
        for valor in nodo.values():
            yield from extraer_jugadores(valor, grupo)
    elif isinstance(nodo, list):
        for item in nodo:
            yield from extraer_jugadores(item, grupo)


def extraer_record(team):
    """Extrae wins/losses/ties/win_percentage/rank de team.recordOverall.
    Tolerante a ausencia del campo (p.ej. pretemporada sin partidos)."""
    rec = (team or {}).get("recordOverall") or {}
    if not rec:
        return None
    win_pct = rec.get("winPercentage")
    if isinstance(win_pct, dict):
        win_pct = win_pct.get("value")
    return {
        "victorias": rec.get("wins", 0),
        "derrotas": rec.get("losses", 0),
        "empates": rec.get("ties", 0),
        "pct": win_pct,
        "formateado": rec.get("formatted", ""),
        "rank": rec.get("rank"),
    }


def normalizar_estado(injury):
    """Devuelve (estado, detalle) a partir del objeto injury, o (None, None)
    si el jugador no está en un estado de alerta."""
    if not injury:
        return None, None

    candidatos = []
    abrev = str(injury.get("typeAbbreviaition", "")).upper().strip()
    if abrev in ABREV_A_ESTADO:
        candidatos.append(ABREV_A_ESTADO[abrev])
    if abrev in ESTADOS_ALERTA:
        candidatos.append(abrev)

    for campo in ("typeFull", "severity"):
        valor = str(injury.get(campo, "")).upper().strip()
        for estado in ESTADOS_ALERTA:
            if estado == valor or (len(valor) > 2 and estado in valor):
                candidatos.append(estado)

    if not candidatos:
        return None, None

    # Prioridad: el estado más grave
    orden = ["IR", "OUT", "DOUBTFUL", "QUESTIONABLE"]
    estado = next(e for e in orden if e in candidatos)
    detalle = injury.get("description") or injury.get("typeFull") or ""
    return estado, detalle


def main():
    data = get("FetchUserLeagues", {"user_id": USER_ID})
    leagues = data.get("leagues", [])

    resultado = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ligas": [],
    }

    for lg in leagues:
        team = lg.get("ownedTeam") or {}
        league_id = lg.get("id")
        team_id = team.get("id")
        if not league_id or not team_id:
            continue

        tipo, formato = FORMATO_LIGAS.get(league_id, (None, None))

        entrada = {
            "league_id": league_id,
            "liga": lg.get("name", ""),
            "team_id": team_id,
            "equipo": team.get("name", ""),
            "tipo": tipo,
            "formato": formato,
            "record": extraer_record(team),
            "total_roster": 0,
            "alertas": [],
        }

        try:
            roster = get(
                "FetchRoster", {"league_id": league_id, "team_id": team_id}
            )
        except requests.RequestException as e:
            entrada["error"] = str(e)
            resultado["ligas"].append(entrada)
            continue

        vistos = set()
        for grupo, jugador in extraer_jugadores(roster):
            jid = jugador.get("id")
            if jid in vistos:
                continue
            vistos.add(jid)
            entrada["total_roster"] += 1

            estado, detalle = normalizar_estado(jugador.get("injury"))
            if estado:
                entrada["alertas"].append(
                    {
                        "nombre": jugador.get("nameFull", ""),
                        "posicion": jugador.get("position", ""),
                        "equipo_nfl": jugador.get("proTeamAbbreviation", ""),
                        "grupo": grupo or "",
                        "estado": estado,
                        "rojo": estado in ESTADOS_ROJOS,
                        "detalle": detalle,
                    }
                )

        # Rojos primero, luego dudas
        entrada["alertas"].sort(key=lambda a: (not a["rojo"], a["nombre"]))
        resultado["ligas"].append(entrada)
        print(
            f"{entrada['liga']} — {entrada['equipo']}: "
            f"{len(entrada['alertas'])} alerta(s) de {entrada['total_roster']} jugadores"
        )

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=1)
    print(f"Escrito {OUTPUT}")


if __name__ == "__main__":
    main()
