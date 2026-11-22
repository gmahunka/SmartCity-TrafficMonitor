# SmartCity-TrafficMonitor

Traffic monitoring project at the Budapest University of Technology and Economics.
The CV runner uses YOLO and ByteTrack to count vehicles from one camera. The
dashboard displays completed minute counts, the annotated stream, and traffic forecasts.

## Quick start

With Docker Compose available, copy `.env.example` to `.env`. Set `TRAFFIC_SOURCE`
to a YouTube, HTTP, or RTSP stream URL and choose a stable `TRAFFIC_CAMERA` ID.

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Open the [dashboard](http://127.0.0.1:8001/dashboard). Check
[CV health](http://127.0.0.1:8002/health) for frame age, processed FPS, and reconnects.
Use `docker compose logs -f cv` to follow the runner.

## Configuration and operation

- For LAN access, set `TRAFFIC_BIND_IP` to the host's LAN IP and
  `TRAFFIC_STREAM_URL` to `http://<host-lan-ip>:8002/stream`. Open
  `http://<host-lan-ip>:8001/dashboard`.
- Set `TRAFFIC_TIMEZONE` for dashboard dates. The default is `Europe/Budapest`.
  Lower `TRAFFIC_MAX_FPS` or `TRAFFIC_MAX_HEIGHT` if inference uses too much CPU.
- Both services use one SQLite database in the `traffic-data` volume. Use a
  separate database for each camera. Restarts, rebuilds, and `docker compose down`
  preserve history. **`docker compose down --volumes` deletes it.**
- The dashboard clear action is disabled by default in Compose. Set
  `TRAFFIC_ALLOW_CLEAR=true` to enable it. Clearing removes events, counts, and
  saved forecasts.
- Both services have an automatic restart policy. For unattended operation,
  configure Docker to start at boot and verify recovery after a reboot and a
  camera outage.

## Backup and restore

Choose a new backup filename each time. Run these commands while the dashboard
service is up. The snapshot reports its row counts and the copy saves it on the host.

```sh
docker compose exec -T dashboard python -m data.backup ../var/lib/traffic/traffic.db ../tmp/traffic-backup.db
docker compose cp dashboard:/tmp/traffic-backup.db ./traffic-backup.db
```

For scheduled backups, run both commands from the project directory with a dated
filename and keep the copy on separate storage.

Restore into a fresh Compose project and compare the row counts. The temporary
container gives the snapshot tool access to that project's data volume.

```sh
docker compose -p traffic-restore run --rm -d --no-deps --name traffic-db-restore dashboard sleep 600
docker cp ./traffic-backup.db traffic-db-restore:/tmp/traffic-backup.db
docker exec traffic-db-restore python -m data.backup ../tmp/traffic-backup.db ../var/lib/traffic/traffic.db
docker stop traffic-db-restore
```

To view the restored counts, run the dashboard on a separate port and open
[the restored dashboard](http://127.0.0.1:8011/dashboard).

```sh
docker compose -p traffic-restore run --rm --no-deps -p 127.0.0.1:8011:8001 dashboard
```

## Forecasts

The dashboard refreshes every minute and shows completed minute buckets. The CV
runner uses Prophet by default to forecast the next 30 minutes every five minutes
when enough recent data is available. Set `TRAFFIC_FORECAST_MODEL=ridge_ar` to
use ridge regression for live forecasts. In the dashboard, select a date and
**Replay model** to compare a model with the recent-average baseline.

## Local development

In a Python 3.10+ environment, install the project and run the tests.

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
```

Run the CV runner and dashboard in separate terminals using the same test database.
Use a local MP4, or substitute a stream URL for `sample.mp4`.

```sh
python -m cv.run --url sample.mp4 --db data/smoke.db
python -m dashboard.app --db data/smoke.db
```

Add `--show` to the CV runner for an OpenCV preview. Install Deno for local
YouTube extraction. See [AGENTS.md](AGENTS.md) for integration details.
