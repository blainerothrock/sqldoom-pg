# SQL DOOM PostgreSQL Comparision

This repository take the [SQLDoom](https://github.com/cedardb/sqldoom) ([Blog Post](https://cedardb.com/blog/sqldoom/)) implementation from CedarDB and makes some comparision against vanilla PostgreSQL. The goal is to show the performance differences in different architechures and provide a basic example of OLAP versus OLTP for a University lecture.

This work has no affiliation with CedarDB or author of the SQLDoom work. Nor is this to serve as any real-world comparision of the two databases. 

## AI-Disclosure
Claude Opus 5.5 was used to generate the code used in this repository along with formulating the comparision SQL. This README.md was written by a human (`blainerothrock`).

## Getting Started

1. Install and sync Python environment (using [UV](https://docs.astral.sh/uv/getting-started/installation/))
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

2. Copy and populate .env
```bash
cp .env-template .env
```

Populate password (`Sqldoom-Local-2026!`, for example) and update environment as needed. Note update password in `CEDAR_PASSWORD`, `DB_DSN`, `CPG_DSN`, and `CPG_PASSWORD`. Feel free to use a different one for each database (but not needed for local development).

load the variables:
```bash
source .env
```

3. Get Doom WAD file (shareware)

[WAD](https://doomwiki.org/wiki/DOOM.WAD) is a file format for Doom assets and map layouts (everything but game logic). Doom is a licensed game, therefore the WAD files that are used to populate the database require a game copy. We can copy the files from a purchased game, for example on [Steam](https://store.steampowered.com/app/2280/DOOM_DOOM_II/), or use the shareware (esentially a demo of the first level). Here we will use the shareware.

```bash
curl -LO https://deb.debian.org/debian/pool/non-free/d/doom-wad-shareware/doom-wad-shareware_1.9.fixed.orig.tar.gz
tar xzf doom-wad-shareware_1.9.fixed.orig.tar.gz
rm tar xzf doom-wad-shareware_1.9.fixed.orig.tar.gz
```

4. Start the Docker Containers
```bash
docker-compose up -d
```

Wait a few seconds for everything to start. Check the health with `docker-compose ps`.
```txt
CONTAINER ID   IMAGE                    COMMAND                  CREATED          STATUS                    PORTS                      NAMES
7dfacf8cb645   postgres:18              "docker-entrypoint.s…"   30 seconds ago   Up 29 seconds (healthy)   127.0.0.1:5433->5432/tcp   sqldoom-pg
8bbb4f2bf71e   cedardb/cedardb:latest   "/usr/local/bin/dock…"   30 seconds ago   Up 29 seconds (healthy)   127.0.0.1:5432->5432/tcp   sqldoom-db
```

5. load data into Cedar
```bash
uv run python sqldoom/wad_loader.py doom-wad-shareware-1.9.fixed/doom1.wad --dsn "$DB_DSN"
```

the Doom client from the sqldoom project can now be started (optional)
```bash
uv run sqldoom/doom_client.py
```

6. Load PosgreSQL data
```bash
uv run postgres/clone_to_postgres.py
```

## Comparisions

Here are a few comparisions of the PostgreSQL and CedarDB.

### Rendering
**NOTE**: we only look at the rendering (`sqldoom/sql/renderer.sql`) as the runtime is writing in Cedar procederal langauge.
**NOTE**: `--jit` refers to PostfreSQL [just-in-time compliation](https://www.postgresql.org/docs/current/jit.html), which actually makes the queries slower. [CedarDB's compliation is different](https://cedardb.com/blog/compilation/) and is the key to performance of the large renderer query.
**NOTE**: See `postgres/port_renderer.py` for the slight alterations made to port the query from CedarDB to PostgreSQL.

#### Single Frame
```bash
uv run postgres/bench_frame.py --runs 10 --jit off --workers 2 --save frames
```

### Multiple Frames
```bash
uv run postgres/flythrough.py --frames 16 --jit off --workers 4
```

### Explain
```bash
uv run postgres/explain_renderer.py --jit off
```

### OLAP
