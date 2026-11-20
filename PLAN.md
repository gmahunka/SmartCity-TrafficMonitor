# Traffic monitoring roadmap

Build a short-term traffic forecast first, then run the monitor continuously on a Linux PC, then bring multiple locations together on a world map. The existing project uses YOLO and ByteTrack to count vehicles, SQLite to store events and minute totals, and FastAPI to serve the dashboard and embed the annotated stream.

The local database inspected during planning contained 73 stored minute buckets and 2,378 vehicle events from one camera, `cam1`. That session can support a forecast prototype and historical replay. Daily and weekly forecasts need more operating history.

## Phase 1 — Short-term forecast

- [x] Forecast the total count for the next 30 minutes in six 5-minute buckets. Use a stable camera ID throughout, starting with `cam1`.
- [x] Fit Prophet to completed counts and retain ridge regression for comparison. Save each issued forecast and keep its overnight points visible on the next day's chart.
- [x] Add historical replay and show when there is insufficient recent data to issue a new forecast.
- [x] Backtest in chronological order and compare mean absolute error with the recent-average baseline on the same eligible targets.
- [ ] Record capture coverage so a known online minute with zero counts can be distinguished from missing data.

**Done when** the dashboard can replay the recorded session, display the 30-minute forecast, and report its backtest error. Once continuous collection provides enough history, compare the baseline with a forecast based on the camera's local time of day and day of week. [Forecasting methods and time-series evaluation](https://otexts.com/fpp3/toolbox.html)

## Phase 2 — Continuous operation on Linux

- [ ] Package the CV runner and dashboard as two Docker Compose services. Put the selected stream URL and stable camera ID in configuration so collection starts when the services start.
- [ ] Start with one stream on the CPU and measure whether the host keeps up. Run the CV service without the optional OpenCV preview window.
- [ ] Copy the existing SQLite data into persistent storage shared by both services. Document backup and restore, configure automatic restart, and make the dashboard and its embedded stream reachable on the local network.
- [ ] Verify a fresh installation using the Linux instructions. Reboot the PC, interrupt and restore the stream, and check that collection resumes and earlier data is still present. Restore a backup to a separate database and verify that it opens.

**Done when** the PC starts collecting from the configured feed after boot, recovers from a temporary stream interruption, and serves the dashboard on the local network with the existing history intact. [Docker Compose restart policies](https://docs.docker.com/reference/compose-file/services/#restart) · [Persistent storage](https://docs.docker.com/reference/compose-file/volumes/)

## Phase 3 — World map and multiple cameras

- [ ] Configure each camera with a stable ID, display name, stream URL, latitude, longitude, and time zone.
- [ ] Add minute totals keyed by camera ID and minute start before enabling concurrent streams. Raw events already carry a camera ID and can be used to rebuild per-camera history. Preserve any legacy totals whose source camera cannot be verified.
- [ ] Run two configured streams on the Linux host and serve camera-specific status, totals, forecasts, and annotated streams.
- [ ] Add an interactive map with one pin per configured location. Each pin shows camera status, the latest completed count, and the next 30-minute forecast when available. Clicking a pin opens that camera's chart and stream.
- [ ] Verify that the two feeds have independent counts and forecasts. Disconnect one feed and confirm that its status changes while the other feed continues collecting.

**Done when** two configured locations can be monitored independently from the map.
