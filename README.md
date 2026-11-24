# SQL DOOM PostgreSQL Comparision

This repository takes the [SQLDoom](https://github.com/cedardb/sqldoom) ([Blog Post](https://cedardb.com/blog/sqldoom/)) implementation from CedarDB and makes some comparision against vanilla PostgreSQL. The goal is to show the performance differences in different database system architechures and provide a basic example of OLAP versus OLTP for a University lecture.

This work has no affiliation with CedarDB or author of the SQLDoom work. Nor is this project to serve as any real-world comparision of the two databases. For example, PostgreSQL has a rich extension library which may help the performance in some comparision listed below. 

This has been tested on macOS (M2 Pro with 16GB unified memory), using [colima](https://colima.run/) as the Docker backend. SQLDoom runs at slightly below 60FPS (via client UI visual inspection while playing).

## Getting Started

### 1. Clone repo and populate submodule

This repository links sqldoom as a submobule and does not alter the origional code. 
```bash
git clone git@github.com:blainerothrock/sqldoom-pg.git
git submodule update --init --recursive
```

### 2. Install and sync Python environment (using [UV](https://docs.astral.sh/uv/getting-started/installation/))
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

### 3. Copy and populate .env
```bash
cp .env-template .env
```

Populate password (`Sqldoom-Local-2026!`, for example) and update environment as needed. Note update password in `CEDAR_PASSWORD`, `DB_DSN`, `PG_DSN`, and `PG_PASSWORD`. Feel free to use a different one for each database (but not needed for local development).

load the variables:
```bash
source .env
```

### 4. Get Doom WAD file (shareware)

[WAD](https://doomwiki.org/wiki/DOOM.WAD) is a file format for Doom assets and map layouts (everything but game logic). Doom is a licensed game, therefore the WAD files that are used to populate the database require a game copy. We can copy the files from a purchased game, for example on [Steam](https://store.steampowered.com/app/2280/DOOM_DOOM_II/), or use the shareware (esentially a demo of the first level). Here we will use the shareware.

```bash
curl -LO https://deb.debian.org/debian/pool/non-free/d/doom-wad-shareware/doom-wad-shareware_1.9.fixed.orig.tar.gz
tar xzf doom-wad-shareware_1.9.fixed.orig.tar.gz
rm tar xzf doom-wad-shareware_1.9.fixed.orig.tar.gz
```

### 5. Start the Docker Containers
```bash
docker-compose up -d
```

Wait a few seconds for everything to start. Check the health with `docker-compose ps`.
```txt
CONTAINER ID   IMAGE                    COMMAND                  CREATED          STATUS                    PORTS                      NAMES
7dfacf8cb645   postgres:18              "docker-entrypoint.s…"   30 seconds ago   Up 29 seconds (healthy)   127.0.0.1:5433->5432/tcp   sqldoom-pg
8bbb4f2bf71e   cedardb/cedardb:latest   "/usr/local/bin/dock…"   30 seconds ago   Up 29 seconds (healthy)   127.0.0.1:5432->5432/tcp   sqldoom-db
```

The containers are lightly configured version of what is hosted by CedarDB and PosgreSQL respectively. CedarDB configures the memory size and parallel workers (adjust to see varied results). PostgreSQL adjusts the shared memory to 1G from the default 64MB in Docker.

### 6. load data into CedarDB
This script parses the WAD file into a relational model. See the [origional blog post](https://cedardb.com/blog/sqldoom/) for details.
```bash
uv run python sqldoom/wad_loader.py doom-wad-shareware-1.9.fixed/doom1.wad --dsn "$DB_DSN"
```

the Doom client from the sqldoom project can now be started (optional)
```bash
uv run sqldoom/doom_client.py
```

At this point, you should be able to use any of the scripts in `sqldoom/` against our setup. Not all have been tested here.

### 7. Load PosgreSQL data

The schema and data is copied from CedarDB into the newly created PostgreSQL database instance.
```bash
uv run postgres/clone_to_postgres.py
```

## Comparisions

Here are a few comparisions of the PostgreSQL and CedarDB.

### Rendering

The following are some tests comparing SQLDOOM's renderer query (`sqldoom/sql/renderer.sql`) between CedarDB and PostgreSQL. This is a very large query with 89 common table expressions (CTE, the `WITH` keyword) which is ran at every game tic to generate the frame from the WAD relational translation.

#### NOTES
* These tests only explore the rendering (`sqldoom/sql/renderer.sql`) as the runtime is writing in CedarDB's procederal langauge.
* `--jit` refers to PostfreSQL [just-in-time compliation](https://www.postgresql.org/docs/current/jit.html). For this use-case JIT makes the queries slower. [CedarDB's compliation is different](https://cedardb.com/blog/compilation/) and is the key to performance of the large renderer query.
* `postgres/port_renderer.py` contains slight modification to the renderering script from CedarDB for compatability.
* We only use vanilla PostgreSQL (no extensions).

### Render Single Frame
Outputs a single frame from either PG or CedarDB based on the current DB state. Examples:
```bash
uv run postgres/render_one.py --engine cedar --ascii
uv run postgres/render_one.py --engine pg --png pg.png
uv run postgres/render_one.py --engine pg --sql render-frame.sql
```

#### Single Frame Comparision
Runs a single frame from the current CedarDB database state (runs pg clone before rendering), and measures the time. 
```bash
uv run postgres/bench_frame.py --runs 10 --jit off
```

#### Multiple Frames
Similar to `bench_frame.py`, but create a 360 degree rotation of the player, comparing rendering times. Produces a HTML file to compare the two databases.
```bash
uv run postgres/flythrough.py --frames 34 --jit off --workers 4
```

#### Explain
Computes planning diagrams of the query in both CedarDB and PostgreSQL. This uses the same diagram generation in the SQLDOOM project, but includes PostgreSQL.
```bash
uv run postgres/explain_renderer.py --jit off
```

### OLAP

The slowdowns in PostgreSQL above are not related to OLAP storage. They are, seeminly, due to the query compliation techniques of CedarDB. The following tests generates a new facts table of player data at each game tick to demonstrate performance of OLAP storage. 

#### NOTES:
* CedarDB uses [a hybrid engine, Colibri](https://cedardb.com/blog/colibri/) ([paper](https://db.in.tum.de/~schmidt/papers/colibri.pdf)) which mixes OLAP and OTLP, therefore this may not be a great test of generic OLAP v. OTLP querying, but still a interesting comparision.

#### Load the randomized data
We create a new schema and table in both CedarDB and PostgreSQL, `olap` and create a single facts table which references tables from the origional WAD-generated schema. This creates a small start 

```bash
uv run postgres/olap.py load --rows 10000000 --engine both
```

This create the `olap` schema in both database systems, and a new table `tic_log` with 10,000,000 rows. Rows are generated, seeded by the row number, therefore both tables are the same in each system. See `postgres/olap/tic_log.sql`. 10,000,000 rows is about 1.4GB (plus 422MB index) on PostgreSQL.

The olap schema can be dropped with

```bash
uv run postgres/olap.py drop --engines both
```

#### Test Queries

There are 5 test queries in `postgres/olap/queries.sql`, each testing a different OLAP (Q1-4) or OTLP feature (Q5-6). 
* **Q1** (OLAP): Aggregation across the entire `tik_log` for two columns (narrow scan).
* **Q2** (OLAP): Aggregation with fact table join to a deminsion in the WAD schema (star)
* **Q3** (OLAP): Distance traveled per player using `WINDOW`
* **Q4** (OLTP): Single record lookup by primary key
* **Q5** (OLTP): 1,000 single record lookups by primary key

Run with the following to test a individual query:

```bash
uv run postgres/olap.py bench --runs 10 --jit off --workers 4 --only Q1
```

## Runtime Queries

`runtime-select.sql` and `runtime-updates.sql` contain queries that can be ran while playing the game (`uv run sqldoom/doom_client.py`). 


## AI Disclosure
Claude Opus 5.5 was used to write the code used in this repository. This includes the translation of the schema to PostgreSQL, formulating the comparision examples, generation of HTML reports, and assistance in streamlining project setup in Docker. This README.md was written by a human (@blainerothrock).
