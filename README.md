# MechDog Patrol Monitor

A web dashboard for the MechDog patrol and apprehension (순찰 및 검거) scenario in `../patrol_apprehension_plan.md`. It shows live LiDAR scans, the positions and status of the three dogs, sound alerts with direction, Dog A's camera feed, the mission state, an event timeline and evidence snapshots.

The project guide (how to carry out the project, with diagrams and screenshots) is in **[docs/project_guide.html](docs/project_guide.html)**.

## Quick start (simulated robots)

```bash
pip install -r requirements.txt

# terminal 1: monitor server (UDP :9000 in, web + WebSocket on :8000)
uvicorn server.app:app --host 0.0.0.0 --port 8000

# terminal 2: fake telemetry that plays the 3-minute demo scenario on a loop
python -m server.simulator            # add --speed 3 to run 3x faster
```

Open http://localhost:8000.

## Layout

```
mechdog-patrol-monitor/
├── server/
│   ├── app.py          # telemetry hub: UDP/HTTP in -> WebSocket out
│   └── simulator.py    # fake Pi + dogs, same messages as the real system
├── web/
│   ├── index.html      # dashboard
│   ├── app.js
│   ├── style.css
│   └── arena.json      # arena size, exits, obstacles, zones, LiDAR position
├── docs/
│   ├── project_guide.html
│   ├── message_schema.md
│   └── images/
└── requirements.txt
```

## Connecting the real robots

Point the Pi and the dogs at `udp://<monitor-ip>:9000` and send the messages in [docs/message_schema.md](docs/message_schema.md). Stop the simulator. Nothing in the dashboard needs to change. Edit `web/arena.json` to match your measured arena.

## First test with a real MechDog

1. Start the server only (no simulator). Run `python tools/send_test.py` and check that a moving Dog B appears at http://localhost:8000. Stop it.
2. Open `firmware/dog_status.py` in Thonny, set `WIFI_SSID`, `WIFI_PASS`, `MONITOR` (this computer's IP) and `NAME`, and run it on the dog over USB.
3. The dog's card should go live, showing Wi-Fi strength, and the timeline should show "Dog B online". Then fill in the sensor hooks one at a time.
4. The dog has no position until the Pi + LiDAR are running. To see its marker meanwhile: `python tools/send_test.py --dog B --x 1.2 --y 0.8`.
