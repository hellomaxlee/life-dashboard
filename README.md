# Life Dashboard

### My first adventure into the realm of Hardware!

## Stack<img width="518" height="461" alt="Screenshot 2026-11-17 at 8 39 08 PM" src="https://github.com/user-attachments/assets/a5c278c3-2d64-43c4-97de-6e8ecdff1ee7" />

I got a Divoom Pixoo 64 (LED display) and coded up a dashboard of various rotating wellbeing metrics. I'll get a Raspberry Pi one day but currently it depends on my MacBook being on.

![The five rotation screens: Today, Weather, Week, Month, Books](docs/images/rotation.png)

- Sleep and workout data that I linked with the iOS Health Auto Export app via REST API
- Delays for the various bus and train lines I pull from the MTA alerts page
- Book progress within a year via my public Goodreads RSS feed
- Daily weather
- A monthly feature synthesizing some aspect(s) of data collection
## Screens

| Today | Week | Books |
|---|---|---|
| ![Today](docs/images/today.png) | ![Week](docs/images/week.png) | ![Books](docs/images/books.png) |

| Weather | Transit | Month |
|---|---|---|
| ![Weather](docs/images/city-weather.png) | ![Transit](docs/images/city-transit.png) | ![Month](docs/images/month.png) |

Small wins and a completed week get a short celebration, sent to the panel as a sequence of stills:

| Workout win | Sleep win | Week done |
|---|---|---|
| ![Workout sparkle](docs/images/sparkle-workout.gif) | ![Sleep sparkle](docs/images/sparkle-sleep.gif) | ![Week party](docs/images/party.gif) |

- Python 3.12, uv, FastAPI, SQLite (stdlib, hand-written migrations), APScheduler, httpx, Pillow, pytest, ruff.
- Anthropic SDK for the daily line, the month feature, and a heart-rate "coach" that can credit a sustained effort the feed missed. Model ids and the monthly cap live in `config.toml`.
