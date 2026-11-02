# Mareas Las Grutas — versión web

Esta es una primera adaptación web del programa de escritorio. Conserva la lógica de lectura de mareas, interpolación, clasificación y consulta de Open-Meteo.

## Requisitos
- Python 3.10 o superior
- Conexión a internet

## Ejecutar en Windows (Visual Studio Code o CMD)

1. Descomprimí la carpeta.
2. Abrí una terminal dentro de `mareas_las_grutas_web`.
3. (Opcional pero recomendado) Creá un entorno virtual:
   `py -m venv .venv`
4. Activá el entorno:
   `.venv\Scripts\activate`
5. Instalá las dependencias:
   `py -m pip install -r requirements.txt`
6. Ejecutá:
   `py app.py`
7. Abrí en el navegador:
   `http://127.0.0.1:5000`

## Publicación
Para Render, el comando de inicio habitual es:
`gunicorn app:app`

Configuración:
- Runtime: Python
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`

## Consideraciones importantes
- La página consulta las fuentes de mareas configuradas en `app.py` y Open-Meteo.
- Los datos se mantienen en caché en memoria durante 15 minutos para evitar consultar las fuentes en cada visita.
- El scraping depende de la estructura de las páginas fuente. Si alguna cambia, habrá que ajustar el lector.
- La altura es una estimación interpolada usando el Puerto San Antonio Este como referencia; no es una medición exacta en cada punto de Las Grutas.
- La gráfica usa Chart.js desde CDN, por lo que requiere conexión a internet.
