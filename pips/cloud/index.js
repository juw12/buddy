// Pips Friends API - Cloudflare Worker + D1.
//
// Auth: every device registers once and gets {id, secret}. Later calls send
// "Authorization: Bearer <id>.<secret>". Only a SHA-256 of the secret is stored.
// Shared data is limited to: display name, animal + colour, level / XP, streak,
// today's / this week's pomodoros + focus minutes, achievements, mood.
// Never: app names, window titles, detailed scores.
// v12: the app version each device runs and an optional work e-mail (filled in from Outlook,
// editable in the Profile tab) - both visible to the owner and moderators only, for update reminders.
// v13: Pips Community - #announcements (staff + automatic release posts) and #general for everyone.

const VERSION = "13";
const OWNER_LEVEL = 99;
const OWNER_XP = 999999;
const CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // no 0/O/1/I
const MAX_FRIENDS = 100;
const MAX_GROUPS = 5;
// v6: roles + premium
const PREMIUM_FOREVER = 4102444800; // 2100-01-01 = "forever"
const MAX_FRIENDS_PREMIUM = 300;
const MAX_CIRCLES_OWNED_PREMIUM = 30;
const STAFF_ROLES = new Set(["owner", "mod"]);
const isStaff = (u) => !!u && STAFF_ROLES.has(u.role || "");
const isPremium = (u) => isStaff(u) || (u && (u.premium_until || 0) > Math.floor(Date.now() / 1000));
const friendLimit = (u) => (isPremium(u) ? MAX_FRIENDS_PREMIUM : MAX_FRIENDS);
const isMuted = (u) => (u.muted_until || 0) > Math.floor(Date.now() / 1000);
// v12: app versions + update reminders
const MANIFEST_URL = "https://juw12.github.io/projects/pips/download/latest.json";
const EXACT_VERSION = /^\d{1,3}\.\d{1,3}\.\d{1,3}$/;
const ACTIVE_DAYS = 14;              // people not seen for longer count as inactive
const REMIND_VIA = new Set(["email", "teams"]);
const EMAIL_RE = /^[^\s@<>"',;:()\[\]]+@[^\s@<>"',;:()\[\]]+\.[A-Za-z]{2,}$/;
let latestCache = { version: "", at: 0 };

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });

const fail = (status, error) => json({ ok: false, error }, status);

