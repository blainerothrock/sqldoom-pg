-----------------------------------------
--- Turn every floor into green slime ---
-----------------------------------------
UPDATE sectors SET floor_tex = 'NUKAGE1' WHERE map_id = 1;

-- undo
UPDATE sectors SET floor_tex = spawn_floor_tex WHERE map_id = 1;

-----------------------
--- remove the roof ---
-----------------------
UPDATE sectors SET ceil_tex = 'F_SKY1' WHERE map_id = 1;

-- undo
UPDATE sectors s SET ceil_tex = o.tex
FROM (SELECT DISTINCT fsec AS id, f_ceil_tex AS tex
      FROM render_segs WHERE map_id = 1) o
WHERE s.map_id = 1 AND s.id = o.id;

------------------
--- red filter ---
------------------
UPDATE colormap_rgb
SET rgb = decode(lpad(to_hex(r::int), 2, '0') || '0000', 'hex')
WHERE pal = 0;

-- undo
UPDATE colormap_rgb
SET rgb = decode(lpad(to_hex(r::int), 2, '0') || lpad(to_hex(g::int), 2, '0') || lpad(to_hex(b::int), 2, '0'), 'hex');

-----------------------
--- Invulnerability ---
-----------------------
UPDATE player_state SET invuln_tics = 350 WHERE map_id = 1;

-- undo
UPDATE player_state SET invuln_tics = 0 WHERE map_id = 1;

------------------
--- update HUD ---
------------------
CREATE TABLE hud_backup AS
  SELECT map_id, player_thing_id, health, armor, ammo_bullets FROM player_state;

UPDATE player_state SET health = 7, armor = 200, ammo_bullets = 999 WHERE map_id = 1;

-- undo
UPDATE player_state p
SET health = b.health, armor = b.armor, ammo_bullets = b.ammo_bullets
FROM hud_backup b
WHERE p.map_id = b.map_id AND p.player_thing_id = b.player_thing_id;

DROP TABLE hud_backup;
