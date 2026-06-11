# ThaunNFL — Lineup Injury Alerts

Comprueba automáticamente el estado de los jugadores titulares en todas tus ligas de Fleaflicker y envía una alerta por Telegram si alguno está QUESTIONABLE, DOUBTFUL, OUT o en IR.

## Configuración

Añade estos tres Secrets en tu repositorio (Settings → Secrets and variables → Actions):

| Secret | Descripción |
|---|---|
| `FLEAFLICKER_USER_ID` | Tu user ID de Fleaflicker |
| `TELEGRAM_BOT_TOKEN` | Token de tu bot de Telegram |
| `TELEGRAM_CHAT_ID` | Tu chat ID de Telegram |

## Cuándo se ejecuta

Automáticamente cada hora entre las 17:00 y 23:00 UTC los **jueves, domingos y lunes** (días de partidos NFL).

También puedes lanzarlo manualmente desde la pestaña **Actions** de GitHub.

## Ejecución manual local

```bash
pip install requests
FLEAFLICKER_USER_ID=xxx TELEGRAM_BOT_TOKEN=yyy TELEGRAM_CHAT_ID=zzz python check_lineup.py
```
