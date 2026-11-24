-------------------
--- Player info ---
-------------------
SELECT health, armor, ammo_bullets AS bullets, ammo_shells AS shells,
       round(position_x) AS x, round(position_y) AS y, round(view_angle) AS angle,
       sector_id, level_tics, level_tics / 35 AS seconds
FROM player_state
WHERE map_id = 1 AND player_thing_id = 0;


---------------------------------------
--- Sector (room) of current player ---
---------------------------------------
SELECT s.id, s.floor_tex, s.ceil_tex, s.floor_height, s.ceil_height,
       s.light_level, s.special
FROM player_state p
JOIN sectors s ON s.map_id = p.map_id AND s.id = p.sector_id
WHERE p.map_id = 1 AND p.player_thing_id = 0;

----------------------
--- level progress ---
----------------------
SELECT kills, total_kills, items, total_items, secrets, total_secrets,
       par_tics / 35 AS par_seconds
FROM level_stats
WHERE map_id = 1;

----------------
--- Monsters ---
----------------
SELECT c.name,
       count(*) FILTER (WHERE h.alive)     AS alive,
       count(*) FILTER (WHERE NOT h.alive) AS dead,
       round(min(sqrt((t.x - p.position_x)^2 + (t.y - p.position_y)^2))
             FILTER (WHERE h.alive))       AS nearest
FROM thing_health h
JOIN things t            ON t.map_id = h.map_id AND t.id = h.thing_id
JOIN thing_combat_defs c ON c.thing_type = t.type
JOIN player_state p      ON p.map_id = h.map_id AND p.player_thing_id = 0
WHERE h.map_id = 1 AND c.counts_kill
GROUP BY c.name
ORDER BY nearest NULLS LAST;


--------------------------------
--- sector change since load ---
--------------------------------
SELECT s.id,
       s.floor_tex,   s.spawn_floor_tex,
       s.ceil_tex,    o.ceil_tex AS loaded_ceil_tex,
       s.light_level, s.spawn_light_level
FROM sectors s
JOIN (SELECT DISTINCT fsec AS id, f_ceil_tex AS ceil_tex
      FROM render_segs WHERE map_id = 1) o ON o.id = s.id
WHERE s.map_id = 1
  AND (s.floor_tex <> s.spawn_floor_tex OR s.ceil_tex <> o.ceil_tex
       OR s.floor_height <> s.spawn_floor_height
       OR s.ceil_height <> s.spawn_ceil_height
       OR s.light_level <> s.spawn_light_level)
ORDER BY s.id;
