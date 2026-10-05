name: NFL Lineup Injury Check

on:
  workflow_dispatch:
  schedule:
    # Horarios pensados en hora española, cubriendo tanto CET (invierno,
    # UTC+1) como CEST (verano, UTC+2) con líneas solapadas. Puede haber
    # alguna ejecución "de más" fuera de la franja exacta según la época
    # del año, pero nunca faltará ninguna. Franjas objetivo (hora
    # española): domingo 14:30 (partidos internacionales en Europa,
    # algunas semanas), domingo 17:00 a lunes 02:00, martes 01:00-02:00,
    # jueves 01:00-02:00.
    - cron: '30 12-13 * * 0'  # Domingo 14:30 hora española (partido Europa, todas las semanas por si acaso)
    - cron: '0 15-23 * * 0'   # Domingo tarde/noche (ambos regímenes)
    - cron: '0 0-1 * * 1'     # Lunes madrugada, cola del domingo (ambos regímenes)
    - cron: '0 23 * * 1'      # Lunes 23:00 UTC = martes 01:00 en CEST
    - cron: '0 0-1 * * 2'     # Martes madrugada en CET + margen
    - cron: '0 23 * * 3'      # Miércoles 23:00 UTC = jueves 01:00 en CEST
    - cron: '0 0-1 * * 4'     # Jueves madrugada en CET + margen

jobs:
  check-lineup:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install requests

      - name: Run lineup check
        env:
          FLEAFLICKER_USER_ID: ${{ secrets.FLEAFLICKER_USER_ID }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python check_lineup.py
