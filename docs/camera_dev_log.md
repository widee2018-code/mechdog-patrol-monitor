# Camera firmware & live video: dev log

Last updated: 2026-11-10. Work is on branch `dev`, **not committed yet** (see "Uncommitted changes").

## Goal

The stock Hiwonder camera firmware only runs as its own Wi-Fi AP (192.168.5.1), so the computer had to leave
the normal network to see video. We replaced it with our own firmware so the camera joins the existing Wi-Fi,
then added live video to the dashboard.

## Hardware facts (confirmed)

| Item | Value |
|---|---|
| Camera module | ESP32-S3, separate from the MechDog main board |
| Image sensor | **GC2145** (2 MP, **no hardware JPEG**: RGB565 only) |
| Pin layout | **ESP32-S3-EYE** map (first entry in `PIN_MAPS`) |
| PSRAM | Yes, **OPI PSRAM** works |
| Flash | 8 MB chip, DIO mode |
| USB | Through a USB-to-serial chip: port `/dev/cu.usbserial-1120` |
| Wi-Fi MAC | `8c:bf:ea:87:5b:80` |
| Current IP | **192.168.0.20** (DHCP, not reserved yet), mDNS `mechdog-cam-a.local` |
| Signal | RSSI about −46 dBm |
| Mac (monitor) | 192.168.0.11, router 192.168.0.1 |

Status page when it worked:
`{"mode":"sta","ip":"192.168.0.20","rssi":-46,"camera":"ESP32-S3-EYE","sensor":"GC2145","hw_jpeg":false,"psram":true,...}`

## Arduino IDE settings that work

- Board: **ESP32S3 Dev Module** (esp32 core 3.3.12 by Espressif)
- PSRAM: **OPI PSRAM**
- **USB CDC On Boot: Disabled**. The module uses a USB-serial chip. With it Enabled, the sketch's `Serial`
  output is invisible and only ESP-IDF log lines show.
- Serial Monitor: **115200** baud (garbled text = wrong baud)
- Partition: Default 4MB with spiffs (sketch is about 78% of 1.3 MB). Edge Impulse later needs "Huge APP" or an 8M scheme.
- If the port disappears during upload, use BOOT + RESET. The board may have no buttons; replugging USB = reset.
  The auto-reset through the USB-serial chip has worked so far.

## Firmware: `firmware/mechdog_cam/`

- `mechdog_cam.ino`: joins Wi-Fi (STA). Falls back to AP `MechDog-Cam-A` / `mechdog123` (192.168.4.1) if it can't join.
  - Endpoints: `/` (preview), `/capture` (JPEG), `:81/stream` (MJPEG), `/status` (JSON).
  - Tries JPEG first. On `ESP_ERR_NOT_SUPPORTED` it re-inits as RGB565 at QVGA 320×240 and encodes JPEG in
    software (`frame2jpg`, quality 80).
  - The `Jpeg` struct must stay above all functions, because Arduino auto-generates prototypes at the top.
  - `E (933) camera: JPEG format is not supported on this sensor` at every boot is **expected**: it's the first
    JPEG attempt failing before the RGB565 retry.
- `secrets.h` (git-ignored) holds `WIFI_SSID` / `WIFI_PASS`; `secrets.h.example` is the template.
- The ESP32 `:81` server can serve **only one stream client at a time**.

## Original firmware backup

- `firmware/backup/hiwonder_camera_8MB.bin` (git-ignored, **only copy is on this Mac**: copy it elsewhere)
- SHA-256 `1210a6c05da2f79090d9a3ad551fb1e67eb35580aebe7ccdc4aeaa500fce3578`
- Layout: bootloader 0x0, partitions 0x8000, `factory` app 0x10000 (3.75 MB, 1.2 MB used), `nvs` 0x3d0000, `fr` 0x3e0000; top 4 MB unused
- Stock firmware is ESP-IDF based and serves `/capture` and `/stream` on 192.168.5.1. It contains STA-mode strings
  (`sta.ssid`, `connect to ap SSID:%s`), so it may support joining Wi-Fi somehow (unknown how).
