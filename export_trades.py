"""
export_trades.py — Genera docs/data/trades.json con los trades PENDIENTES
(en revisión) que afectan a los equipos del usuario en cada liga. No
incluye histórico de trades completados.

AVISO: el parseo de la respuesta de FetchTrades es defensivo (recorre la
estructura buscando equipos/jugadores/picks) porque no se ha podido
verificar contra la API real. Revisar el resultado de la primera ejecución.

Requiere variable de entorno: FLEAFLICKER_USER_ID
"""

import json
import os
from datetime import datetime, timezone

import requests

BASE_URL = "https://www.fleaflicker.com/api"
USER_ID = os.environ["FLEAFLICKER_USER_ID"]
OUTPUT = "docs/data/trades.json"


def get(endpoint, params):
    params["sport"] = "NFL"
    r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def buscar_jugadores_y_picks(nodo):
    """Recorre recursivamente un 'lado' del trade y devuelve
    ([jugadores], [picks]) encontrados en esa rama."""
    jugadores = []
    picks = []

    def recorrer(n):
        if isinstance(n, dict):
            if "proPlayer" in n and isinstance(n["proPlayer"], dict):
                jugadores.append(n["proPlayer"].get("nameFull", "Jugador"))
            if "slot" in n and "season" in n and "player" in n:
                p = n.get("player") or {}
                picks.append(
                    f"Pick {n.get('season','')} ({p.get('nameFull') or 'ronda ' + str(n.get('slot',''))})"
                )
            for v in n.values():
                recorrer(v)
        elif isinstance(n, list):
            for item in n:
                recorrer(item)

    recorrer(nodo)
    return jugadores, picks


def procesar_trade(trade, team_id_usuario):
    lados = trade.get("teams", [])
    resumen_lados = []
    involucra_usuario = False

    for lado in lados:
        equipo = lado.get("team", {}) if isinstance(lado, dict) else {}
        nombre_equipo = equipo.get("name", "Equipo desconocido")
        if equipo.get("id") == team_id_usuario:
            involucra_usuario = True
        jugadores, picks = buscar_jugadores_y_picks(lado)
        resumen_lados.append(
            {"equipo": nombre_equipo, "recibe": jugadores + picks}
        )

    if not involucra_usuario:
        return None

    return {
        "id": trade.get("id"),
        "estado": trade.get("status", ""),
        "descripcion": trade.get("description", ""),
        "propuesto": trade.get("proposedOn"),
        "aprobado": trade.get("approvedOn"),
        "lados": resumen_lados,
    }


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

        entrada = {
            "league_id": league_id,
            "liga": lg.get("name", ""),
            "equipo": team.get("name", ""),
            "trades": [],
        }

        try:
            abiertos = get(
                "FetchTrades", {"league_id": league_id, "filter": "TRADES_UNDER_REVIEW"}
            )
        except requests.RequestException as e:
            entrada["error"] = str(e)
            resultado["ligas"].append(entrada)
            continue

        vistos = set()
        for t in abiertos.get("trades", []):
            tid = t.get("id")
            if tid in vistos:
                continue
            procesado = procesar_trade(t, team_id)
            if procesado:
                vistos.add(tid)
                entrada["trades"].append(procesado)

        entrada["trades"].sort(key=lambda t: t.get("propuesto") or 0, reverse=True)
        resultado["ligas"].append(entrada)
        print(f"{entrada['liga']}: {len(entrada['trades'])} trade(s) relevante(s)")

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=1)
    print(f"Escrito {OUTPUT}")


if __name__ == "__main__":
    main()
