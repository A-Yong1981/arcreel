# ArcReel Deployment Bundle

Compose bundle for the ArcReel deployment on the NAS, including the Muse uploader and the companion bridge service. The ArcReel application itself is pulled as the upstream `arcreel/arcreel` container image; upstream application source is not duplicated here.

## Services

- `arcreel`: upstream ArcReel image.
- `muse-uploader`: Streamable HTTP MCP connector and shot upload API.
- `arcreel-bridge`: local companion API and portal.

## Setup

1. Copy `.env.example` to `.env` and set the ArcReel password.
2. Copy `arcreel-muse-uploader/env.example` to `arcreel-muse-uploader/.env`; configure the existing bridge token and ArcReel internal API credential there.
3. Copy `arcreel-bridge/env.example` to `arcreel-bridge/.env` and set the bridge encryption key.
4. Run `docker compose -f compose.yml up -d --build`.

Never commit `.env` files, project data, generated media, SQLite files, or logs. The bridge installs dependencies from the configured Python package mirror during image build.

See [MUSE_BOT_SETUP.md](MUSE_BOT_SETUP.md) for Muse connection and operating instructions.