- Restore:
  ```bash
  python3 -m esptool --chip esp32s3 -p /dev/cu.usbserial-1120 -b 460800 write-flash 0 firmware/backup/hiwonder_camera_8MB.bin
  ```
- Also present: `firmware/backup/main_board_backup/` (main board).

## Dashboard live video

- `server/app.py`: `CameraFeed` keeps **one** upstream MJPEG connection per camera in a background thread
  (urllib, `read1`) and fans frames out to all viewers.
  - `GET /api/camera/{name}/stream`: MJPEG to the browser
  - `GET /api/cameras`: `{name: {online, url, error}}`
  - Timeline events when the camera connects or fails (each distinct error once); every failure is printed to the console.
  - Configured by env var `CAMERAS="A=http://192.168.0.20:81/stream"`.
- `web/index.html` + `web/app.js`: `#cam-live` img overlays the snapshot `#cam`; `pollLive()` checks `/api/cameras`
  every 3 s and shows "실시간" when online, falling back to `camera` message snapshots otherwise.
- Run with `--timeout-graceful-shutdown 2`, otherwise Ctrl+C hangs while a dashboard has the stream open.
- Tested with a fake local MJPEG camera: 2 viewers × 10 fps from one upstream, connect/fail events, 404 for an
  unknown camera, shutdown in about 2 s. **Not yet seen working against the real camera.**

Run:
```bash
cd "/Users/daeheekim/Documents/Daehee/청년 피지컬AI 일경험 프로젝트/Team_Project/mechdog-patrol-monitor"
conda activate physical_ai
CAMERAS="A=http://192.168.0.20:81/stream" uvicorn server.app:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 2
```

## Current blocker (as of 2026-11-10)

Server prints `[Errno 65] No route to host` for 192.168.0.20, while the **browser** can open `:81/stream` fine.
Cause: the **macOS Local Network privacy permission**. The server was run from **VS Code's integrated terminal**,
and VS Code had been running since before the permission was toggled.

Next things to try, in order:
1. System Settings → Privacy & Security → Local Network → **Visual Studio Code** on, then **⌘Q VS Code** and reopen.
2. Or run the server from **Terminal.app** and click **Allow** on the prompt.
3. Reboot the Mac if it still fails (macOS 27 sometimes needs it).
4. Check from the same terminal: `curl -m 5 http://192.168.0.20/status`. JSON = fixed.
5. Close any browser tab on `:81/stream` before starting the server (one stream client only). If it's left open,
   the server error becomes `timed out` instead.

Offered but not built: a dashboard fallback that loads `http://192.168.0.20:81/stream` directly in the browser
when the server can't reach the camera (one tab only).

Claude Code's own shell also can't reach the LAN (same permission). Localhost (`127.0.0.1:8000`) works, so
`/api/cameras` and `/api/state` can be queried from there to see the server's view.

## Uncommitted changes on `dev`

- `firmware/mechdog_cam/` (new), `.gitignore` (ignore `secrets.h`)
- `server/app.py`, `web/app.js`, `web/index.html` (live video)
- `README.md` (camera firmware + live video sections)
- this file

## Ideas / later

- Reserve 192.168.0.20 for MAC `8c:bf:ea:87:5b:80` in the router, or set `USE_STATIC_IP` in the sketch.
- Raise resolution to VGA 640×480 (GC2145 supports up to UXGA), at about 3–5 fps.
- Simplify the firmware: drop the XIAO pin map and try RGB565 first.
- README: note GC2145, the USB-serial chip, and USB CDC On Boot = Disabled.
- Edge Impulse: export an Arduino library (int8, EON), FOMO 96×96, run in the camera firmware (RGB565 frames make
  this easy), send detections as UDP messages. Needs a bigger app partition, and may need an older esp32 core.
