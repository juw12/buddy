# Pips Friends server

Cloudflare Worker + D1 behind the in-app Friends feature (social.py).

* URL: https://pips-friends.juwenmondial.workers.dev
* Account: (the owner's Cloudflare account)
* D1 database: pips-friends (9df328b7-b300-4b61-b7b3-06e8d2fd4114)

Deploy: `npx wrangler deploy` in this folder (after `npx wrangler login`).
Schema: `npx wrangler d1 execute pips-friends --remote --file=schema.sql`.
