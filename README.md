# EchelonHD Scanner

Scan your Plex media library and automatically upload torrents to EchelonHD. Everything runs behind a VPN for safety.

## Features

- **Library Scanner** — scans Movies, TV Shows, and Anime directories
- **Auto-Detection** — parses title, year, resolution from file/folder names
- **TMDB Integration** — fetches metadata for every item
- **Torrent Creation** — creates .torrent files with correct announce URL
- **qBittorrent Integration** — adds torrents for seeding with skip_checking
- **VPN Protection** — all traffic routed through Gluetun (IPVanish, Mullvad, etc.)
- **Web Dashboard** — beautiful dark UI to monitor scans, uploads, and stats
- **Docker** — one command deployment via Docker Compose

## Quick Start

```bash
git clone https://github.com/YOUR_USER/echelon-scanner.git
cd echelon-scanner
cp .env.example .env
# Edit .env with your settings
docker compose up -d
```

Access the dashboard at `http://your-server:9090`

## Architecture

```
┌── Gluetun (VPN) ──────────────────────────┐
│                                            │
│  qBittorrent ←──── Scanner App            │
│  (seeds torrents)   (scans + creates)      │
│                                            │
└────────────────────────────────────────────┘
        ↕                    ↕
   BitTorrent            EchelonHD API
   (seeding)             (registration)
```

## Configuration

See `.env.example` for all available options.

## Dashboard

The web dashboard shows:
- Library stats (Movies, TV, Anime counts)
- Scan progress with live updates
- Upload status per item
- qBit connection status
- Error logs

## License

MIT