async function sha256(text) {
  const data = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function randomCode(length = 6) {
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  return [...bytes].map((b) => CODE_ALPHABET[b % CODE_ALPHABET.length]).join("");
}

function randomSecret() {
  const bytes = crypto.getRandomValues(new Uint8Array(24));
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

const now = () => Math.floor(Date.now() / 1000);

function cleanText(value, max, fallback = "") {
  if (typeof value !== "string") return fallback;
  const text = value.replace(/[\u0000-\u001f\u007f]/g, "").trim().slice(0, max);
  return text || fallback;
}

function cleanInt(value, min, max, fallback = 0) {
  const n = Number.parseInt(value, 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}

function cleanColor(value, fallback) {
  return typeof value === "string" && /^#[0-9a-fA-F]{6}$/.test(value) ? value.toLowerCase() : fallback;
}

function cleanGroup(value) {
  if (typeof value !== "string") return "";
  return value.toUpperCase().replace(/[^A-Z0-9_-]/g, "").slice(0, 16);
}

const PROP_SLOTS = ["head", "face", "neck", "back"];

function cleanLook(value, fallback) {
  if (value === undefined) return fallback;
  if (!value || typeof value !== "object" || Array.isArray(value)) return "{}";
  const out = {};
  for (const slot of PROP_SLOTS) {
    const item = value[slot];
    if (typeof item === "string" && /^[a-z_]{1,24}$/.test(item)) out[slot] = item;
  }
  if (Array.isArray(value.moves)) {
    out.moves = value.moves.filter((m) => typeof m === "string" && /^[a-z_]{1,24}$/.test(m)).slice(0, 12);
  }
  return JSON.stringify(out);
}

function parseLook(text) {
  try {
    const look = JSON.parse(text || "{}");
    return look && typeof look === "object" ? look : {};
  } catch {
    return {};
  }
}

function cleanAchievements(value) {
  if (!Array.isArray(value)) return "[]";
  const ids = value
    .filter((x) => typeof x === "string")
    .map((x) => x.replace(/[^a-z0-9_]/g, "").slice(0, 32))
    .filter(Boolean)
    .slice(0, 100);
  return JSON.stringify([...new Set(ids)]);
}

async function readJson(request, limit = 16384) {
  try {
    const text = await request.text();
    if (text.length > limit) return null;
    return text ? JSON.parse(text) : {};
  } catch {
    return null;
  }
}

async function authenticate(request, env) {
  const header = request.headers.get("authorization") || "";
  const match = header.match(/^Bearer ([0-9a-f-]{36})\.([0-9a-f]{48})$/i);
  if (!match) return null;
  const user = await env.DB.prepare("SELECT * FROM users WHERE id = ?").bind(match[1]).first();
  if (!user) return null;
  if (user.secret_hash !== (await sha256(match[2]))) return null;
  // presence heartbeat (at most one write a minute)
  const url = new URL(request.url);
  const idleParam = url.searchParams.get("idle");
  const idle = idleParam === null ? user.idle || 0 : idleParam === "1" ? 1 : 0;
  const t = now();
  const version = clientVersion(request);
  const newVersion = !!version && version !== (user.app_ver || "");
  if (t - (user.last_seen || 0) > 60 || idle !== (user.idle || 0) || newVersion) {
    if (newVersion) {
      await env.DB.prepare("UPDATE users SET last_seen = ?, idle = ?, app_ver = ? WHERE id = ?")
        .bind(t, idle, version, user.id)
        .run();
      user.app_ver = version;
    } else {
      await env.DB.prepare("UPDATE users SET last_seen = ?, idle = ? WHERE id = ?").bind(t, idle, user.id).run();
    }
    user.last_seen = t;
    user.idle = idle;
  }
  return user;
}

// ---------------------------------------------------------------- v12: app versions
// Pips >= 2.15 sends "x-pips-version: 2.15.0" (and "user-agent: Pips/2.15.0") on every call.
function clientVersion(request) {
  const header = (request.headers.get("x-pips-version") || "").trim();
  if (EXACT_VERSION.test(header)) return header;
  const agent = request.headers.get("user-agent") || "";
  const match = agent.match(/^Pips\/(\d{1,3}\.\d{1,3}\.\d{1,3})\b/);
  return match ? match[1] : "";
}

function versionParts(text) {
  return EXACT_VERSION.test(text || "") ? text.split(".").map((n) => Number.parseInt(n, 10)) : null;
}

// -1 / 0 / 1; anything that is not an exact x.y.z version sorts below every real version.
function compareVersions(a, b) {
  const pa = versionParts(a);
  const pb = versionParts(b);
  if (!pa || !pb) return pa ? 1 : pb ? -1 : 0;
  for (let i = 0; i < 3; i++) {
    if (pa[i] !== pb[i]) return pa[i] < pb[i] ? -1 : 1;
  }
  return 0;
}

// Older clients don't send their version: 2.14.x is recognised by the allow_nudges flag it syncs.
function inferredVersion(body) {
  return typeof body.allow_nudges === "boolean" ? "2.14" : "<2.14";
}

// "" clears, null = invalid / not sent.
function cleanEmail(value) {
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (!text) return "";
  return text.length <= 120 && EMAIL_RE.test(text) ? text : null;
}

async function latestVersion() {
  const t = now();
  if (latestCache.version && t - latestCache.at < 600) return latestCache.version;
  try {
    const resp = await fetch(MANIFEST_URL, { cf: { cacheTtl: 300, cacheEverything: true } });
    if (resp.ok) {
      const data = await resp.json();
      if (data && EXACT_VERSION.test(data.version || "")) latestCache = { version: data.version, at: t };
    }
  } catch (err) {
    console.error("manifest fetch failed", err);
  }
  return latestCache.version;
}

// ---------------------------------------------------------------- v4: presence, profiles, labels
const ONLINE_WINDOW_S = 150;
const STATUSES = new Set(["auto", "online", "busy", "away", "offline"]);
const LABELS = new Set(["", "buddy", "bro", "sis", "bestie", "partner", "soulmate", "family", "work"]);
const INFO_FIELDS = { job: 40, location: 40, hobbies: 80, birthday: 20, fav: 40 };

function presence(row) {
  const seen = now() - (row.last_seen || 0) < ONLINE_WINDOW_S;
  const status = row.status || "auto";
  if (!seen || status === "offline") return "offline";
  if (status === "auto") return row.idle ? "away" : "online";
  return status;
}

function cleanInfo(value, fallback) {
  if (value === undefined) return fallback;
  if (!value || typeof value !== "object" || Array.isArray(value)) return "{}";
  const out = {};
  for (const [key, max] of Object.entries(INFO_FIELDS)) {
    const text = cleanText(value[key], max, "");
    if (text) out[key] = text;
  }
  return JSON.stringify(out);
}

function parseObj(text) {
  try {
    const obj = JSON.parse(text || "{}");
    return obj && typeof obj === "object" && !Array.isArray(obj) ? obj : {};
  } catch {
    return {};
  }
}

// Public view of a user, with stale day/week numbers zeroed for the viewer's day/week.
function publicProfile(row, day, week) {
  const sameDay = !day || row.day === day;
  const sameWeek = !week || row.week === week;
  let achievements = [];
  try {
    achievements = JSON.parse(row.achievements || "[]");
  } catch {
    achievements = [];
  }
  return {
    code: row.code,
    name: row.name,
    species: row.species,
    color: row.color,
    level: row.level,
    xp: row.xp,
    streak: row.streak,
    today_poms: sameDay ? row.today_poms : 0,
    today_minutes: sameDay ? row.today_minutes : 0,
    week_poms: sameWeek ? row.week_poms : 0,
    week_minutes: sameWeek ? row.week_minutes : 0,
    achievements,
    mood: row.mood,
    look: parseLook(row.look),
    updated_at: row.updated_at,
    avatar_v: row.avatar_v || 0,
    presence: presence(row),
    status_text: row.status_text || "",
    visibility: row.visibility || "public",
    role: row.role || "",
    premium: isPremium(row),
    bday: bdayOf(row),
    nudges: !row.nudges_off,
  };
}

async function register(request, env) {
  const body = await readJson(request);
  if (body === null) return fail(400, "bad_json");
  const id = crypto.randomUUID();
  const secret = randomSecret();
  const name = cleanText(body.name, 24, "Pips friend");
  const species = cleanText(body.species, 16, "cat").toLowerCase().replace(/[^a-z_]/g, "") || "cat";
  const color = cleanColor(body.color, "#ffc78f");
  const t = now();
  for (let attempt = 0; attempt < 6; attempt++) {
    const code = randomCode();
    try {
      await env.DB.prepare(
        "INSERT INTO users (id, secret_hash, code, name, species, color, created_at, updated_at) " +
          "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
      )
        .bind(id, await sha256(secret), code, name, species, color, t, t)
        .run();
      return json({ ok: true, id, secret, code });
    } catch (err) {
      if (!String(err).includes("UNIQUE")) throw err;
    }
  }
  return fail(503, "code_collision");
}

async function updateProfile(request, env, me) {
  const body = await readJson(request);
  if (body === null) return fail(400, "bad_json");
  const fields = {
    name: cleanText(body.name, 24, me.name),
    species: cleanText(body.species, 16, me.species).toLowerCase().replace(/[^a-z_]/g, "") || me.species,
    color: cleanColor(body.color, me.color),
    level: cleanInt(body.level, 1, 999, me.level),
    xp: cleanInt(body.xp, 0, 10_000_000, me.xp),
    streak: cleanInt(body.streak, 0, 10_000, me.streak),
    day: cleanText(body.day, 10, me.day),
    today_poms: cleanInt(body.today_poms, 0, 200, 0),
    today_minutes: cleanInt(body.today_minutes, 0, 1440, 0),
    week: cleanText(body.week, 8, me.week),
    week_poms: cleanInt(body.week_poms, 0, 1400, 0),
    week_minutes: cleanInt(body.week_minutes, 0, 10080, 0),
    achievements: body.achievements === undefined ? me.achievements : cleanAchievements(body.achievements),
    mood: cleanText(body.mood, 16, ""),
    look: cleanLook(body.look, me.look || "{}"),
    visibility: body.visibility === "private" ? "private" : body.visibility === "public" ? "public" : me.visibility || "public",
    bio: body.bio === undefined ? me.bio || "" : cleanText(body.bio, 300, ""),
    info: cleanInfo(body.info, me.info || "{}"),
    status: STATUSES.has(body.status) ? body.status : me.status || "auto",
    status_text: body.status_text === undefined ? me.status_text || "" : cleanText(body.status_text, 60, ""),
    nudges_off: typeof body.allow_nudges === "boolean" ? (body.allow_nudges ? 0 : 1) : me.nudges_off || 0,
    app_ver: clientVersion(request) || inferredVersion(body),
    email: cleanEmail(body.email) ?? (me.email || ""),
  };
  if (body.gear !== undefined) await saveGear(env, me, body.gear);
  if (isMuted(me)) {
    fields.name = me.name;
    fields.bio = me.bio || "";
    fields.status_text = me.status_text || "";
    fields.info = me.info || "{}";
  }
  if (me.role === "owner") {
    fields.level = OWNER_LEVEL;
    fields.xp = Math.max(fields.xp, OWNER_XP);
  }
  await env.DB.prepare(
    "UPDATE users SET name=?, species=?, color=?, level=?, xp=?, streak=?, day=?, today_poms=?, " +
      "today_minutes=?, week=?, week_poms=?, week_minutes=?, achievements=?, mood=?, look=?, visibility=?, bio=?, " +
      "info=?, status=?, status_text=?, nudges_off=?, app_ver=?, email=?, updated_at=? WHERE id=?"
  )
    .bind(
      fields.name, fields.species, fields.color, fields.level, fields.xp, fields.streak, fields.day,
      fields.today_poms, fields.today_minutes, fields.week, fields.week_poms, fields.week_minutes,
      fields.achievements, fields.mood, fields.look, fields.visibility, fields.bio, fields.info, fields.status,
      fields.status_text, fields.nudges_off, fields.app_ver, fields.email, now(), me.id
    )
    .run();
  const row = await env.DB.prepare("SELECT * FROM users WHERE id = ?").bind(me.id).first();
  return json({ ok: true, me: publicProfile(row) });
}

async function board(env, me, url) {
  const day = cleanText(url.searchParams.get("day"), 10, "");
  const week = cleanText(url.searchParams.get("week"), 8, "");
  const friends = await env.DB.prepare(
    "SELECT u.* FROM friends f JOIN users u ON u.id = f.b WHERE f.a = ? ORDER BY u.name LIMIT ?"
  )
    .bind(me.id, MAX_FRIENDS_PREMIUM)
    .all();
  const groupRows = await env.DB.prepare("SELECT grp FROM members WHERE user_id = ? ORDER BY grp")
    .bind(me.id)
    .all();
  const groups = [];
  for (const { grp } of groupRows.results) {
    const members = await env.DB.prepare(
      "SELECT u.* FROM members m JOIN users u ON u.id = m.user_id WHERE m.grp = ? LIMIT 300"
    )
      .bind(grp)
      .all();
    groups.push({
      code: grp,
      members: members.results.map((row) => ({ ...publicProfile(row, day, week), me: row.id === me.id })),
    });
  }
  const affinity = await affinityMap(env, me);
  const labels = await labelMaps(env, me);
  const notices = await env.DB.prepare(
    "SELECT id, kind, body, created_at FROM notices WHERE user_id = ? AND created_at > ? ORDER BY id DESC LIMIT 20"
  )
    .bind(me.id, now() - 30 * 86400)
    .all();
  let staff = null;
  if (isStaff(me)) {
    const open = await env.DB.prepare("SELECT COUNT(*) AS n FROM reports WHERE status = 'open'").first();
    staff = { open_reports: open.n };
  }
  const friendObjs = friends.results.map((row) => ({
    ...publicProfile(row, day, week),
    affinity: affinity[row.id] || 0,
    label: labels.mine[row.id] || "",
    their_label: labels.theirs[row.id] || "",
  }));
  const meObj = {
    ...publicProfile(me, day, week),
    premium_until: me.premium_until || 0,
    muted_until: me.muted_until || 0,
    duel_coins: me.duel_coins || 0,
    rating: me.rating || 1000,
    duel_wins: me.duel_wins || 0,
    duel_losses: me.duel_losses || 0,
    loc_set: !!me.city,
    loc_visible: me.loc_visible !== 0,
    bday_m: me.bday_m || 0,
    bday_d: me.bday_d || 0,
    bday_visible: me.bday_visible !== 0,
    allow_nudges: !me.nudges_off,
  };
  const meRow = { ...me, loc_visible: 1 };
  await attachWeather(env, [meRow, ...friends.results], [meObj, ...friendObjs]);
  return json({
    ok: true,
    me: meObj,
    notices: notices.results.reverse(),
    staff,
    friends: friendObjs,
    requests: await requestsFor(env, me),
    sent: await sentRequests(env, me),
    groups,
    circles: await circlesFor(env, me, day, week),
    invites: await invitesFor(env, me),
    gifts: await pendingGifts(env, me),
  });
}

async function addFriend(request, env, me) {
  const body = await readJson(request);
  const code = cleanText(body && body.code, 8, "").toUpperCase();
  if (!/^[A-Z0-9]{6}$/.test(code)) return fail(400, "bad_code");
  if (code === me.code) return fail(400, "self");
  const other = await env.DB.prepare("SELECT * FROM users WHERE code = ?").bind(code).first();
  if (!other) return fail(404, "not_found");
  if (other.role === "bot") return fail(400, "bot");
  const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM friends WHERE a = ?").bind(me.id).first();
  if (count.n >= friendLimit(me)) return fail(400, "too_many");
  if (await areFriends(env, me.id, other.id)) return json({ ok: true, status: "added", friend: publicProfile(other) });
  // They already asked me -> accepting is the same as adding.
  const theyAsked = await env.DB.prepare("SELECT 1 AS ok FROM friend_requests WHERE from_id = ? AND to_id = ?")
    .bind(other.id, me.id)
    .first();
  if ((other.visibility || "public") === "private" && !theyAsked) {
    await env.DB.prepare("INSERT OR REPLACE INTO friend_requests (from_id, to_id, created_at) VALUES (?, ?, ?)")
      .bind(me.id, other.id, now())
      .run();
    return json({ ok: true, status: "requested", friend: limitedProfile(other) });
  }
  await makeFriends(env, me.id, other.id);
  return json({ ok: true, status: "added", friend: publicProfile(other) });
}

async function makeFriends(env, a, b) {
  const t = now();
  await env.DB.batch([
    env.DB.prepare("INSERT OR IGNORE INTO friends (a, b, created_at) VALUES (?, ?, ?)").bind(a, b, t),
    env.DB.prepare("INSERT OR IGNORE INTO friends (a, b, created_at) VALUES (?, ?, ?)").bind(b, a, t),
    env.DB.prepare("DELETE FROM friend_requests WHERE (from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?)")
      .bind(a, b, b, a),
  ]);
}

function limitedProfile(row) {
  let badges = 0;
  try {
    badges = JSON.parse(row.achievements || "[]").length;
  } catch {
    badges = 0;
  }
  return {
    code: row.code,
    name: row.name,
    species: row.species,
    color: row.color,
    look: parseLook(row.look),
    level: row.level,
    badges,
    avatar_v: row.avatar_v || 0,
    presence: presence(row),
    status_text: row.status_text || "",
    visibility: row.visibility || "public",
    role: row.role || "",
    premium: isPremium(row),
    rating: row.rating || 1000,
    duel_wins: row.duel_wins || 0,
    duel_losses: row.duel_losses || 0,
  };
}

async function requestsFor(env, me) {
  const rows = await env.DB.prepare(
    "SELECT u.* FROM friend_requests r JOIN users u ON u.id = r.from_id WHERE r.to_id = ? ORDER BY r.created_at LIMIT 50"
  )
    .bind(me.id)
    .all();
  return rows.results.map(limitedProfile);
}

async function sentRequests(env, me) {
  const rows = await env.DB.prepare(
    "SELECT u.code FROM friend_requests r JOIN users u ON u.id = r.to_id WHERE r.from_id = ? LIMIT 100"
  )
    .bind(me.id)
    .all();
  return rows.results.map((r) => r.code);
}

async function labelMaps(env, me) {
  const rows = await env.DB.prepare("SELECT owner, other, label FROM friend_labels WHERE owner = ? OR other = ?")
    .bind(me.id, me.id)
    .all();
  const mine = {};
  const theirs = {};
  for (const r of rows.results) {
    if (r.owner === me.id) mine[r.other] = r.label;
    else theirs[r.owner] = r.label;
  }
  return { mine, theirs };
}

async function userByCode(env, body) {
  const code = cleanText(body && body.code, 8, "").toUpperCase();
  if (!/^[A-Z0-9]{6}$/.test(code)) return null;
  return env.DB.prepare("SELECT * FROM users WHERE code = ?").bind(code).first();
}

async function respondRequest(request, env, me) {
  const body = await readJson(request);
  const other = await userByCode(env, body);
  if (!other) return fail(404, "not_found");
  const req = await env.DB.prepare("SELECT 1 AS ok FROM friend_requests WHERE from_id = ? AND to_id = ?")
    .bind(other.id, me.id)
    .first();
  if (!req) return fail(404, "not_found");
  if (body.accept) {
    const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM friends WHERE a = ?").bind(me.id).first();
    if (count.n >= friendLimit(me)) return fail(400, "too_many");
    await makeFriends(env, me.id, other.id);
  } else {
    await env.DB.prepare("DELETE FROM friend_requests WHERE from_id = ? AND to_id = ?").bind(other.id, me.id).run();
  }
  return json({ ok: true, accepted: !!body.accept, friend: limitedProfile(other) });
}

async function cancelRequest(request, env, me) {
  const body = await readJson(request);
  const other = await userByCode(env, body);
  if (!other) return fail(404, "not_found");
  await env.DB.prepare("DELETE FROM friend_requests WHERE from_id = ? AND to_id = ?").bind(me.id, other.id).run();
  return json({ ok: true });
}

async function setLabel(request, env, me) {
  const body = await readJson(request);
  const other = await userByCode(env, body);
  if (!other) return fail(404, "not_found");
  if (!(await areFriends(env, me.id, other.id))) return fail(403, "not_friends");
  const label = typeof body.label === "string" && LABELS.has(body.label) ? body.label : "";
  if (label) {
    await env.DB.prepare("INSERT OR REPLACE INTO friend_labels (owner, other, label) VALUES (?, ?, ?)")
      .bind(me.id, other.id, label)
      .run();
  } else {
    await env.DB.prepare("DELETE FROM friend_labels WHERE owner = ? AND other = ?").bind(me.id, other.id).run();
  }
  return json({ ok: true, label });
}

async function setAvatar(request, env, me) {
  const body = await readJson(request, 90000);
  if (!body) return fail(400, "bad_json");
  const data = typeof body.data === "string" ? body.data.replace(/\s/g, "") : "";
  if (!data) {
    await env.DB.batch([
      env.DB.prepare("DELETE FROM avatars WHERE user_id = ?").bind(me.id),
      env.DB.prepare("UPDATE users SET avatar_v = 0 WHERE id = ?").bind(me.id),
    ]);
    return json({ ok: true, avatar_v: 0 });
  }
  // base64 PNG only, <= ~60 KB
  if (data.length > 80000 || !/^[A-Za-z0-9+/]+=*$/.test(data) || !data.startsWith("iVBORw0KGgo")) {
    return fail(400, "bad_image");
  }
  const v = now();
  await env.DB.batch([
    env.DB.prepare("INSERT OR REPLACE INTO avatars (user_id, data, updated_at) VALUES (?, ?, ?)").bind(me.id, data, v),
    env.DB.prepare("UPDATE users SET avatar_v = ? WHERE id = ?").bind(v, me.id),
  ]);
  return json({ ok: true, avatar_v: v });
}

async function getAvatar(env, url) {
  const code = cleanText(url.searchParams.get("code"), 8, "").toUpperCase();
  const row = await env.DB.prepare(
    "SELECT a.data, a.updated_at FROM users u JOIN avatars a ON a.user_id = u.id WHERE u.code = ?"
  )
    .bind(code)
    .first();
  if (!row) return fail(404, "not_found");
  return json({ ok: true, data: row.data, v: row.updated_at });
}

function relationTo(me, other, friendIds, sentIds, incomingIds) {
  if (other.id === me.id) return "me";
  if (friendIds.has(other.id)) return "friend";
  if (sentIds.has(other.id)) return "sent";
  if (incomingIds.has(other.id)) return "incoming";
  return "none";
}

async function relationSets(env, me) {
  const [fr, sent, inc] = await Promise.all([
    env.DB.prepare("SELECT b AS id FROM friends WHERE a = ?").bind(me.id).all(),
    env.DB.prepare("SELECT to_id AS id FROM friend_requests WHERE from_id = ?").bind(me.id).all(),
    env.DB.prepare("SELECT from_id AS id FROM friend_requests WHERE to_id = ?").bind(me.id).all(),
  ]);
  return [
    new Set(fr.results.map((r) => r.id)),
    new Set(sent.results.map((r) => r.id)),
    new Set(inc.results.map((r) => r.id)),
  ];
}

const BOARD_SORTS = {
  xp: "xp DESC, level DESC",
  badges: "json_array_length(achievements) DESC, xp DESC",
  streak: "streak DESC, xp DESC",
};

async function leaderboard(env, me, url) {
  const sort = BOARD_SORTS[url.searchParams.get("sort")] ? url.searchParams.get("sort") : "xp";
  const rows = await env.DB.prepare(`SELECT * FROM users WHERE role != 'bot' ORDER BY ${BOARD_SORTS[sort]} LIMIT 100`).all();
  const [friendIds, sentIds, incomingIds] = await relationSets(env, me);
  const total = await env.DB.prepare("SELECT COUNT(*) AS n FROM users WHERE role != 'bot'").first();
  let myRank = 0;
  if (sort === "xp") {
    const better = await env.DB.prepare("SELECT COUNT(*) AS n FROM users WHERE xp > ? AND role != 'bot'").bind(me.xp).first();
    myRank = better.n + 1;
  }
  return json({
    ok: true,
    sort,
    total: total.n,
    my_rank: myRank,
    people: rows.results.map((row) => ({
      ...limitedProfile(row),
      xp: row.xp,
      streak: row.streak,
      rel: relationTo(me, row, friendIds, sentIds, incomingIds),
    })),
  });
}

async function viewUser(env, me, url) {
  const code = cleanText(url.searchParams.get("code"), 8, "").toUpperCase();
  const row = await env.DB.prepare("SELECT * FROM users WHERE code = ?").bind(code).first();
  if (!row) return fail(404, "not_found");
  const [friendIds, sentIds, incomingIds] = await relationSets(env, me);
  const rel = relationTo(me, row, friendIds, sentIds, incomingIds);
  const base = { ...limitedProfile(row), rel };
  if (isStaff(me)) {
    base.can_mod = canModerate(me, row);
    base.muted_until = row.muted_until || 0;
    base.premium_until = row.premium_until || 0;
  }
  const full = rel === "me" || rel === "friend" || (row.visibility || "public") === "public" || isStaff(me);
  if (!full) return json({ ok: true, full: false, user: base });
  let achievements = [];
  try {
    achievements = JSON.parse(row.achievements || "[]");
  } catch {
    achievements = [];
  }
  const fcount = await env.DB.prepare("SELECT COUNT(*) AS n FROM friends WHERE a = ?").bind(row.id).first();
  const friendRows = await env.DB.prepare(
    "SELECT u.* FROM friends f JOIN users u ON u.id = f.b WHERE f.a = ? AND (u.visibility = 'public' OR u.id = ? OR ?) " +
      "ORDER BY u.xp DESC LIMIT 24"
  )
    .bind(row.id, me.id, rel === "me" ? 1 : 0)
    .all();
  const user = {
    ...base,
    bio: row.bio || "",
    info: parseObj(row.info),
    xp: row.xp,
    streak: row.streak,
    achievements,
    week_minutes: row.week_minutes,
    friends_count: fcount.n,
    friends: friendRows.results.map(limitedProfile),
    member_since: row.created_at,
    last_seen: (row.status || "auto") === "offline" ? 0 : row.last_seen || 0,
  };
  await attachWeather(env, [rel === "me" ? { ...row, loc_visible: 1 } : row], [user]);
  if (rel === "friend") {
    const [a, b] = pairKey(me.id, row.id);
    const aff = await env.DB.prepare("SELECT points FROM affinity WHERE a = ? AND b = ?").bind(a, b).first();
    const labels = await labelMaps(env, me);
    user.affinity = aff ? aff.points : 0;
    user.label = labels.mine[row.id] || "";
    user.their_label = labels.theirs[row.id] || "";
  }
  return json({ ok: true, full: true, user });
}

async function removeFriend(request, env, me) {
  const body = await readJson(request);
  const code = cleanText(body && body.code, 8, "").toUpperCase();
  const other = await env.DB.prepare("SELECT id FROM users WHERE code = ?").bind(code).first();
  if (!other) return fail(404, "not_found");
  await env.DB.batch([
    env.DB.prepare("DELETE FROM friends WHERE a = ? AND b = ?").bind(me.id, other.id),
    env.DB.prepare("DELETE FROM friends WHERE a = ? AND b = ?").bind(other.id, me.id),
    env.DB.prepare("DELETE FROM friend_labels WHERE (owner = ? AND other = ?) OR (owner = ? AND other = ?)")
      .bind(me.id, other.id, other.id, me.id),
  ]);
  return json({ ok: true });
}

async function joinGroup(request, env, me) {
  const body = await readJson(request);
  const grp = cleanGroup(body && body.group);
  if (grp.length < 2) return fail(400, "bad_group");
  const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM members WHERE user_id = ?").bind(me.id).first();
  if (count.n >= MAX_GROUPS) return fail(400, "too_many");
  await env.DB.prepare("INSERT OR IGNORE INTO members (grp, user_id, joined_at) VALUES (?, ?, ?)")
    .bind(grp, me.id, now())
    .run();
  return json({ ok: true, group: grp });
}

async function leaveGroup(request, env, me) {
  const body = await readJson(request);
  const grp = cleanGroup(body && body.group);
  await env.DB.prepare("DELETE FROM members WHERE grp = ? AND user_id = ?").bind(grp, me.id).run();
  return json({ ok: true });
}

async function deleteMe(env, me) {
  const memberships = await env.DB.prepare("SELECT circle_id FROM circle_members WHERE user_id = ?").bind(me.id).all();
  for (const { circle_id } of memberships.results) await removeFromCircle(env, circle_id, me.id);
  await env.DB.batch([
    env.DB.prepare("DELETE FROM messages WHERE sender = ? OR (room LIKE 'dm:%' AND instr(room, ?) > 0)").bind(me.id, me.id),
    env.DB.prepare("DELETE FROM circle_invites WHERE user_id = ? OR invited_by = ?").bind(me.id, me.id),
    env.DB.prepare("DELETE FROM affinity WHERE a = ? OR b = ?").bind(me.id, me.id),
    env.DB.prepare("DELETE FROM friend_requests WHERE from_id = ? OR to_id = ?").bind(me.id, me.id),
    env.DB.prepare("DELETE FROM friend_labels WHERE owner = ? OR other = ?").bind(me.id, me.id),
    env.DB.prepare("DELETE FROM avatars WHERE user_id = ?").bind(me.id),
    env.DB.prepare("DELETE FROM reports WHERE reporter_id = ? OR target_id = ?").bind(me.id, me.id),
    env.DB.prepare("DELETE FROM notices WHERE user_id = ?").bind(me.id),
    env.DB.prepare("DELETE FROM duels WHERE a_id = ? OR b_id = ?").bind(me.id, me.id),
  ]);
  await env.DB.batch([
    env.DB.prepare("DELETE FROM friends WHERE a = ? OR b = ?").bind(me.id, me.id),
    env.DB.prepare("DELETE FROM members WHERE user_id = ?").bind(me.id),
    env.DB.prepare("DELETE FROM users WHERE id = ?").bind(me.id),
  ]);
  return json({ ok: true });
}


// ---------------------------------------------------------------- v2: chat, circles, affinity
// v11 nudges: remote pokes/tickles/teases that pop up on the friend's screen (no pet visit needed).
const NUDGE_KINDS = new Set(["poke", "tickle", "tease", "boop", "hug"]);
const NUDGE_GAP_S = 3;            // min seconds between two nudges to the same friend
const NUDGE_PER_HOUR = 40;        // per sender -> friend
const KINDS = new Set(["text", "wave", "pet", "snack", "highfive", "visit", "cheer", ...NUDGE_KINDS]);
const AFFINITY_POINTS = {
  text: 1, wave: 2, pet: 3, snack: 3, highfive: 3, visit: 5, cheer: 1,
  poke: 1, tickle: 2, tease: 1, boop: 1, hug: 3,
};
const AFFINITY_DAILY_CAP = 30;
const MESSAGE_TTL_S = 30 * 24 * 3600;
const MAX_CIRCLES_OWNED = 10;
const MAX_CIRCLE_MEMBERSHIPS = 20;
const MAX_CIRCLE_SIZE = 50;

const dmRoom = (x, y) => (x < y ? `dm:${x}:${y}` : `dm:${y}:${x}`);
const pairKey = (x, y) => (x < y ? [x, y] : [y, x]);
const today = () => new Date().toISOString().slice(0, 10);

async function areFriends(env, a, b) {
  const row = await env.DB.prepare("SELECT 1 AS ok FROM friends WHERE a = ? AND b = ?").bind(a, b).first();
  return !!row;
}

async function circleRole(env, circleId, userId) {
  const row = await env.DB.prepare("SELECT role FROM circle_members WHERE circle_id = ? AND user_id = ?")
    .bind(circleId, userId)
    .first();
  return row ? row.role : null;
}

async function addAffinity(env, x, y, kind) {
  const [a, b] = pairKey(x, y);
  const gain = AFFINITY_POINTS[kind] || 0;
  if (!gain) return;
  const d = today();
  const row = await env.DB.prepare("SELECT points, day, day_points FROM affinity WHERE a = ? AND b = ?").bind(a, b).first();
  const dayPoints = row && row.day === d ? row.day_points : 0;
  const add = Math.max(0, Math.min(gain, AFFINITY_DAILY_CAP - dayPoints));
  if (!add && row) return;
  await env.DB.prepare(
    "INSERT INTO affinity (a, b, points, day, day_points, updated_at) VALUES (?, ?, ?, ?, ?, ?) " +
      "ON CONFLICT(a, b) DO UPDATE SET points = points + ?, day = ?, day_points = ?, updated_at = ?"
  )
    .bind(a, b, add, d, add, now(), add, d, dayPoints + add, now())
    .run();
}

async function affinityMap(env, me) {
  const rows = await env.DB.prepare("SELECT a, b, points FROM affinity WHERE a = ? OR b = ?").bind(me.id, me.id).all();
  const map = {};
  for (const r of rows.results) map[r.a === me.id ? r.b : r.a] = r.points;
  return map;
}

async function circlesFor(env, me, day, week) {
  const rows = await env.DB.prepare(
    "SELECT c.id, c.name, c.owner_id, m.role FROM circle_members m JOIN circles c ON c.id = m.circle_id " +
      "WHERE m.user_id = ? ORDER BY c.name"
  )
    .bind(me.id)
    .all();
  const circles = [];
  for (const c of rows.results) {
    const members = await env.DB.prepare(
      "SELECT u.*, m.role AS circle_role FROM circle_members m JOIN users u ON u.id = m.user_id " +
        "WHERE m.circle_id = ? ORDER BY m.joined_at LIMIT ?"
    )
      .bind(c.id, MAX_CIRCLE_SIZE)
      .all();
    circles.push({
      id: c.id,
      name: c.name,
      role: c.role,
      members: members.results.map((row) => ({
        ...publicProfile(row, day, week),
        role: row.circle_role,
        me: row.id === me.id,
      })),
    });
  }
  return circles;
}

async function invitesFor(env, me) {
  const rows = await env.DB.prepare(
    "SELECT i.circle_id, c.name, u.name AS by_name FROM circle_invites i " +
      "JOIN circles c ON c.id = i.circle_id LEFT JOIN users u ON u.id = i.invited_by " +
      "WHERE i.user_id = ? ORDER BY i.created_at"
  )
    .bind(me.id)
    .all();
  return rows.results.map((r) => ({ circle: r.circle_id, name: r.name, by: r.by_name || "?" }));
}

async function sendMessage(request, env, me) {
  const body = await readJson(request);
  if (!body) return fail(400, "bad_json");
  if (body.channel !== undefined) return await sendCommunity(env, me, body);
  const kind = KINDS.has(body.kind) ? body.kind : "text";
  const text = cleanText(body.body, 500, "");
  if (kind === "text" && !text) return fail(400, "empty");
  if (isMuted(me)) return fail(403, "muted");
  const recent = await env.DB.prepare("SELECT COUNT(*) AS n FROM messages WHERE sender = ? AND created_at > ?")
    .bind(me.id, now() - 60)
    .first();
  if (recent.n >= 20) return fail(429, "too_fast");
  let room;
  let other = null;
  if (body.circle) {
    const circleId = cleanText(body.circle, 40, "");
    if (!(await circleRole(env, circleId, me.id))) return fail(403, "not_member");
    if (!["text", "cheer", "wave"].includes(kind)) return fail(400, "bad_kind");
    room = `c:${circleId}`;
  } else {
    const code = cleanText(body.to, 8, "").toUpperCase();
    other = await env.DB.prepare("SELECT id, nudges_off FROM users WHERE code = ?").bind(code).first();
    if (!other) return fail(404, "not_found");
    if (!(await areFriends(env, me.id, other.id))) return fail(403, "not_friends");
    room = dmRoom(me.id, other.id);
    if (NUDGE_KINDS.has(kind)) {
      if (other.nudges_off) return fail(403, "nudges_off");
      const nudges = await env.DB.prepare(
        "SELECT COUNT(*) AS n, MAX(created_at) AS last FROM messages WHERE room = ? AND sender = ? " +
          "AND kind IN ('poke', 'tickle', 'tease', 'boop', 'hug') AND created_at > ?"
      )
        .bind(room, me.id, now() - 3600)
        .first();
      if (nudges && nudges.last && now() - nudges.last < NUDGE_GAP_S) return fail(429, "nudge_slow");
      if (nudges && nudges.n >= NUDGE_PER_HOUR) return fail(429, "nudge_limit");
    }
    if (kind === "visit") {
      const last = await env.DB.prepare(
        "SELECT created_at FROM messages WHERE room = ? AND sender = ? AND kind = 'visit' ORDER BY id DESC LIMIT 1"
      )
        .bind(room, me.id)
        .first();
      if (last && now() - last.created_at < 120) return fail(429, "too_fast");
    }
  }
  const result = await env.DB.prepare("INSERT INTO messages (room, sender, kind, body, created_at) VALUES (?, ?, ?, ?, ?)")
    .bind(room, me.id, kind, text, now())
    .run();
  if (other) await addAffinity(env, me.id, other.id, kind);
  if (!other && kind === "text" && text.startsWith("@")) {
    try {
      await botInRoom(env, me, room, text, LANG_NAMES[body.lang] ? body.lang : "en");
    } catch (err) {
      console.error("bot", err);
    }
  }
  await maybeExpireMessages(env);
  return json({ ok: true, id: result.meta.last_row_id });
}

async function inbox(env, me, url) {
  const since = cleanInt(url.searchParams.get("since"), 0, Number.MAX_SAFE_INTEGER, 0);
  const nudgeAware = url.searchParams.get("nudge") === "1";
  const community = url.searchParams.get("community") === "1";   // v13: Pips >= 2.16
  const personal =
    "(m.room LIKE 'dm:%' AND instr(m.room, ?) > 0) OR " +
    "m.room IN (SELECT 'c:' || circle_id FROM circle_members WHERE user_id = ?)";
  const select =
    "SELECT m.id, m.room, m.kind, m.body, m.created_at, u.code AS sender_code, u.name AS sender_name, " +
    "u.species AS sender_species, u.color AS sender_color, u.look AS sender_look, u.role AS sender_role, (m.sender = ?) AS mine FROM messages m " +
    "LEFT JOIN users u ON u.id = m.sender WHERE ";
  let list;
  if (since) {
    const rooms = community ? `${personal} OR m.room IN (${COMMUNITY_SQL})` : personal;
    const rows = await env.DB.prepare(select + `m.id > ? AND (${rooms}) ORDER BY m.id ASC LIMIT 300`)
      .bind(me.id, since, me.id, me.id)
      .all();
    list = rows.results;
  } else {
    // first load: the latest personal messages plus each community channel's recent history
    const rows = await env.DB.prepare(select + `(${personal}) ORDER BY m.id DESC LIMIT 300`)
      .bind(me.id, me.id, me.id)
      .all();
    list = rows.results;
    if (community) {
      for (const channel of COMMUNITY_CHANNELS) {
        const recent = await env.DB.prepare(select + "m.room = ? ORDER BY m.id DESC LIMIT ?")
          .bind(me.id, channel.room, channel.history)
          .all();
        list = list.concat(recent.results);
      }
    }
    list.sort((a, b) => a.id - b.id);
  }
  // Convert DM room keys into the other person's friend code so clients never see user ids.
  const roomCodes = {};
  const messages = [];
  for (const m of list) {
    let room = m.room;
    if (room.startsWith("dm:")) {
      if (!(room in roomCodes)) {
        const parts = room.split(":");
        const otherId = parts[1] === me.id ? parts[2] : parts[1];
        const other = await env.DB.prepare("SELECT code FROM users WHERE id = ?").bind(otherId).first();
        roomCodes[room] = other ? `dm:${other.code}` : null;
      }
      room = roomCodes[room];
      if (!room) continue;
    }
    messages.push({
      id: m.id,
      room,
      kind: !nudgeAware && NUDGE_KINDS.has(m.kind) ? "wave" : m.kind,
      body: m.body,
      at: m.created_at,
      mine: !!m.mine,
      from: {
        code: m.sender_code,
        name: m.sender_name || "?",
        species: m.sender_species,
        color: m.sender_color,
        look: parseLook(m.sender_look),
        role: m.sender_role || "",
      },
    });
  }
  const lastId = list.length ? list[list.length - 1].id : since;
  const duelRows = await env.DB.prepare(
    "SELECT d.id, u.name, u.code FROM duels d JOIN users u ON u.id = d.a_id " +
      "WHERE d.b_id = ? AND d.status = 'pending' AND d.created_at > ? ORDER BY d.created_at DESC LIMIT 5"
  )
    .bind(me.id, now() - DUEL_CHALLENGE_TTL)
    .all();
  return json({
    ok: true,
    messages,
    last_id: Math.max(lastId, since),
    invites: await invitesFor(env, me),
    duels: duelRows.results.map((r) => ({ id: r.id, from: { name: r.name, code: r.code } })),
    ...(community ? await communityExtras(env) : {}),
  });
}

// ---------------------------------------------------------------- v13: Pips Community
// One Discord-style space for everyone with Friends turned on (Pips >= 2.16 asks for it with
// ?community=1 on /v1/inbox): #announcements (owner + moderators post; release.py posts every new
// version automatically) and #general (everyone chats). Announcements never expire; #general
// follows the normal 30-day message expiry.
const COMMUNITY_CHANNELS = [
  { id: "news", room: "pub:news", staffOnly: true, history: 40 },
  { id: "general", room: "pub:general", staffOnly: false, history: 120 },
];
const COMMUNITY_BY_ID = Object.fromEntries(COMMUNITY_CHANNELS.map((c) => [c.id, c]));
const COMMUNITY_ROOMS = new Set(COMMUNITY_CHANNELS.map((c) => c.room));
const COMMUNITY_SQL = COMMUNITY_CHANNELS.map((c) => `'${c.room}'`).join(", ");
const COMMUNITY_KINDS = new Set(["text", "wave", "cheer"]);
const KEPT_ROOMS_SQL = "'pub:news'";      // rooms that never expire
const NEWS_MAX_CHARS = 1500;             // staff announcements may be longer than chat lines
const RELEASE_MAX_CHARS = 6000;          // structured release post (notes in 4 languages)
const RELEASE_LANGS = ["en", "id", "zh_TW", "zh_CN"];
const COMMUNITY_GAP_S = 2;               // slow mode in #general
const COMMUNITY_MEMBER_DAYS = 30;        // "members" = Friends users seen in the last 30 days
const COMMUNITY_DELETED_DAYS = 7;        // how long clients are told about removed messages
let communityCache = { at: 0, data: null };

// Like cleanText, but keeps line breaks (announcements).
function cleanMultiline(value, max) {
  if (typeof value !== "string") return "";
  return value
    .replace(/\r\n?/g, "\n")
    .replace(/[\u0000-\u0009\u000b-\u001f\u007f]/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
    .slice(0, max);
}

// {v, title, date, notes: {en: [...], id: [...], ...}} - posted by release.py for every release.
function cleanRelease(value) {
  let data = value;
  if (typeof data === "string") {
    try {
      data = JSON.parse(data);
    } catch {
      return null;
    }
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const v = cleanText(data.v, 16, "");
  if (!EXACT_VERSION.test(v)) return null;
  const out = { v, title: cleanText(data.title, 80, ""), date: cleanText(data.date, 10, ""), notes: {} };
  const notes = data.notes && typeof data.notes === "object" && !Array.isArray(data.notes) ? data.notes : {};
  for (const lang of RELEASE_LANGS) {
    if (!Array.isArray(notes[lang])) continue;
    const lines = notes[lang].map((n) => cleanText(n, 400, "")).filter(Boolean).slice(0, 8);
    if (lines.length) out.notes[lang] = lines;
  }
  if (!out.notes.en) return null;
  return JSON.stringify(out).length <= RELEASE_MAX_CHARS ? out : null;
}

async function maybeExpireMessages(env) {
  if (Math.random() < 0.02) {
    await env.DB.prepare(`DELETE FROM messages WHERE created_at < ? AND room NOT IN (${KEPT_ROOMS_SQL})`)
      .bind(now() - MESSAGE_TTL_S)
      .run();
    await env.DB.prepare("DELETE FROM community_deleted WHERE deleted_at < ?")
      .bind(now() - COMMUNITY_DELETED_DAYS * 86400)
      .run();
  }
}

async function sendCommunity(env, me, body) {
  const channel = COMMUNITY_BY_ID[cleanText(body.channel, 16, "")];
  if (!channel) return fail(404, "not_found");
  if (isMuted(me)) return fail(403, "muted");
  let kind = typeof body.kind === "string" ? body.kind : "text";
  let text;
  let release = null;
  if (channel.staffOnly) {
    if (!isStaff(me)) return fail(403, "not_staff");
    if (kind === "release") {
      release = cleanRelease(body.body);
      if (!release) return fail(400, "bad_release");
      const dup = await env.DB.prepare(
        "SELECT id FROM messages WHERE room = ? AND kind = 'release' AND json_extract(body, '$.v') = ?"
      )
        .bind(channel.room, release.v)
        .first();
      if (dup) return json({ ok: true, id: dup.id, duplicate: true });
      text = JSON.stringify(release);
    } else {
      kind = "text";
      text = cleanMultiline(body.body, NEWS_MAX_CHARS);
    }
  } else {
    if (!COMMUNITY_KINDS.has(kind)) kind = "text";
    text = kind === "text" ? cleanText(body.body, 500, "") : "";
  }
  if (kind === "text" && !text) return fail(400, "empty");
  const recent = await env.DB.prepare("SELECT COUNT(*) AS n FROM messages WHERE sender = ? AND created_at > ?")
    .bind(me.id, now() - 60)
    .first();
  if (recent.n >= 20) return fail(429, "too_fast");
  if (!channel.staffOnly) {
    const last = await env.DB.prepare("SELECT MAX(created_at) AS t FROM messages WHERE room = ? AND sender = ?")
      .bind(channel.room, me.id)
      .first();
    if (last && last.t && now() - last.t < COMMUNITY_GAP_S) return fail(429, "too_fast");
  }
  const result = await env.DB.prepare("INSERT INTO messages (room, sender, kind, body, created_at) VALUES (?, ?, ?, ?, ?)")
    .bind(channel.room, me.id, kind, text, now())
    .run();
  if (channel.staffOnly) {
    await logAction(env, me, null, "announce", release ? `Pips ${release.v}` : text.slice(0, 120));
  }
  await maybeExpireMessages(env);
  return json({ ok: true, id: result.meta.last_row_id });
}

// Remove a community message: your own, or anyone's if you're staff (mods can't remove the owner's
// or other mods' posts). Clients drop it on their next inbox poll.
async function deleteCommunityMessage(request, env, me) {
  const body = await readJson(request);
  const id = cleanInt(body && body.id, 1, Number.MAX_SAFE_INTEGER, 0);
  if (!id) return fail(400, "bad_id");
  const msg = await env.DB.prepare(
    "SELECT m.id, m.room, m.sender, m.kind, m.body, u.role AS sender_role FROM messages m " +
      "LEFT JOIN users u ON u.id = m.sender WHERE m.id = ?"
  )
    .bind(id)
    .first();
  if (!msg || !COMMUNITY_ROOMS.has(msg.room)) return fail(404, "not_found");
  const own = msg.sender === me.id;
  if (!own) {
    if (!isStaff(me)) return fail(403, "not_staff");
    if (msg.sender_role !== null && !canModerate(me, { id: msg.sender, role: msg.sender_role || "" })) {
      return fail(403, "not_allowed");
    }
  }
  await env.DB.batch([
    env.DB.prepare("DELETE FROM messages WHERE id = ?").bind(id),
    env.DB.prepare("INSERT OR REPLACE INTO community_deleted (id, room, deleted_at) VALUES (?, ?, ?)")
      .bind(id, msg.room, now()),
  ]);
  if (!own) {
    const excerpt = msg.kind === "release" ? "release post" : (msg.body || "").slice(0, 120);
    await logAction(env, me, { id: msg.sender }, "delete_msg", excerpt);
  }
  return json({ ok: true });
}

async function communityStats(env) {
  const t = now();
  if (communityCache.data && t - communityCache.at < 60) return communityCache.data;
  const row = await env.DB.prepare(
    "SELECT COUNT(*) AS members, " +
      "SUM(CASE WHEN last_seen > ? AND COALESCE(status, 'auto') != 'offline' THEN 1 ELSE 0 END) AS online " +
      "FROM users WHERE COALESCE(role, '') != 'bot' AND last_seen > ?"
  )
    .bind(t - ONLINE_WINDOW_S, t - COMMUNITY_MEMBER_DAYS * 86400)
    .first();
  communityCache = { at: t, data: { members: (row && row.members) || 0, online: (row && row.online) || 0 } };
  return communityCache.data;
}

async function communityExtras(env) {
  const deleted = await env.DB.prepare(
    "SELECT id FROM community_deleted WHERE deleted_at > ? ORDER BY deleted_at DESC LIMIT 200"
  )
    .bind(now() - COMMUNITY_DELETED_DAYS * 86400)
    .all();
  return { community: await communityStats(env), deleted: deleted.results.map((r) => r.id) };
}


async function createCircle(request, env, me) {
  const body = await readJson(request);
  const name = cleanText(body && body.name, 32, "");
  if (name.length < 2) return fail(400, "bad_name");
  const owned = await env.DB.prepare("SELECT COUNT(*) AS n FROM circles WHERE owner_id = ?").bind(me.id).first();
  const joined = await env.DB.prepare("SELECT COUNT(*) AS n FROM circle_members WHERE user_id = ?").bind(me.id).first();
  const ownLimit = isPremium(me) ? MAX_CIRCLES_OWNED_PREMIUM : MAX_CIRCLES_OWNED;
  if (owned.n >= ownLimit || joined.n >= MAX_CIRCLE_MEMBERSHIPS) return fail(400, "too_many");
  const id = crypto.randomUUID().slice(0, 12);
  const t = now();
  await env.DB.batch([
    env.DB.prepare("INSERT INTO circles (id, name, owner_id, created_at) VALUES (?, ?, ?, ?)").bind(id, name, me.id, t),
    env.DB.prepare("INSERT INTO circle_members (circle_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)").bind(id, me.id, t),
  ]);
  return json({ ok: true, circle: { id, name } });
}

async function inviteToCircle(request, env, me) {
  const body = await readJson(request);
  const circleId = cleanText(body && body.circle, 40, "");
  if (!(await circleRole(env, circleId, me.id))) return fail(403, "not_member");
  const code = cleanText(body && body.code, 8, "").toUpperCase();
  const other = await env.DB.prepare("SELECT id, name FROM users WHERE code = ?").bind(code).first();
  if (!other) return fail(404, "not_found");
  if (!(await areFriends(env, me.id, other.id))) return fail(403, "not_friends");
  if (await circleRole(env, circleId, other.id)) return fail(400, "already_member");
  const size = await env.DB.prepare("SELECT COUNT(*) AS n FROM circle_members WHERE circle_id = ?").bind(circleId).first();
  if (size.n >= MAX_CIRCLE_SIZE) return fail(400, "too_many");
  await env.DB.prepare("INSERT OR REPLACE INTO circle_invites (circle_id, user_id, invited_by, created_at) VALUES (?, ?, ?, ?)")
    .bind(circleId, other.id, me.id, now())
    .run();
  return json({ ok: true, invited: other.name });
}

async function respondInvite(request, env, me) {
  const body = await readJson(request);
  const circleId = cleanText(body && body.circle, 40, "");
  const invite = await env.DB.prepare("SELECT 1 AS ok FROM circle_invites WHERE circle_id = ? AND user_id = ?")
    .bind(circleId, me.id)
    .first();
  if (!invite) return fail(404, "not_found");
  const ops = [env.DB.prepare("DELETE FROM circle_invites WHERE circle_id = ? AND user_id = ?").bind(circleId, me.id)];
  if (body.accept) {
    const joined = await env.DB.prepare("SELECT COUNT(*) AS n FROM circle_members WHERE user_id = ?").bind(me.id).first();
    if (joined.n >= MAX_CIRCLE_MEMBERSHIPS) return fail(400, "too_many");
    ops.push(
      env.DB.prepare("INSERT OR IGNORE INTO circle_members (circle_id, user_id, role, joined_at) VALUES (?, ?, 'member', ?)")
        .bind(circleId, me.id, now())
    );
  }
  await env.DB.batch(ops);
  return json({ ok: true });
}

async function removeFromCircle(env, circleId, userId) {
  const role = await circleRole(env, circleId, userId);
  if (!role) return;
  await env.DB.prepare("DELETE FROM circle_members WHERE circle_id = ? AND user_id = ?").bind(circleId, userId).run();
  if (role === "owner") {
    const next = await env.DB.prepare(
      "SELECT user_id FROM circle_members WHERE circle_id = ? ORDER BY joined_at LIMIT 1"
    )
      .bind(circleId)
      .first();
    if (next) {
      await env.DB.batch([
        env.DB.prepare("UPDATE circle_members SET role = 'owner' WHERE circle_id = ? AND user_id = ?").bind(circleId, next.user_id),
        env.DB.prepare("UPDATE circles SET owner_id = ? WHERE id = ?").bind(next.user_id, circleId),
      ]);
    } else {
      await env.DB.batch([
        env.DB.prepare("DELETE FROM circles WHERE id = ?").bind(circleId),
        env.DB.prepare("DELETE FROM circle_invites WHERE circle_id = ?").bind(circleId),
        env.DB.prepare("DELETE FROM messages WHERE room = ?").bind(`c:${circleId}`),
      ]);
    }
  }
}

async function leaveCircle(request, env, me) {
  const body = await readJson(request);
  await removeFromCircle(env, cleanText(body && body.circle, 40, ""), me.id);
  return json({ ok: true });
}

async function kickFromCircle(request, env, me) {
  const body = await readJson(request);
  const circleId = cleanText(body && body.circle, 40, "");
  if ((await circleRole(env, circleId, me.id)) !== "owner") return fail(403, "not_owner");
  const other = await env.DB.prepare("SELECT id FROM users WHERE code = ?")
    .bind(cleanText(body && body.code, 8, "").toUpperCase())
    .first();
  if (!other || other.id === me.id) return fail(404, "not_found");
  await removeFromCircle(env, circleId, other.id);
  return json({ ok: true });
}

// ---------------------------------------------------------------- v6: moderators, premium, reports
const NOTICE_KINDS = new Set([
  "warn", "mute", "unmute", "cleared", "renamed", "mod_on", "mod_off", "premium_on", "premium_off",
]);

async function notify(env, userId, kind, body = "") {
  if (!NOTICE_KINDS.has(kind)) return;
  await env.DB.prepare("INSERT INTO notices (user_id, kind, body, created_at) VALUES (?, ?, ?, ?)")
    .bind(userId, kind, String(body).slice(0, 300), now())
    .run();
}

async function logAction(env, actor, target, action, detail = "") {
  await env.DB.prepare("INSERT INTO mod_log (actor_id, target_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)")
    .bind(actor.id, target ? target.id : null, action, String(detail).slice(0, 200), now())
    .run();
}

// Mods can act on normal users; only the owner can act on mods; nobody acts on the owner or themselves.
function canModerate(actor, target) {
  if (!isStaff(actor) || !target || actor.id === target.id) return false;
  if (target.role === "owner") return false;
  if (target.role === "mod") return actor.role === "owner";
  return true;
}

function premiumUntil(current, days) {
  if (days === -1) return PREMIUM_FOREVER;
  if (days <= 0) return 0;
  if ((current || 0) >= PREMIUM_FOREVER) return PREMIUM_FOREVER;
  return Math.max(now(), current || 0) + days * 86400;
}

async function reportUser(request, env, me) {
  const body = await readJson(request);
  const target = await userByCode(env, body);
  if (!target) return fail(404, "not_found");
  if (target.id === me.id) return fail(400, "self");
  const reason = cleanText(body.reason, 200, "");
  const recent = await env.DB.prepare("SELECT COUNT(*) AS n FROM reports WHERE reporter_id = ? AND created_at > ?")
    .bind(me.id, now() - 86400)
    .first();
  if (recent.n >= 10) return fail(429, "too_fast");
  const open = await env.DB.prepare(
    "SELECT id FROM reports WHERE reporter_id = ? AND target_id = ? AND status = 'open'"
  )
    .bind(me.id, target.id)
    .first();
  if (open) {
    await env.DB.prepare("UPDATE reports SET reason = ?, created_at = ? WHERE id = ?").bind(reason, now(), open.id).run();
  } else {
    await env.DB.prepare("INSERT INTO reports (reporter_id, target_id, reason, created_at) VALUES (?, ?, ?, ?)")
      .bind(me.id, target.id, reason, now())
      .run();
  }
  return json({ ok: true });
}

function staffView(row) {
  return {
    ...limitedProfile(row),
    bio: row.bio || "",
    muted_until: row.muted_until || 0,
    premium_until: row.premium_until || 0,
  };
}

async function modReports(env, me) {
  if (!isStaff(me)) return fail(403, "not_staff");
  const rows = await env.DB.prepare(
    "SELECT r.id, r.reason, r.created_at, r.target_id, rep.name AS reporter_name, rep.code AS reporter_code " +
      "FROM reports r LEFT JOIN users rep ON rep.id = r.reporter_id WHERE r.status = 'open' " +
      "ORDER BY r.created_at DESC LIMIT 60"
  ).all();
  const targets = {};
  const reports = [];
  for (const r of rows.results) {
    if (!(r.target_id in targets)) {
      targets[r.target_id] = await env.DB.prepare("SELECT * FROM users WHERE id = ?").bind(r.target_id).first();
    }
    const target = targets[r.target_id];
    if (!target) continue;
    reports.push({
      id: r.id,
      reason: r.reason,
      at: r.created_at,
      reporter: { name: r.reporter_name || "?", code: r.reporter_code || "" },
      target: { ...staffView(target), can_act: canModerate(me, target) },
    });
  }
  return json({ ok: true, reports, log: await recentLog(env, 30) });
}

async function recentLog(env, limit) {
  const rows = await env.DB.prepare(
    "SELECT l.action, l.detail, l.created_at, a.name AS actor_name, a.code AS actor_code, " +
      "t.name AS target_name, t.code AS target_code FROM mod_log l " +
      "LEFT JOIN users a ON a.id = l.actor_id LEFT JOIN users t ON t.id = l.target_id " +
      "WHERE l.action != 'bad_key' ORDER BY l.id DESC LIMIT ?"
  )
    .bind(limit)
    .all();
  return rows.results.map((r) => ({
    action: r.action,
    detail: r.detail,
    at: r.created_at,
    actor: r.actor_name || "?",
    actor_code: r.actor_code || "",
    target: r.target_name || "",
    target_code: r.target_code || "",
  }));
}

async function modAction(request, env, me) {
  if (!isStaff(me)) return fail(403, "not_staff");
  const body = await readJson(request);
  if (!body) return fail(400, "bad_json");
  const action = cleanText(body.action, 16, "");
  if (action === "dismiss") {
    const id = cleanInt(body.report, 0, Number.MAX_SAFE_INTEGER, 0);
    const row = await env.DB.prepare("SELECT target_id FROM reports WHERE id = ?").bind(id).first();
    if (!row) return fail(404, "not_found");
    await env.DB.prepare("UPDATE reports SET status = 'dismissed', handled_by = ?, handled_at = ? WHERE id = ?")
      .bind(me.id, now(), id)
      .run();
    await logAction(env, me, { id: row.target_id }, "dismiss", `#${id}`);
    return json({ ok: true, action });
  }
  const target = await userByCode(env, body);
  if (!target) return fail(404, "not_found");
  if (!canModerate(me, target)) return fail(403, "not_allowed");
  let detail = "";
  if (action === "warn") {
    detail = cleanText(body.text, 200, "") || "Please be kind and follow the Pips community rules.";
    await notify(env, target.id, "warn", detail);
  } else if (action === "mute") {
    const hours = cleanInt(body.hours, 1, 720, 24);
    const until = now() + hours * 3600;
    await env.DB.prepare("UPDATE users SET muted_until = ? WHERE id = ?").bind(until, target.id).run();
    await notify(env, target.id, "mute", String(until));
    detail = `${hours}h`;
  } else if (action === "unmute") {
    await env.DB.prepare("UPDATE users SET muted_until = 0 WHERE id = ?").bind(target.id).run();
    await notify(env, target.id, "unmute");
  } else if (action === "clear_profile") {
    await env.DB.batch([
      env.DB.prepare("UPDATE users SET bio = '', status_text = '', info = '{}', avatar_v = 0 WHERE id = ?").bind(target.id),
      env.DB.prepare("DELETE FROM avatars WHERE user_id = ?").bind(target.id),
    ]);
    await notify(env, target.id, "cleared");
  } else if (action === "reset_name") {
    await env.DB.prepare("UPDATE users SET name = 'Pips friend' WHERE id = ?").bind(target.id).run();
    await notify(env, target.id, "renamed");
  } else {
    return fail(400, "bad_action");
  }
  await env.DB.prepare(
    "UPDATE reports SET status = 'resolved', handled_by = ?, handled_at = ? WHERE target_id = ? AND status = 'open'"
  )
    .bind(me.id, now(), target.id)
    .run();
  await logAction(env, me, target, action, detail);
  const row = await env.DB.prepare("SELECT * FROM users WHERE id = ?").bind(target.id).first();
  return json({ ok: true, action, user: staffView(row) });
}

// ---------------------------------------------------------------- v12: who still needs to update
async function modVersions(env, me) {
  if (!isStaff(me)) return fail(403, "not_staff");
  const rows = await env.DB.prepare(
    "SELECT * FROM users WHERE role != 'bot' " +
      "ORDER BY max(COALESCE(last_seen, 0), COALESCE(updated_at, 0)) DESC LIMIT 500"
  ).all();
  let latest = await latestVersion();
  if (!latest) {
    latest = rows.results.map((r) => r.app_ver || "").filter((v) => versionParts(v)).sort(compareVersions).pop() || "";
  }
  const activeSince = now() - ACTIVE_DAYS * 86400;
  const counts = { active: 0, current: 0, outdated: 0, inactive: 0 };
  const versions = {};
  const current = [];
  const outdated = [];
  for (const row of rows.results) {
    const seen = Math.max(row.last_seen || 0, row.updated_at || 0);
    const active = seen >= activeSince;
    const ver = row.app_ver || "";
    const upToDate = !!latest && !!versionParts(ver) && compareVersions(ver, latest) >= 0;
    if (active) {
      counts.active += 1;
      versions[ver] = (versions[ver] || 0) + 1;
    }
    if (upToDate) {
      if (active) {
        counts.current += 1;
        current.push({ code: row.code, name: row.name, app_ver: ver });
      }
      continue;
    }
    counts[active ? "outdated" : "inactive"] += 1;
    outdated.push({
      ...limitedProfile(row),
      app_ver: ver,
      seen,
      active,
      email: row.email || "",
      reminded_at: row.reminded_at || 0,
      reminded_by: row.reminded_by || "",
      reminded_via: row.reminded_via || "",
    });
  }
  return json({
    ok: true,
    latest,
    active_days: ACTIVE_DAYS,
    counts,
    versions,
    current: current.slice(0, 100),
    outdated: outdated.slice(0, 300),
  });
}

async function modContact(request, env, me) {
  if (!isStaff(me)) return fail(403, "not_staff");
  const body = await readJson(request);
  if (!body) return fail(400, "bad_json");
  const target = await userByCode(env, body);
  if (!target) return fail(404, "not_found");
  const email = cleanEmail(body.email);
  if (email === null) return fail(400, "bad_email");
  await env.DB.prepare("UPDATE users SET email = ? WHERE id = ?").bind(email, target.id).run();
  await logAction(env, me, target, "contact", email ? "set" : "cleared");
  return json({ ok: true, code: target.code, email });
}

async function modRemind(request, env, me) {
  if (!isStaff(me)) return fail(403, "not_staff");
  const body = await readJson(request);
  if (!body) return fail(400, "bad_json");
  const via = REMIND_VIA.has(body.via) ? body.via : "email";
  const codes = [...new Set((Array.isArray(body.codes) ? body.codes : [])
    .map((c) => cleanText(c, 8, "").toUpperCase())
    .filter(Boolean))].slice(0, 100);
  if (!codes.length) return fail(400, "no_codes");
  const t = now();
  const targets = [];
  for (const code of codes) {
    const target = await env.DB.prepare("SELECT id, code, name FROM users WHERE code = ?").bind(code).first();
    if (target) targets.push(target);
  }
  if (!targets.length) return fail(404, "not_found");
  await env.DB.batch(targets.map((target) =>
    env.DB.prepare("UPDATE users SET reminded_at = ?, reminded_by = ?, reminded_via = ? WHERE id = ?")
      .bind(t, me.name, via, target.id)
  ));
  if (targets.length === 1) await logAction(env, me, targets[0], "remind", via);
  else await logAction(env, me, null, "remind_all", `${targets.length} \u00b7 ${via}`);
  return json({ ok: true, reminded: targets.length, via, at: t });
}

async function adminRole(request, env, me) {
  if (me.role !== "owner") return fail(403, "not_owner");
  const body = await readJson(request);
  const target = await userByCode(env, body);
  if (!target) return fail(404, "not_found");
  if (target.id === me.id || target.role === "owner") return fail(400, "self");
  const role = body.role === "mod" ? "mod" : "";
  await env.DB.prepare("UPDATE users SET role = ? WHERE id = ?").bind(role, target.id).run();
  await notify(env, target.id, role ? "mod_on" : "mod_off");
  await logAction(env, me, target, role ? "mod_on" : "mod_off");
  return json({ ok: true, role });
}

async function adminPremium(request, env, me) {
  if (me.role !== "owner") return fail(403, "not_owner");
  const body = await readJson(request);
  const target = await userByCode(env, body);
  if (!target) return fail(404, "not_found");
  const days = cleanInt(body.days, -1, 3650, 30);
  const until = premiumUntil(target.premium_until, days);
  await env.DB.prepare("UPDATE users SET premium_until = ? WHERE id = ?").bind(until, target.id).run();
  await notify(env, target.id, until ? "premium_on" : "premium_off", String(until));
  await logAction(env, me, target, until ? "premium_on" : "premium_off", days === -1 ? "forever" : `${days}d`);
  return json({ ok: true, premium_until: until });
}

async function adminKeys(request, env, me) {
  if (me.role !== "owner") return fail(403, "not_owner");
  const body = await readJson(request);
  let days = cleanInt(body && body.days, -1, 3650, 30);
  if (days === 0) days = 30;
  const count = cleanInt(body && body.count, 1, 20, 1);
  const note = cleanText(body && body.note, 60, "");
  const keys = [];
  for (let i = 0; i < count; i++) {
    const key = `PIPS-${randomCode(4)}-${randomCode(4)}`;
    await env.DB.prepare("INSERT INTO premium_keys (key, days, created_by, created_at) VALUES (?, ?, ?, ?)")
      .bind(key, days, me.id, now())
      .run();
    keys.push(key);
  }
  await logAction(env, me, null, "keys", `${count} x ${days === -1 ? "forever" : days + "d"}${note ? " - " + note : ""}`);
  return json({ ok: true, keys, days });
}

async function adminOverview(env, me) {
  if (me.role !== "owner") return fail(403, "not_owner");
  const t = now();
  const [mods, premium, keys, used] = await Promise.all([
    env.DB.prepare("SELECT * FROM users WHERE role = 'mod' ORDER BY name LIMIT 100").all(),
    env.DB.prepare("SELECT * FROM users WHERE premium_until > ? AND role = '' ORDER BY premium_until DESC LIMIT 200")
      .bind(t)
      .all(),
    env.DB.prepare("SELECT key, days, created_at FROM premium_keys WHERE redeemed_by IS NULL ORDER BY created_at DESC LIMIT 40").all(),
    env.DB.prepare(
      "SELECT k.key, k.days, k.redeemed_at, u.name, u.code FROM premium_keys k LEFT JOIN users u ON u.id = k.redeemed_by " +
        "WHERE k.redeemed_by IS NOT NULL ORDER BY k.redeemed_at DESC LIMIT 20"
    ).all(),
  ]);
  const total = await env.DB.prepare("SELECT COUNT(*) AS n FROM users WHERE role != 'bot'").first();
  return json({
    ok: true,
    users: total.n,
    mods: mods.results.map(staffView),
    premium: premium.results.map(staffView),
    keys: keys.results,
    redeemed: used.results,
    log: await recentLog(env, 40),
  });
}

async function redeemKey(request, env, me) {
  const body = await readJson(request);
  const key = cleanText(body && body.key, 24, "").toUpperCase().replace(/[^A-Z0-9-]/g, "");
  const recentFails = await env.DB.prepare(
    "SELECT COUNT(*) AS n FROM mod_log WHERE actor_id = ? AND action = 'bad_key' AND created_at > ?"
  )
    .bind(me.id, now() - 3600)
    .first();
  if (recentFails.n >= 10) return fail(429, "too_fast");
  const row = key ? await env.DB.prepare("SELECT * FROM premium_keys WHERE key = ?").bind(key).first() : null;
  if (!row || row.redeemed_by) {
    await logAction(env, me, null, "bad_key", key.slice(0, 14));
    return fail(404, "bad_key");
  }
  const claim = await env.DB.prepare(
    "UPDATE premium_keys SET redeemed_by = ?, redeemed_at = ? WHERE key = ? AND redeemed_by IS NULL"
  )
    .bind(me.id, now(), key)
    .run();
  if (!claim.meta || claim.meta.changes !== 1) return fail(404, "bad_key");
  const until = premiumUntil(me.premium_until, row.days);
  await env.DB.prepare("UPDATE users SET premium_until = ? WHERE id = ?").bind(until, me.id).run();
  await notify(env, me.id, "premium_on", String(until));
  await logAction(env, me, me, "redeem", `${key} (${row.days === -1 ? "forever" : row.days + "d"})`);
  return json({ ok: true, premium_until: until });
}

// ---------------------------------------------------------------- v7: arena & duels
// Gear stats live on the server so fights can't be tampered with. The client mirrors this table for the shop.
const GEAR = {
  water_pistol: { slot: "weapon", atk: 4, kind: "gun" },
  slingshot: { slot: "weapon", atk: 6, crit: 3, kind: "gun" },
  foam_sword: { slot: "weapon", atk: 8, kind: "melee" },
  bubble_blaster: { slot: "weapon", atk: 10, spd: 2, kind: "gun" },
  laser_blaster: { slot: "weapon", atk: 13, crit: 5, kind: "gun" },
  rocket_launcher: { slot: "weapon", atk: 18, spd: -2, kind: "launcher" },
  plasma_cannon: { slot: "weapon", atk: 20, crit: 8, kind: "gun", premium: true },
  cardboard_armor: { slot: "armor", def: 2, hp: 10 },
  leather_armor: { slot: "armor", def: 4, hp: 20 },
  knight_armor: { slot: "armor", def: 7, hp: 35, spd: -1 },
  ninja_suit: { slot: "armor", def: 5, hp: 20, spd: 4 },
  mech_suit: { slot: "armor", def: 11, hp: 60, premium: true },
  medkit: { slot: "tool", special: "heal" },
  water_bomb: { slot: "tool", special: "bomb" },
  jetpack: { slot: "tool", special: "dodge", spd: 3 },
  lucky_charm: { slot: "tool", special: "power", crit: 10 },
  force_field: { slot: "tool", special: "shield", premium: true },
};
const GEAR_SLOTS = ["weapon", "armor", "tool"];
const DUEL_ROUND_S = 12;
const DUEL_MAX_ROUNDS = 12;
const DUEL_CHALLENGE_TTL = 120;
const DUEL_SPECIAL_COST = 3;
const DUEL_MAX_MISSES = 3;
const DUEL_REWARDS_PER_DAY = 6;
const DUEL_ACTIONS = new Set(["attack", "guard", "special"]);

function cleanGear(value, user) {
  const out = {};
  if (!value || typeof value !== "object" || Array.isArray(value)) return out;
  for (const slot of GEAR_SLOTS) {
    const id = value[slot];
    const item = typeof id === "string" ? GEAR[id] : null;
    if (item && item.slot === slot && (!item.premium || isPremium(user))) out[slot] = id;
  }
  return out;
}

function fighterStats(row) {
  const gear = cleanGear(parseObj(row.gear), row);
  const lvl = Math.max(1, Math.min(99, row.level || 1));
  let hp = 120 + 2.5 * lvl;
  let atk = 18 + 0.4 * lvl;
  let def = 0.2 * lvl;
  let crit = 8;
  let spd = 10 + 0.1 * lvl;
  let special = "power";
  let weaponKind = "paw";
  for (const slot of GEAR_SLOTS) {
    const item = GEAR[gear[slot]];
    if (!item) continue;
    hp += item.hp || 0;
    atk += item.atk || 0;
    def += item.def || 0;
    crit += item.crit || 0;
    spd += item.spd || 0;
    if (item.special) special = item.special;
    if (item.kind) weaponKind = item.kind;
  }
  crit = Math.min(40, crit);
  const r1 = (x) => Math.round(x * 10) / 10;
  return {
    hp: Math.round(hp),
    atk: r1(atk),
    def: r1(def),
    crit: r1(crit),
    spd: r1(spd),
    special,
    weapon_kind: weaponKind,
    gear,
    level: lvl,
    power: Math.round(hp / 4 + atk * 3 + def * 3 + crit + spd),
  };
}

function fighterSnapshot(row) {
  return {
    code: row.code,
    name: row.name,
    species: row.species,
    color: row.color,
    look: parseLook(row.look),
    role: row.role || "",
    premium: isPremium(row),
    rating: row.rating || 1000,
    ...fighterStats(row),
  };
}

function arenaCard(row) {
  const stats = fighterStats(row);
  return {
    ...limitedProfile(row),
    rating: row.rating || 1000,
    wins: row.duel_wins || 0,
    losses: row.duel_losses || 0,
    power: stats.power,
    gear: stats.gear,
    in_arena: (row.arena_until || 0) > now(),
  };
}

// One round: both actions known (missing = attack). Returns new hp/charge + events + winner.
function resolveRound(d, A, B, rng = Math.random) {
  const side = {
    a: { f: A, hp: d.a_hp, charge: d.a_charge, act: DUEL_ACTIONS.has(d.a_act) ? d.a_act : "attack" },
    b: { f: B, hp: d.b_hp, charge: d.b_charge, act: DUEL_ACTIONS.has(d.b_act) ? d.b_act : "attack" },
  };
  for (const s of ["a", "b"]) {
    if (side[s].act === "special" && side[s].charge < DUEL_SPECIAL_COST) side[s].act = "attack";
  }
  const guarding = { a: side.a.act === "guard", b: side.b.act === "guard" };
  const shielded = {
    a: side.a.act === "special" && A.special === "shield",
    b: side.b.act === "special" && B.special === "shield",
  };
  const jetDodge = {
    a: side.a.act === "special" && A.special === "dodge",
    b: side.b.act === "special" && B.special === "dodge",
  };
  let order = A.spd === B.spd ? (rng() < 0.5 ? ["a", "b"] : ["b", "a"]) : A.spd > B.spd ? ["a", "b"] : ["b", "a"];
  const events = [];
  for (const s of order) {
    const o = s === "a" ? "b" : "a";
    const me = side[s];
    const op = side[o];
    if (me.hp <= 0) continue;
    const ev = { r: d.round, s, act: me.act };
    if (me.act === "guard") {
      events.push(ev);
      continue;
    }
    let mult = 1;
    let pierce = false;
    if (me.act === "special") {
      me.charge -= DUEL_SPECIAL_COST;
      ev.special = me.f.special;
      if (me.f.special === "heal") {
        const before = me.hp;
        me.hp = Math.min(me.f.hp, me.hp + Math.round(me.f.hp * 0.3));
        ev.heal = me.hp - before;
        mult = 0.5;
      } else if (me.f.special === "bomb") {
        mult = 2.0;
        pierce = true;
      } else if (me.f.special === "dodge") {
        mult = 1.3;
      } else if (me.f.special === "shield") {
        mult = 1.4;
      } else {
        mult = 1.8;
        pierce = true;
      }
    }
    if (shielded[o]) {
      ev.dmg = 0;
      ev.shield = true;
    } else {
      const dodgeChance = Math.min(0.3, Math.max(0.04, 0.08 + (op.f.spd - me.f.spd) * 0.012));
      if (jetDodge[o] || rng() < dodgeChance) {
        ev.dmg = 0;
        ev.dodge = true;
      } else {
        let dmg = me.f.atk * (0.55 + rng() * 0.9) * mult;
        dmg *= 100 / (100 + op.f.def * 4);
        if (rng() * 100 < me.f.crit) {
          dmg *= 1.7;
          ev.crit = true;
        }
        if (guarding[o] && !pierce) {
          dmg *= 0.35;
          ev.blocked = true;
        }
        dmg = Math.max(1, Math.round(dmg));
        op.hp = Math.max(0, op.hp - dmg);
        ev.dmg = dmg;
      }
    }
    ev.hp_a = side.a.hp;
    ev.hp_b = side.b.hp;
    events.push(ev);
  }
  for (const s of ["a", "b"]) {
    side[s].charge = Math.min(6, side[s].charge + (side[s].act === "guard" ? 2 : 1));
  }
  let winner = null;
  if (side.a.hp <= 0 && side.b.hp <= 0) winner = "draw";
  else if (side.a.hp <= 0) winner = "b";
  else if (side.b.hp <= 0) winner = "a";
  else if (d.round >= DUEL_MAX_ROUNDS) {
    const pa = side.a.hp / A.hp;
    const pb = side.b.hp / B.hp;
    winner = Math.abs(pa - pb) < 0.001 ? "draw" : pa > pb ? "a" : "b";
  }
  return { a_hp: side.a.hp, b_hp: side.b.hp, a_charge: side.a.charge, b_charge: side.b.charge, events, winner };
}

function aiAction(hp, maxHp, charge, rng = Math.random) {
  if (charge >= DUEL_SPECIAL_COST && rng() < 0.75) return "special";
  if (hp / maxHp < 0.4 && rng() < 0.35) return "guard";
  return rng() < 0.12 ? "guard" : "attack";
}

async function rewardFor(env, userId, coins) {
  if (!coins) return 0;
  const today = new Date().toISOString().slice(0, 10);
  const row = await env.DB.prepare("SELECT duel_day, duel_day_rewards FROM users WHERE id = ?").bind(userId).first();
  if (!row) return 0;
  const used = row.duel_day === today ? row.duel_day_rewards : 0;
  if (used >= DUEL_REWARDS_PER_DAY) return 0;
  await env.DB.prepare("UPDATE users SET duel_coins = duel_coins + ?, duel_day = ?, duel_day_rewards = ? WHERE id = ?")
    .bind(coins, today, used + 1, userId)
    .run();
  return coins;
}

// Ratings (Elo), win/loss counters and coin rewards once a duel ends.
async function finishDuel(env, d, winner) {
  const [ua, ub] = await Promise.all([
    env.DB.prepare("SELECT id, rating FROM users WHERE id = ?").bind(d.a_id).first(),
    env.DB.prepare("SELECT id, rating FROM users WHERE id = ?").bind(d.b_id).first(),
  ]);
  const ra = ua ? ua.rating || 1000 : 1000;
  const rb = ub ? ub.rating || 1000 : 1000;
  const ea = 1 / (1 + Math.pow(10, (rb - ra) / 400));
  const sa = winner === "a" ? 1 : winner === "b" ? 0 : 0.5;
  const quick = d.mode === "quick";
  const deltaA = Math.round((quick ? 24 : 32) * (sa - ea));
  const deltaB = Math.round((quick ? 8 : 32) * ((1 - sa) - (1 - ea)));
  const result = { winner, rating: { a: deltaA, b: deltaB }, coins: { a: 0, b: 0 } };
  const ops = [];
  if (ua) {
    ops.push(env.DB.prepare(
      "UPDATE users SET rating = MAX(100, rating + ?), duel_wins = duel_wins + ?, duel_losses = duel_losses + ? WHERE id = ?"
    ).bind(deltaA, winner === "a" ? 1 : 0, winner === "b" ? 1 : 0, ua.id));
  }
  if (ub) {
    ops.push(env.DB.prepare(
      "UPDATE users SET rating = MAX(100, rating + ?), duel_wins = duel_wins + ?, duel_losses = duel_losses + ? WHERE id = ?"
    ).bind(deltaB, winner === "b" ? 1 : 0, winner === "a" ? 1 : 0, ub.id));
  }
  if (ops.length) await env.DB.batch(ops);
  const coinsFor = (s) => (winner === s ? 20 : winner === "draw" ? 10 : 5);
  if (ua) result.coins.a = await rewardFor(env, ua.id, coinsFor("a"));
  if (ub && !quick) result.coins.b = await rewardFor(env, ub.id, coinsFor("b"));
  await env.DB.prepare("UPDATE duels SET result = ? WHERE id = ?").bind(JSON.stringify(result), d.id).run();
  return result;
}

// Lazily moves a duel forward (expiry, timeouts, round resolution). Safe to call from every poll.
async function advanceDuel(env, d) {
  const t = now();
  if (d.status === "pending" && t - d.created_at > DUEL_CHALLENGE_TTL) {
    await env.DB.prepare("UPDATE duels SET status = 'expired', updated_at = ? WHERE id = ? AND status = 'pending'")
      .bind(t, d.id)
      .run();
    return { ...d, status: "expired" };
  }
  if (d.status !== "active") return d;
  if (!(d.a_act && d.b_act) && t < d.deadline) return d;
  const A = JSON.parse(d.a_stats);
  const B = JSON.parse(d.b_stats);
  const aMiss = d.a_act ? 0 : d.a_miss + 1;
  const bMiss = d.b_act ? 0 : d.b_miss + 1;
  const res = resolveRound(d, A, B);
  let winner = res.winner;
  const events = res.events;
  if (!winner && (aMiss >= DUEL_MAX_MISSES || bMiss >= DUEL_MAX_MISSES)) {
    winner = aMiss >= DUEL_MAX_MISSES && bMiss >= DUEL_MAX_MISSES ? "draw" : aMiss >= DUEL_MAX_MISSES ? "b" : "a";
    events.push({ r: d.round, s: winner === "a" ? "b" : "a", act: "afk" });
  }
  const log = JSON.parse(d.log || "[]").concat(events);
  const done = !!winner;
  const upd = await env.DB.prepare(
    "UPDATE duels SET a_hp = ?, b_hp = ?, a_charge = ?, b_charge = ?, a_act = NULL, b_act = NULL, a_miss = ?, b_miss = ?, " +
      "round = round + ?, deadline = ?, log = ?, status = ?, winner = ?, updated_at = ? " +
      "WHERE id = ? AND round = ? AND status = 'active'"
  )
    .bind(res.a_hp, res.b_hp, res.a_charge, res.b_charge, aMiss, bMiss, done ? 0 : 1, t + DUEL_ROUND_S,
      JSON.stringify(log), done ? "done" : "active", winner, t, d.id, d.round)
    .run();
  const fresh = await env.DB.prepare("SELECT * FROM duels WHERE id = ?").bind(d.id).first();
  if (upd.meta && upd.meta.changes === 1 && done) {
    fresh.result = JSON.stringify(await finishDuel(env, fresh, winner));
  }
  return fresh;
}

function duelView(d, me) {
  const A = JSON.parse(d.a_stats || "{}");
  const B = JSON.parse(d.b_stats || "{}");
  const mine = me && d.a_id === me.id ? "a" : me && d.b_id === me.id ? "b" : null;
  return {
    id: d.id,
    mode: d.mode,
    status: d.status,
    round: d.round,
    deadline: d.deadline,
    now: now(),
    a: { ...A, hp_now: d.a_hp, charge: d.a_charge, acted: !!d.a_act },
    b: { ...B, hp_now: d.b_hp, charge: d.b_charge, acted: !!d.b_act },
    mine,
    log: JSON.parse(d.log || "[]"),
    winner: d.winner,
    result: parseObj(d.result),
    special_cost: DUEL_SPECIAL_COST,
    max_rounds: DUEL_MAX_ROUNDS,
  };
}

async function saveGear(env, me, gear) {
  if (gear === undefined) return me;
  const clean = JSON.stringify(cleanGear(gear, me));
  if (clean !== (me.gear || "{}")) {
    await env.DB.prepare("UPDATE users SET gear = ? WHERE id = ?").bind(clean, me.id).run();
    me.gear = clean;
  }
  return me;
}

async function activeDuelOf(env, userId) {
  return env.DB.prepare(
    "SELECT * FROM duels WHERE status = 'active' AND (a_id = ? OR b_id = ?) ORDER BY updated_at DESC LIMIT 1"
  )
    .bind(userId, userId)
    .first();
}

async function arenaState(env, me, url) {
  const t = now();
  if (url.searchParams.get("here") === "1") {
    await env.DB.prepare("UPDATE users SET arena_until = ? WHERE id = ?").bind(t + 60, me.id).run();
    me.arena_until = t + 60;
  }
  let active = await activeDuelOf(env, me.id);
  if (active) active = await advanceDuel(env, active);
  const [lobby, incoming, outgoing, live, recent, top] = await Promise.all([
    env.DB.prepare("SELECT * FROM users WHERE arena_until > ? AND id != ? ORDER BY rating DESC LIMIT 40").bind(t, me.id).all(),
    env.DB.prepare(
      "SELECT d.id, d.created_at, u.* FROM duels d JOIN users u ON u.id = d.a_id " +
        "WHERE d.b_id = ? AND d.status = 'pending' AND d.created_at > ? ORDER BY d.created_at DESC LIMIT 10"
    ).bind(me.id, t - DUEL_CHALLENGE_TTL).all(),
    env.DB.prepare(
      "SELECT d.id AS duel_id, d.created_at AS duel_at, u.code, u.name FROM duels d JOIN users u ON u.id = d.b_id " +
        "WHERE d.a_id = ? AND d.status = 'pending' AND d.created_at > ? ORDER BY d.created_at DESC LIMIT 10"
    ).bind(me.id, t - DUEL_CHALLENGE_TTL).all(),
    env.DB.prepare("SELECT * FROM duels WHERE status = 'active' AND updated_at > ? ORDER BY updated_at DESC LIMIT 10")
      .bind(t - 120)
      .all(),
    env.DB.prepare("SELECT * FROM duels WHERE status = 'done' ORDER BY updated_at DESC LIMIT 12").all(),
    env.DB.prepare(
      "SELECT * FROM users WHERE duel_wins + duel_losses > 0 AND role != 'bot' ORDER BY rating DESC, duel_wins DESC LIMIT 10"
    ).all(),
  ]);
  const brief = (d) => {
    const A = JSON.parse(d.a_stats || "{}");
    const B = JSON.parse(d.b_stats || "{}");
    return {
      id: d.id,
      mode: d.mode,
      round: d.round,
      winner: d.winner,
      at: d.updated_at,
      a: { name: A.name, code: A.code, species: A.species, color: A.color, hp: d.a_hp, max: A.hp },
      b: { name: B.name, code: B.code, species: B.species, color: B.color, hp: d.b_hp, max: B.hp },
    };
  };
  return json({
    ok: true,
    me: {
      rating: me.rating || 1000,
      wins: me.duel_wins || 0,
      losses: me.duel_losses || 0,
      duel_coins: me.duel_coins || 0,
      stats: fighterStats(me),
      active: active && active.status === "active" ? active.id : null,
    },
    lobby: lobby.results.map(arenaCard),
    incoming: incoming.results.map((r) => ({ id: r.id, at: r.created_at, from: arenaCard(r) })),
    outgoing: outgoing.results.map((r) => ({ id: r.duel_id, at: r.duel_at, to: { code: r.code, name: r.name } })),
    live: live.results.map(brief),
    recent: recent.results.map(brief),
    top: top.results.map(arenaCard),
    rules: { round_s: DUEL_ROUND_S, max_rounds: DUEL_MAX_ROUNDS, special_cost: DUEL_SPECIAL_COST, rewards_per_day: DUEL_REWARDS_PER_DAY },
  });
}

async function duelChallenge(request, env, me) {
  const body = await readJson(request);
  if (!body) return fail(400, "bad_json");
  await saveGear(env, me, body.gear);
  const target = await userByCode(env, body);
  if (!target) return fail(404, "not_found");
  if (target.id === me.id) return fail(400, "self");
  if (target.role === "bot") return fail(400, "bot");
  if (await activeDuelOf(env, me.id)) return fail(400, "busy");
  const t = now();
  const existing = await env.DB.prepare(
    "SELECT id FROM duels WHERE a_id = ? AND b_id = ? AND status = 'pending' AND created_at > ?"
  )
    .bind(me.id, target.id, t - DUEL_CHALLENGE_TTL)
    .first();
  if (existing) return json({ ok: true, id: existing.id, status: "pending" });
  const pending = await env.DB.prepare("SELECT COUNT(*) AS n FROM duels WHERE a_id = ? AND status = 'pending' AND created_at > ?")
    .bind(me.id, t - DUEL_CHALLENGE_TTL)
    .first();
  if (pending.n >= 5) return fail(429, "too_fast");
  const id = crypto.randomUUID().slice(0, 12);
  await env.DB.prepare("INSERT INTO duels (id, mode, status, a_id, b_id, created_at, updated_at) VALUES (?, 'live', 'pending', ?, ?, ?, ?)")
    .bind(id, me.id, target.id, t, t)
    .run();
  return json({ ok: true, id, status: "pending" });
}

async function duelRespond(request, env, me) {
  const body = await readJson(request);
  if (!body) return fail(400, "bad_json");
  await saveGear(env, me, body.gear);
  const id = cleanText(body.id, 16, "");
  let d = await env.DB.prepare("SELECT * FROM duels WHERE id = ?").bind(id).first();
  if (!d || d.b_id !== me.id) return fail(404, "not_found");
  d = await advanceDuel(env, d);
  if (d.status !== "pending") return fail(400, "expired");
  const t = now();
  if (!body.accept) {
    await env.DB.prepare("UPDATE duels SET status = 'declined', updated_at = ? WHERE id = ?").bind(t, id).run();
    return json({ ok: true, status: "declined" });
  }
  if (await activeDuelOf(env, me.id)) return fail(400, "busy");
  const challenger = await env.DB.prepare("SELECT * FROM users WHERE id = ?").bind(d.a_id).first();
  if (!challenger) return fail(404, "not_found");
  if (await activeDuelOf(env, challenger.id)) return fail(400, "busy");
  const A = fighterSnapshot(challenger);
  const B = fighterSnapshot(me);
  const upd = await env.DB.prepare(
    "UPDATE duels SET status = 'active', a_stats = ?, b_stats = ?, a_hp = ?, b_hp = ?, round = 1, deadline = ?, updated_at = ? " +
      "WHERE id = ? AND status = 'pending'"
  )
    .bind(JSON.stringify(A), JSON.stringify(B), A.hp, B.hp, t + DUEL_ROUND_S + 3, t, id)
    .run();
  if (!upd.meta || upd.meta.changes !== 1) return fail(400, "expired");
  return json({ ok: true, status: "active", id });
}

async function duelAct(request, env, me) {
  const body = await readJson(request);
  if (!body) return fail(400, "bad_json");
  const id = cleanText(body.id, 16, "");
  const action = DUEL_ACTIONS.has(body.action) ? body.action : "attack";
  let d = await env.DB.prepare("SELECT * FROM duels WHERE id = ?").bind(id).first();
  if (!d || (d.a_id !== me.id && d.b_id !== me.id)) return fail(404, "not_found");
  d = await advanceDuel(env, d);
  if (d.status !== "active") return json({ ok: true, duel: duelView(d, me) });
  if (cleanInt(body.round, 0, 999, d.round) !== d.round) return json({ ok: true, duel: duelView(d, me) });
  const col = d.a_id === me.id ? "a_act" : "b_act";
  await env.DB.prepare(`UPDATE duels SET ${col} = ? WHERE id = ? AND round = ? AND ${col} IS NULL`).bind(action, id, d.round).run();
  d = await env.DB.prepare("SELECT * FROM duels WHERE id = ?").bind(id).first();
  d = await advanceDuel(env, d);
  return json({ ok: true, duel: duelView(d, me) });
}

async function duelGet(env, me, url) {
  const id = cleanText(url.searchParams.get("id"), 16, "");
  let d = await env.DB.prepare("SELECT * FROM duels WHERE id = ?").bind(id).first();
  if (!d) return fail(404, "not_found");
  d = await advanceDuel(env, d);
  return json({ ok: true, duel: duelView(d, me) });
}

async function duelForfeit(request, env, me) {
  const body = await readJson(request);
  const id = cleanText(body && body.id, 16, "");
  const d = await env.DB.prepare("SELECT * FROM duels WHERE id = ?").bind(id).first();
  if (!d || (d.a_id !== me.id && d.b_id !== me.id)) return fail(404, "not_found");
  if (d.status === "pending") {
    await env.DB.prepare("UPDATE duels SET status = 'declined', updated_at = ? WHERE id = ?").bind(now(), id).run();
    return json({ ok: true });
  }
  if (d.status !== "active") return json({ ok: true });
  const winner = d.a_id === me.id ? "b" : "a";
  const log = JSON.parse(d.log || "[]");
  log.push({ r: d.round, s: winner === "a" ? "b" : "a", act: "forfeit" });
  const upd = await env.DB.prepare(
    "UPDATE duels SET status = 'done', winner = ?, log = ?, updated_at = ? WHERE id = ? AND status = 'active'"
  )
    .bind(winner, JSON.stringify(log), now(), id)
    .run();
  if (upd.meta && upd.meta.changes === 1) await finishDuel(env, d, winner);
  return json({ ok: true });
}

async function duelQuick(request, env, me) {
  const body = await readJson(request);
  if (!body) return fail(400, "bad_json");
  await saveGear(env, me, body.gear);
  const target = await userByCode(env, body);
  if (!target) return fail(404, "not_found");
  if (target.id === me.id) return fail(400, "self");
  const t = now();
  const recent = await env.DB.prepare("SELECT COUNT(*) AS n FROM duels WHERE a_id = ? AND mode = 'quick' AND created_at > ?")
    .bind(me.id, t - 3600)
    .first();
  if (recent.n >= 30) return fail(429, "too_fast");
  const A = fighterSnapshot(me);
  const B = fighterSnapshot(target);
  const d = { round: 1, a_hp: A.hp, b_hp: B.hp, a_charge: 0, b_charge: 0 };
  let log = [];
  let winner = null;
  while (!winner) {
    d.a_act = aiAction(d.a_hp, A.hp, d.a_charge);
    d.b_act = aiAction(d.b_hp, B.hp, d.b_charge);
    const res = resolveRound(d, A, B);
    log = log.concat(res.events);
    Object.assign(d, { a_hp: res.a_hp, b_hp: res.b_hp, a_charge: res.a_charge, b_charge: res.b_charge });
    winner = res.winner;
    d.round += 1;
  }
  const id = crypto.randomUUID().slice(0, 12);
  const row = {
    id, mode: "quick", status: "done", a_id: me.id, b_id: target.id,
    a_stats: JSON.stringify(A), b_stats: JSON.stringify(B), a_hp: d.a_hp, b_hp: d.b_hp,
    a_charge: d.a_charge, b_charge: d.b_charge, round: d.round - 1, deadline: 0,
    log: JSON.stringify(log), winner, created_at: t, updated_at: t, result: "{}",
  };
  await env.DB.prepare(
    "INSERT INTO duels (id, mode, status, a_id, b_id, a_stats, b_stats, a_hp, b_hp, a_charge, b_charge, round, deadline, log, winner, created_at, updated_at) " +
      "VALUES (?, 'quick', 'done', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)"
  )
    .bind(id, me.id, target.id, row.a_stats, row.b_stats, row.a_hp, row.b_hp, row.a_charge, row.b_charge, row.round, row.log, winner, t, t)
    .run();
  row.result = JSON.stringify(await finishDuel(env, row, winner));
  return json({ ok: true, duel: duelView(row, me) });
}

// ---------------------------------------------------------------- v8: location & live weather
// Only the city / country are ever shown to others; coordinates stay on the server (rounded to ~1 km).
const WEATHER_TTL_S = 15 * 60;

function hasLocation(row) {
  return !!(row && row.city && row.lat !== null && row.lat !== undefined && row.loc_visible !== 0);
}

function weatherKey(lat, lon) {
  return `${Number(lat).toFixed(1)},${Number(lon).toFixed(1)}`;
}

async function fetchWeather(lat, lon) {
  const url =
    "https://api.open-meteo.com/v1/forecast?latitude=" + Number(lat).toFixed(2) + "&longitude=" + Number(lon).toFixed(2) +
    "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,is_day,wind_speed_10m&timezone=auto";
  const resp = await fetch(url, { headers: { "user-agent": "Pips-Friends/" + VERSION } });
  if (!resp.ok) throw new Error("weather " + resp.status);
  const data = await resp.json();
  const c = data.current || {};
  return {
    temp: Math.round((c.temperature_2m ?? 0) * 10) / 10,
    feels: Math.round((c.apparent_temperature ?? c.temperature_2m ?? 0) * 10) / 10,
    humidity: c.relative_humidity_2m ?? null,
    code: c.weather_code ?? 0,
    is_day: c.is_day ?? 1,
    wind: c.wind_speed_10m ?? null,
    utc_offset: data.utc_offset_seconds ?? 0,
    at: now(),
  };
}

// Adds {loc, weather} to profile objects in place. rows[i] matches objs[i].
async function attachWeather(env, rows, objs) {
  const keys = new Set();
  rows.forEach((row) => {
    if (hasLocation(row)) keys.add(weatherKey(row.lat, row.lon));
  });
  const cache = {};
  if (keys.size) {
    const list = [...keys].slice(0, 60);
    const placeholders = list.map(() => "?").join(",");
    const cached = await env.DB.prepare(`SELECT key, data, fetched_at FROM weather_cache WHERE key IN (${placeholders})`)
      .bind(...list)
      .all();
    for (const r of cached.results) cache[r.key] = { data: parseObj(r.data), at: r.fetched_at };
    const stale = list.filter((k) => !cache[k] || now() - cache[k].at > WEATHER_TTL_S).slice(0, 12);
    await Promise.all(
      stale.map(async (k) => {
        const [lat, lon] = k.split(",").map(Number);
        try {
          const w = await fetchWeather(lat, lon);
          cache[k] = { data: w, at: now() };
          await env.DB.prepare("INSERT OR REPLACE INTO weather_cache (key, data, fetched_at) VALUES (?, ?, ?)")
            .bind(k, JSON.stringify(w), now())
            .run();
        } catch (err) {
          console.error(err);
        }
      })
    );
  }
  rows.forEach((row, i) => {
    if (!hasLocation(row)) return;
    objs[i].loc = { city: row.city, country: row.country || "" };
    const hit = cache[weatherKey(row.lat, row.lon)];
    if (hit && hit.data && hit.data.at) objs[i].weather = hit.data;
  });
  return objs;
}

async function setLocation(request, env, me) {
  const body = await readJson(request);
  if (!body) return fail(400, "bad_json");
  const mode = body.mode === "auto" || body.mode === "city" || body.mode === "off" || body.mode === "hide" || body.mode === "show"
    ? body.mode : "";
  if (!mode) return fail(400, "bad_mode");
  if (mode === "off") {
    await env.DB.prepare("UPDATE users SET city = '', country = '', lat = NULL, lon = NULL WHERE id = ?").bind(me.id).run();
    return json({ ok: true, loc: null });
  }
  if (mode === "hide" || mode === "show") {
    await env.DB.prepare("UPDATE users SET loc_visible = ? WHERE id = ?").bind(mode === "show" ? 1 : 0, me.id).run();
    return json({ ok: true, visible: mode === "show" });
  }
  let city;
  let country;
  let lat;
  let lon;
  if (mode === "auto") {
    const cf = request.cf || {};
    city = cleanText(cf.city || "", 40, "");
    country = cleanText(cf.country || "", 40, "");
    lat = Number.parseFloat(cf.latitude);
    lon = Number.parseFloat(cf.longitude);
    if (!city || !Number.isFinite(lat) || !Number.isFinite(lon)) return fail(404, "no_location");
  } else {
    city = cleanText(body.city, 40, "");
    country = cleanText(body.country, 40, "");
    lat = Number.parseFloat(body.lat);
    lon = Number.parseFloat(body.lon);
    if (!city || !Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
      return fail(400, "bad_location");
    }
  }
  lat = Math.round(lat * 100) / 100;
  lon = Math.round(lon * 100) / 100;
  await env.DB.prepare("UPDATE users SET city = ?, country = ?, lat = ?, lon = ?, loc_visible = 1 WHERE id = ?")
    .bind(city, country, lat, lon, me.id)
    .run();
  const row = { city, country, lat, lon, loc_visible: 1 };
  const [obj] = await attachWeather(env, [row], [{}]);
  return json({ ok: true, loc: obj.loc, weather: obj.weather || null });
}

async function geoSearch(env, url) {
  const q = cleanText(url.searchParams.get("q"), 60, "");
  if (q.length < 2) return json({ ok: true, places: [] });
  const lang = /^[a-z]{2}$/.test(url.searchParams.get("lang") || "") ? url.searchParams.get("lang") : "en";
  const resp = await fetch(
    "https://geocoding-api.open-meteo.com/v1/search?count=8&format=json&language=" + lang + "&name=" + encodeURIComponent(q),
    { headers: { "user-agent": "Pips-Friends/" + VERSION } }
  );
  if (!resp.ok) return fail(502, "geo_failed");
  const data = await resp.json();
  const places = (data.results || []).map((p) => ({
    name: p.name,
    admin: p.admin1 || "",
    country: p.country || "",
    lat: p.latitude,
    lon: p.longitude,
  }));
  return json({ ok: true, places });
}

// ---------------------------------------------------------------- v9: chat bots
// Bots are real (login-less) users with role 'bot'. They answer in private bot chats (/v1/bot/ask)
// and when mentioned in group chats ("@weather Batam"). Workers AI writes the replies; every bot
// also has built-in commands / fallbacks so it still works without AI.
const AI_MODELS = ["@cf/meta/llama-3.1-8b-instruct", "@cf/meta/llama-3.2-3b-instruct"];
const BOT_DAILY_LIMIT = 120;
const BOTS = {
  helper: { id: "bot-helper", code: "BOTPIP", name: "Pip Helper", species: "owl", color: "#9b7bd4", level: 10,
            gear: {}, role: "the friendly guide who explains how every Pips feature works" },
  coach: { id: "bot-coach", code: "BOTFCS", name: "Coach Focus", species: "dog", color: "#f08c3c", level: 20,
           gear: {}, role: "an upbeat productivity and focus coach who knows the user's Pomodoro stats" },
  weather: { id: "bot-weather", code: "BOTWTR", name: "Sunny", species: "penguin", color: "#5aa7e6", level: 12,
             gear: {}, role: "a cheerful weather reporter with live weather data" },
  arena: { id: "bot-arena", code: "BOTARN", name: "Arena Master", species: "dragon", color: "#d6453d", level: 30,
           gear: { weapon: "laser_blaster", armor: "knight_armor", tool: "medkit" },
           role: "the dramatic host of the Pips Arena who gives battle and gear advice" },
  fun: { id: "bot-fun", code: "BOTFUN", name: "Giggles", species: "bunny", color: "#f783ac", level: 8, gear: {},
         role: "a playful entertainer who tells clean jokes, riddles, fun facts and plays mini games" },
  guard: { id: "bot-guard", code: "BOTGRD", name: "Guardian", species: "bear", color: "#3b82c4", level: 25, gear: {},
           role: "the calm community guardian who explains the community rules and how to report problems" },
};
const BOT_IDS = new Set(Object.values(BOTS).map((b) => b.id));
const LANG_NAMES = { en: "English", id: "Bahasa Indonesia", zh_TW: "Traditional Chinese", zh_CN: "Simplified Chinese" };
const PIPS_GUIDE =
  "Pips is a cute desktop pet + Pomodoro focus timer for Windows made by Juwen Lie (owner, 73946469+juw12@users.noreply.github.com). " +
  "Click the pet to open the focus card (25-minute pomodoro). Drag the pet anywhere; rub the mouse over it to pet it. " +
  "Right-click the pet: snacks, 23 animals, colours, language, settings, dark mode, Help (guide, check for updates, about). " +
  "Everyone has a 6-character friend code (letters + numbers) shown at the top right of their hub with a Copy button; to add a " +
  "friend, type their code in the box at the top of the Friends tab and press Add friend (private profiles get a friend request). " +
  "You can also add people from the Ranks tab or their profile. " +
  "The hub (right-click > Friends) has tabs: Friends (friend list, friend requests, affinity hearts, " +
  "wave/pet/snack/high-five/visit), Chat (private chats, group chats and bots), Groups (invite-only group chats), " +
  "Arena (live duels: each 12-second round pick Attack, Guard or Special; quick battles vs anyone; rating and coins), " +
  "Ranks (everyone by XP, badges or streak), Shop (spend coins on props, moves and arena gear; 51 animals to pick from), Birthdays (set yours in Profile; on a friend's birthday join their party room, write a wish and send a gift from the Shop), Pokes (poke, tickle, tease, boop or hug a friend from a distance: Friends tab Poke button, the chat, or right-click your pet > Poke a friend; it pops up on their screen and their pet reacts, they can poke back; turn it off in Profile), Badges (22 achievements), " +
  "Profile (photo, bio, info, online status, public/private, location & live weather banner, light/dark theme, Premium, About). " +
  "Coins: +5 per pomodoro, +40 per level, +25 per badge, +10 per day used, +15 per friendship level, +2 per interaction " +
  "(max 20/day), arena win +20 (first 6 fights a day). Premium (purple badge): exclusive props, Fireworks move, +30 coins a " +
  "day, plasma cannon, mech suit, force field - buy via Profile > Premium > Ask to buy, then redeem the PIPS-XXXX-XXXX key. " +
  "Moderators handle reports (Report button on profiles). Updates arrive automatically - the pet offers them.";
const COMMUNITY_RULES =
  "1) Be kind and respectful - no insults, harassment or hate. 2) Keep it workplace-friendly - no adult or violent content. " +
  "3) No spam or ads. 4) Don't share other people's private information. 5) Don't cheat or exploit bugs - report them to the owner. " +
  "To report someone: open their profile and press Report; moderators review it and can warn, mute (1h/24h/7d), clear a " +
  "profile or reset a name. Contact the owner: 73946469+juw12@users.noreply.github.com.";
const JOKES = [
  "Why did the computer go to the doctor? It had a virus! 🤒",
  "Why don't programmers like nature? Too many bugs. 🐛",
  "What do you call a fake noodle? An impasta! 🍝",
  "Why did the scarecrow win an award? He was outstanding in his field. 🌾",
  "I told my pet a joke about time management... it didn't have a minute to laugh. ⏱",
  "Why was the math book sad? It had too many problems. 📚",
];
const EIGHT_BALL = ["Yes, definitely! ✨", "It is certain.", "Ask again after your next pomodoro 🍅", "Don't count on it.",
  "Signs point to yes.", "Very doubtful.", "Better not tell you now 🤫", "Outlook good!"];

let botsReady = false;
async function ensureBots(env) {
  if (botsReady) return;
  const t = now();
  const ops = Object.values(BOTS).map((b) =>
    env.DB.prepare(
      "INSERT INTO users (id, secret_hash, code, name, species, color, level, xp, role, gear, visibility, bio, created_at, updated_at) " +
        "VALUES (?, 'bot', ?, ?, ?, ?, ?, ?, 'bot', ?, 'public', ?, ?, ?) " +
        "ON CONFLICT(id) DO UPDATE SET name = excluded.name, species = excluded.species, color = excluded.color, " +
        "level = excluded.level, role = 'bot', gear = excluded.gear, bio = excluded.bio"
    ).bind(b.id, b.code, b.name, b.species, b.color, b.level, b.level * 300, JSON.stringify(b.gear), "I'm " + b.role + ".", t, t)
  );
  await env.DB.batch(ops);
  botsReady = true;
}

async function aiReply(env, system, history, text) {
  if (!env.AI) return null;
  const messages = [{ role: "system", content: system }];
  for (const h of history.slice(-8)) {
    const content = cleanText(h.text, 500, "");
    if (content) messages.push({ role: h.mine ? "user" : "assistant", content });
  }
  messages.push({ role: "user", content: text });
  for (const model of AI_MODELS) {
    try {
      const out = await env.AI.run(model, { messages, max_tokens: 260, temperature: 0.7 });
      const reply = ((out && (out.response || (out.result && out.result.response))) || "").trim();
      if (reply) return reply.slice(0, 900);
    } catch (err) {
      console.error("ai", model, String(err));
    }
  }
  return null;
}

async function geocodeOne(name) {
  const resp = await fetch(
    "https://geocoding-api.open-meteo.com/v1/search?count=1&format=json&language=en&name=" + encodeURIComponent(name),
    { headers: { "user-agent": "Pips-Friends/" + VERSION } }
  );
  if (!resp.ok) return null;
  const data = await resp.json();
  const p = (data.results || [])[0];
  return p ? { city: p.name, country: p.country || "", lat: p.latitude, lon: p.longitude } : null;
}

const WX_WORDS = /\b(weather|cuaca|forecast|prakiraan|temperature|temp|suhu|hujan|rain|raining|rainy|sunny|snow|storm|windy|wind|humid|humidity|hot|cold|warm|panas|dingin|in|di|at|for|of|the|a|what'?s|what|how'?s|how|is|are|it|will|would|does|do|did|be|going|gonna|to|should|i|we|bring|need|an|umbrella|payung|jacket|now|today|tonight|tomorrow|besok|nanti|malam|sekarang|hari|ini|apakah|akan|bagaimana|gimana|like|right|there|currently|please|tell|me|about|check|cek|lagi|ya|kah|sunny|hey|hi|pips)\b|@\w+|[?!.,:;'"]|天氣|天气|今天|明天|現在|现在|怎麼樣|怎么样|會下雨嗎|会下雨吗|下雨|嗎|吗/gi;
const WX_STOP = new Set(("weather cuaca forecast temperature rain today tomorrow tonight now will it is the what how " +
  "does do in di at for hujan suhu sunny hey hi pips umbrella jacket").split(" "));

function weatherPlaceCandidates(text) {
  const raw = String(text || "").replace(/@\w+/g, " ");
  const out = [];
  const add = (s) => {
    s = String(s || "").replace(/[?!.,:;'"]/g, " ").replace(/\s+/g, " ").trim();
    if (s.length >= 2 && s.split(" ").length <= 4 && !WX_STOP.has(s.toLowerCase()) && !out.includes(s)) out.push(s);
  };
  // "in Taipei", "di Batam", "for New York", "at Jakarta"
  const prep = raw.match(/\b(?:in|di|at|for|of)\s+([A-Za-z\u00C0-\u024F][\w\u00C0-\u024F'\-]*(?:\s+[A-Z\u00C0-\u024F][\w\u00C0-\u024F'\-]*){0,2})/);
  if (prep) {
    add(prep[1].replace(WX_WORDS, " "));
    add(prep[1].split(/\s+/)[0]);
  }
  // Everything left after removing weather / filler words
  add(raw.replace(WX_WORDS, " "));
  // Capitalised word runs (skip the very first word of the sentence if it's a common verb)
  const caps = raw.match(/\b[A-Z\u00C0-\u024F][\w\u00C0-\u024F'\-]*(?:\s+[A-Z\u00C0-\u024F][\w\u00C0-\u024F'\-]*)*/g) || [];
  for (const c of caps) add(c.replace(WX_WORDS, " "));
  // Chinese place names left after removing weather words
  const cjk = raw.replace(WX_WORDS, " ").match(/[\u4e00-\u9fff]{2,}/g) || [];
  for (const c of cjk) add(c);
  return out.slice(0, 3);
}

const WX_TEXT = { 0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog", 51: "light drizzle",
  53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 80: "rain showers", 81: "rain showers",
  82: "violent rain showers", 71: "light snow", 73: "snow", 75: "heavy snow", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail" };

async function weatherFacts(env, me, text) {
  let place = null;
  for (const cand of weatherPlaceCandidates(text)) {
    try {
      place = await geocodeOne(cand);
    } catch (e) {
      place = null;
    }
    if (place) break;
  }
  if (!place && me.city && me.lat !== null && me.lat !== undefined) {
    place = { city: me.city, country: me.country || "", lat: me.lat, lon: me.lon };
  }
  if (!place) return null;
  const objs = [{}];
  await attachWeather(env, [{ ...place, loc_visible: 1 }], objs);
  const w = objs[0].weather;
  if (!w) return null;
  const local = new Date(Date.now() + (w.utc_offset || 0) * 1000).toISOString().slice(11, 16);
  let today = "";
  try {
    const resp = await fetch(
      `https://api.open-meteo.com/v1/forecast?latitude=${place.lat}&longitude=${place.lon}` +
      "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code&forecast_days=2&timezone=auto",
      { headers: { "user-agent": "Pips-Friends/" + VERSION } }
    );
    if (resp.ok) {
      const d = (await resp.json()).daily || {};
      const day = (i, label) => d.time && d.time[i] !== undefined
        ? ` ${label}: ${Math.round(d.temperature_2m_min[i])}-${Math.round(d.temperature_2m_max[i])}°C, ` +
          `${WX_TEXT[d.weather_code[i]] || "mixed"}, chance of rain ${d.precipitation_probability_max[i] ?? "?"}%.`
        : "";
      today = day(0, "Today") + day(1, "Tomorrow");
    }
  } catch (e) {
    today = "";
  }
  return `Live weather in ${place.city}${place.country ? ", " + place.country : ""}: ${Math.round(w.temp)}°C ` +
    `(feels ${Math.round(w.feels)}°C), ${WX_TEXT[w.code] || "code " + w.code}, humidity ${w.humidity}%, wind ${Math.round(w.wind || 0)} km/h, ` +
    `${w.is_day ? "daytime" : "night"}, local time ${local}.` + today;
}

async function botAnswer(env, me, botKey, text, history, lang) {
  const bot = BOTS[botKey];
  const low = text.toLowerCase().trim();
  let context = "";
  let fallback = "";
  if (botKey === "helper") {
    context = "App facts (only use these, don't invent features): " + PIPS_GUIDE +
      ` The user is ${me.name}, level ${me.level}${isStaff(me) ? ", role " + me.role : ""}${isPremium(me) ? ", Premium" : ""}.`;
    fallback = "I can explain anything in Pips: the focus card, friends, chat, groups, the Arena, the Shop, badges, " +
      "Premium, location & weather or updates. What would you like to know? 🦉";
  } else if (botKey === "coach") {
    const today = new Date().toISOString().slice(0, 10);
    const fresh = me.day === today;
    context = `User stats: name ${me.name}, level ${me.level}, ${me.xp} XP, daily streak ${me.streak} days, ` +
      `today ${fresh ? me.today_poms : 0} pomodoros (${fresh ? me.today_minutes : 0} focus minutes), ` +
      `this week ${me.week_poms} pomodoros (${me.week_minutes} minutes). One pomodoro = 25 minutes focus + 5 minutes break. ` +
      "Give concrete, encouraging focus/break/ergonomics tips; suggest starting a pomodoro from the pet's focus card.";
    fallback = `You've done ${fresh ? me.today_poms : 0} pomodoros today and your streak is ${me.streak} days. ` +
      "Try one more 25-minute focus block - close distractions, then take a real 5-minute break. You've got this! 💪";
  } else if (botKey === "weather") {
    const facts = await weatherFacts(env, me, text);
    if (facts) {
      context = "Real data (use exactly these numbers): " + facts + " Add one practical tip (umbrella, water, jacket...).";
      fallback = "☀ " + facts;
    } else {
      context = "No location known. Ask the user which city they mean, or tell them to set their city in Profile > Location & weather.";
      fallback = "Which city? Try \"weather Batam\" - or set your city in Profile → Location & weather 📍";
    }
  } else if (botKey === "arena") {
    const stats = fighterStats(me);
    const top = await env.DB.prepare(
      "SELECT name, rating, duel_wins, duel_losses FROM users WHERE role != 'bot' AND duel_wins + duel_losses > 0 ORDER BY rating DESC LIMIT 5"
    ).all();
    const topText = top.results.map((r, i) => `${i + 1}. ${r.name} (${r.rating}, ${r.duel_wins}W/${r.duel_losses}L)`).join("; ") || "nobody yet";
    const gearText = Object.entries(GEAR).map(([k, g]) =>
      `${k.replace(/_/g, " ")} (${g.slot}${g.atk ? " atk+" + g.atk : ""}${g.def ? " def+" + g.def : ""}${g.hp ? " hp+" + g.hp : ""}${g.special ? " special " + g.special : ""}${g.premium ? ", Premium" : ""})`
    ).join(", ");
    context = `User fighter: rating ${me.rating || 1000}, ${me.duel_wins || 0} wins / ${me.duel_losses || 0} losses, power ${stats.power}, ` +
      `HP ${stats.hp}, ATK ${stats.atk}, DEF ${stats.def}, crit ${stats.crit}%, special ${stats.special}, gear ${JSON.stringify(stats.gear)}. ` +
      `Top fighters: ${topText}. Gear in the shop: ${gearText}. Combat: Guard blocks 65% and charges special twice as fast; ` +
      "Power strike and Water bomb break guard; specials need 3 charge. The user can Quick-battle you (Arena Master) from your profile.";
    if (/^(top|rank|ranking|peringkat|排行)/.test(low)) return "🏆 " + topText;
    fallback = `⚔ Your power is ${stats.power} (rating ${me.rating || 1000}). Top fighters: ${topText}. ` +
      "Upgrade your weapon first for the biggest boost - and use Guard to charge your special!";
  } else if (botKey === "fun") {
    if (/^(roll|dice|dadu|骰)/.test(low)) return `🎲 You rolled a ${1 + Math.floor(Math.random() * 6)}!`;
    if (/^(flip|coin|koin|硬幣|硬币)/.test(low)) return Math.random() < 0.5 ? "🪙 Heads!" : "🪙 Tails!";
    if (/^(8ball|8-ball|magic)/.test(low)) return "🎱 " + EIGHT_BALL[Math.floor(Math.random() * EIGHT_BALL.length)];
    context = "Tell clean, short, workplace-friendly jokes, riddles (give the answer if asked), fun facts and play word games. " +
      "Commands the user can type: roll, flip, 8ball.";
    fallback = JOKES[Math.floor(Math.random() * JOKES.length)];
  } else if (botKey === "guard") {
    let staffInfo = "";
    if (isStaff(me)) {
      const open = await env.DB.prepare("SELECT COUNT(*) AS n FROM reports WHERE status = 'open'").first();
      staffInfo = ` The user is a ${me.role}; there are ${open.n} open reports in the Mod tab.`;
    }
    context = "Community rules: " + COMMUNITY_RULES + staffInfo + " If someone is being harmed or harassed, advise reporting and contacting the owner.";
    if (/^(rules|aturan|規則|规则)/.test(low)) return "🛡 " + COMMUNITY_RULES;
    fallback = "🛡 " + COMMUNITY_RULES;
  }
  const system =
    `You are ${bot.name}, a bot character inside the Pips desktop-pet app. Your role: ${bot.role}. ${context} ` +
    `Rules: be warm and fun, keep replies short (under 70 words), workplace-appropriate, no medical/legal/financial advice, ` +
    `never claim features that aren't listed. Reply in ${LANG_NAMES[lang] || "the user's language"}. Plain text, max 2 emojis.`;
  const reply = await aiReply(env, system, history, text);
  return reply || fallback;
}

async function botQuota(env, me) {
  const day = new Date().toISOString().slice(0, 10);
  const row = await env.DB.prepare("SELECT n FROM bot_usage WHERE user_id = ? AND day = ?").bind(me.id, day).first();
  if (row && row.n >= BOT_DAILY_LIMIT) return false;
  await env.DB.prepare(
    "INSERT INTO bot_usage (user_id, day, n) VALUES (?, ?, 1) ON CONFLICT(user_id, day) DO UPDATE SET n = n + 1"
  ).bind(me.id, day).run();
  return true;
}

async function botAsk(request, env, me) {
  const body = await readJson(request, 32000);
  if (!body) return fail(400, "bad_json");
  const botKey = typeof body.bot === "string" && BOTS[body.bot] ? body.bot : null;
  if (!botKey) return fail(404, "not_found");
  const text = cleanText(body.text, 400, "");
  if (!text) return fail(400, "empty");
  if (!(await botQuota(env, me))) return fail(429, "bot_limit");
  await ensureBots(env);
  const history = Array.isArray(body.history) ? body.history.slice(-8) : [];
  const lang = LANG_NAMES[body.lang] ? body.lang : "en";
  const reply = await botAnswer(env, me, botKey, text, history, lang);
  return json({ ok: true, bot: botKey, reply });
}

const BOT_HANDLES = { helper: "helper", help: "helper", pip: "helper", coach: "coach", focus: "coach", weather: "weather",
  sunny: "weather", cuaca: "weather", arena: "arena", fun: "fun", giggles: "fun", joke: "fun", guard: "guard", guardian: "guard", mod: "guard" };

// "@weather Batam" in a group chat -> the bot answers in the room for everyone.
async function botInRoom(env, me, room, text, lang) {
  const m = text.match(/^@([a-z]+)\b[\s,:]*([\s\S]*)$/i);
  if (!m) return;
  const botKey = BOT_HANDLES[m[1].toLowerCase()];
  if (!botKey) return;
  if (!(await botQuota(env, me))) return;
  await ensureBots(env);
  const recent = await env.DB.prepare("SELECT sender, body FROM messages WHERE room = ? AND kind = 'text' ORDER BY id DESC LIMIT 7")
    .bind(room)
    .all();
  const history = recent.results.reverse().slice(0, -1).map((r) => ({ mine: !BOT_IDS.has(r.sender), text: r.body }));
  const question = m[2].trim() || "hello";
  const reply = await botAnswer(env, me, botKey, question, history, lang);
  await env.DB.prepare("INSERT INTO messages (room, sender, kind, body, created_at) VALUES (?, ?, 'text', ?, ?)")
    .bind(room, BOTS[botKey].id, reply.slice(0, 500), now())
    .run();
}

// ---------------------------------------------------------------- v10: birthdays, parties & gifts
const GIFT_DAILY_LIMIT = 20;
const WISHES_PER_GUEST = 5;

function daysInMonth(m) {
  return [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1] || 31;
}

function bdayOf(row) {
  return row && row.bday_m && row.bday_d && row.bday_visible !== 0 ? { m: row.bday_m, d: row.bday_d } : null;
}

function isLeap(y) {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

// Returns the party year when `dateStr` (the viewer's local YYYY-MM-DD) is the host's birthday (+-1 day for time zones).
function partyYear(host, dateStr) {
  const mt = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr || "");
  if (!mt || !host.bday_m || !host.bday_d) return 0;
  const base = Date.UTC(+mt[1], +mt[2] - 1, +mt[3]);
  for (const off of [0, -1, 1]) {
    const dt = new Date(base + off * 86400000);
    const y = dt.getUTCFullYear();
    let m = host.bday_m;
    let d = host.bday_d;
    if (m === 2 && d === 29 && !isLeap(y)) d = 28;
    if (dt.getUTCMonth() + 1 === m && dt.getUTCDate() === d) return y;
  }
  return 0;
}

async function setBirthday(request, env, me) {
  const body = await readJson(request);
  if (body === null) return fail(400, "bad_json");
  const m = cleanInt(body.m, 0, 12, 0);
  const d = m ? cleanInt(body.d, 1, daysInMonth(m), 1) : 0;
  const visible = body.visible === false ? 0 : 1;
  await env.DB.prepare("UPDATE users SET bday_m = ?, bday_d = ?, bday_visible = ?, updated_at = ? WHERE id = ?")
    .bind(m, d, visible, now(), me.id)
    .run();
  return json({ ok: true, bday_m: m, bday_d: d, bday_visible: visible === 1 });
}

async function pendingGifts(env, me) {
  const rows = await env.DB.prepare(
    "SELECT g.id, g.item, g.coins, g.message, g.created_at, u.code, u.name, u.species, u.color " +
      "FROM gifts g JOIN users u ON u.id = g.from_id WHERE g.to_id = ? AND g.claimed_at = 0 ORDER BY g.id LIMIT 30"
  )
    .bind(me.id)
    .all();
  return rows.results.map((r) => ({
    id: r.id,
    item: r.item || "",
    coins: r.coins || 0,
    message: r.message || "",
    at: r.created_at,
    from: { code: r.code, name: r.name, species: r.species, color: r.color },
  }));
}

async function sendGift(request, env, me) {
  const body = await readJson(request);
  if (body === null) return fail(400, "bad_json");
  const code = cleanText(body.to, 8, "").toUpperCase();
  const item = typeof body.item === "string" && /^[a-z_]{1,24}$/.test(body.item) ? body.item : "";
  const coins = cleanInt(body.coins, 0, 2000, 0);
  if (!item && !coins) return fail(400, "empty");
  const message = isMuted(me) ? "" : cleanText(body.message, 140, "");
  const other = await env.DB.prepare("SELECT * FROM users WHERE code = ?").bind(code).first();
  if (!other) return fail(404, "not_found");
  if (other.id === me.id) return fail(400, "self");
  if (other.role === "bot") return fail(400, "bot");
  if (!(await areFriends(env, me.id, other.id))) return fail(403, "not_friends");
  const sent = await env.DB.prepare("SELECT COUNT(*) AS n FROM gifts WHERE from_id = ? AND created_at > ?")
    .bind(me.id, now() - 86400)
    .first();
  if (sent.n >= GIFT_DAILY_LIMIT) return fail(429, "gift_limit");
  await env.DB.prepare("INSERT INTO gifts (from_id, to_id, item, coins, message, created_at) VALUES (?, ?, ?, ?, ?, ?)")
    .bind(me.id, other.id, item, coins, message, now())
    .run();
  return json({ ok: true });
}

async function claimGifts(request, env, me) {
  const body = await readJson(request);
  const ids = (Array.isArray(body && body.ids) ? body.ids : [])
    .map((v) => parseInt(v, 10))
    .filter((v) => Number.isFinite(v) && v > 0)
    .slice(0, 50);
  if (!ids.length) return json({ ok: true, claimed: 0 });
  const marks = ids.map(() => "?").join(",");
  await env.DB.prepare(`UPDATE gifts SET claimed_at = ? WHERE to_id = ? AND claimed_at = 0 AND id IN (${marks})`)
    .bind(now(), me.id, ...ids)
    .run();
  return json({ ok: true, claimed: ids.length });
}

async function partyHost(env, me, code) {
  const host = code === me.code ? me : await env.DB.prepare("SELECT * FROM users WHERE code = ?").bind(code).first();
  if (!host) return { error: fail(404, "not_found") };
  if (host.id !== me.id && !(await areFriends(env, me.id, host.id))) return { error: fail(403, "not_friends") };
  return { host };
}

async function partyGet(env, me, url) {
  const code = cleanText(url.searchParams.get("code"), 8, "").toUpperCase();
  const { host, error } = await partyHost(env, me, code);
  if (error) return error;
  const year = partyYear(host, url.searchParams.get("date"));
  if (!year) return fail(400, "not_birthday");
  const t = now();
  if (host.id !== me.id) {
    await env.DB.prepare("INSERT OR REPLACE INTO party_guests (host_id, year, user_id, seen_at) VALUES (?, ?, ?, ?)")
      .bind(host.id, year, me.id, t)
      .run();
  }
  const guests = await env.DB.prepare(
    "SELECT u.*, g.seen_at FROM party_guests g JOIN users u ON u.id = g.user_id " +
      "WHERE g.host_id = ? AND g.year = ? ORDER BY g.seen_at DESC LIMIT 40"
  )
    .bind(host.id, year)
    .all();
  const wishes = await env.DB.prepare(
    "SELECT w.id, w.text, w.created_at, u.code, u.name, u.species, u.color FROM party_wishes w " +
      "JOIN users u ON u.id = w.from_id WHERE w.host_id = ? AND w.year = ? ORDER BY w.id DESC LIMIT 100"
  )
    .bind(host.id, year)
    .all();
  const gifts = await env.DB.prepare("SELECT COUNT(*) AS n FROM gifts WHERE to_id = ? AND created_at > ?")
    .bind(host.id, t - 3 * 86400)
    .first();
  return json({
    ok: true,
    year,
    host: publicProfile(host),
    is_host: host.id === me.id,
    guests: guests.results.map((row) => ({
      code: row.code,
      name: row.name,
      species: row.species,
      color: row.color,
      look: parseLook(row.look),
      here: row.seen_at > t - 90,
    })),
    wishes: wishes.results.map((w) => ({
      id: w.id,
      text: w.text,
      at: w.created_at,
      from: { code: w.code, name: w.name, species: w.species, color: w.color },
    })),
    gifts: gifts.n,
  });
}

async function partyWish(request, env, me) {
  const body = await readJson(request);
  if (body === null) return fail(400, "bad_json");
  if (isMuted(me)) return fail(403, "muted");
  const code = cleanText(body.code, 8, "").toUpperCase();
  const text = cleanText(body.text, 200, "");
  if (!text) return fail(400, "empty");
  const { host, error } = await partyHost(env, me, code);
  if (error) return error;
  const year = partyYear(host, body.date);
  if (!year) return fail(400, "not_birthday");
  const mine = await env.DB.prepare(
    "SELECT COUNT(*) AS n FROM party_wishes WHERE host_id = ? AND year = ? AND from_id = ?"
  )
    .bind(host.id, year, me.id)
    .first();
  if (mine.n >= WISHES_PER_GUEST) return fail(429, "wish_limit");
  await env.DB.prepare("INSERT INTO party_wishes (host_id, year, from_id, text, created_at) VALUES (?, ?, ?, ?, ?)")
    .bind(host.id, year, me.id, text, now())
    .run();
  return json({ ok: true });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    const method = request.method.toUpperCase();
    try {
      if (path === "/" || path === "/v1/health") {
        return json({ ok: true, service: "pips-friends", version: VERSION });
      }
      if (path === "/v1/register" && method === "POST") {
        return await register(request, env);
      }
      const me = await authenticate(request, env);
      if (!me) return fail(401, "unauthorized");
      if (path === "/v1/profile" && method === "POST") return await updateProfile(request, env, me);
      if (path === "/v1/board" && method === "GET") return await board(env, me, url);
      if (path === "/v1/friends/add" && method === "POST") return await addFriend(request, env, me);
      if (path === "/v1/friends/remove" && method === "POST") return await removeFriend(request, env, me);
      if (path === "/v1/groups/join" && method === "POST") return await joinGroup(request, env, me);
      if (path === "/v1/groups/leave" && method === "POST") return await leaveGroup(request, env, me);
      if (path === "/v1/me" && method === "DELETE") return await deleteMe(env, me);
      if (path === "/v1/send" && method === "POST") return await sendMessage(request, env, me);
      if (path === "/v1/inbox" && method === "GET") return await inbox(env, me, url);
      if (path === "/v1/community/delete" && method === "POST") return await deleteCommunityMessage(request, env, me);
      if (path === "/v1/circles/create" && method === "POST") return await createCircle(request, env, me);
      if (path === "/v1/circles/invite" && method === "POST") return await inviteToCircle(request, env, me);
      if (path === "/v1/circles/respond" && method === "POST") return await respondInvite(request, env, me);
      if (path === "/v1/circles/leave" && method === "POST") return await leaveCircle(request, env, me);
      if (path === "/v1/circles/kick" && method === "POST") return await kickFromCircle(request, env, me);
      if (path === "/v1/friends/respond" && method === "POST") return await respondRequest(request, env, me);
      if (path === "/v1/friends/cancel" && method === "POST") return await cancelRequest(request, env, me);
      if (path === "/v1/friends/label" && method === "POST") return await setLabel(request, env, me);
      if (path === "/v1/avatar" && method === "POST") return await setAvatar(request, env, me);
      if (path === "/v1/avatar" && method === "GET") return await getAvatar(env, url);
      if (path === "/v1/leaderboard" && method === "GET") return await leaderboard(env, me, url);
      if (path === "/v1/user" && method === "GET") return await viewUser(env, me, url);
      if (path === "/v1/report" && method === "POST") return await reportUser(request, env, me);
      if (path === "/v1/mod/reports" && method === "GET") return await modReports(env, me);
      if (path === "/v1/mod/action" && method === "POST") return await modAction(request, env, me);
      if (path === "/v1/mod/versions" && method === "GET") return await modVersions(env, me);
      if (path === "/v1/mod/contact" && method === "POST") return await modContact(request, env, me);
      if (path === "/v1/mod/remind" && method === "POST") return await modRemind(request, env, me);
      if (path === "/v1/admin/role" && method === "POST") return await adminRole(request, env, me);
      if (path === "/v1/admin/premium" && method === "POST") return await adminPremium(request, env, me);
      if (path === "/v1/admin/keys" && method === "POST") return await adminKeys(request, env, me);
      if (path === "/v1/admin/overview" && method === "GET") return await adminOverview(env, me);
      if (path === "/v1/premium/redeem" && method === "POST") return await redeemKey(request, env, me);
      if (path === "/v1/arena" && method === "GET") return await arenaState(env, me, url);
      if (path === "/v1/location" && method === "POST") return await setLocation(request, env, me);
      if (path === "/v1/bot/ask" && method === "POST") return await botAsk(request, env, me);
      if (path === "/v1/geo" && method === "GET") return await geoSearch(env, url);
      if (path === "/v1/duel" && method === "GET") return await duelGet(env, me, url);
      if (path === "/v1/duel/challenge" && method === "POST") return await duelChallenge(request, env, me);
      if (path === "/v1/duel/respond" && method === "POST") return await duelRespond(request, env, me);
      if (path === "/v1/duel/act" && method === "POST") return await duelAct(request, env, me);
      if (path === "/v1/duel/forfeit" && method === "POST") return await duelForfeit(request, env, me);
      if (path === "/v1/duel/quick" && method === "POST") return await duelQuick(request, env, me);
      if (path === "/v1/birthday" && method === "POST") return await setBirthday(request, env, me);
      if (path === "/v1/gift" && method === "POST") return await sendGift(request, env, me);
      if (path === "/v1/gift/claim" && method === "POST") return await claimGifts(request, env, me);
      if (path === "/v1/party" && method === "GET") return await partyGet(env, me, url);
      if (path === "/v1/party/wish" && method === "POST") return await partyWish(request, env, me);
      return fail(404, "not_found");
    } catch (err) {
      console.error(err);
      return fail(500, "server_error");
    }
  },
};
