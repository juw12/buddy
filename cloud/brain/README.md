# Buddy's free brain

A tiny Cloudflare Worker that gives Buddy (Windows and Mac) a vision brain that works out of the box:
an OpenAI-compatible `POST /v1/chat/completions` in front of Cloudflare Workers AI.

- Model: `buddy-free` = Llama 4 Scout 17B (picked because it points at the right menu item; Mistral Small 3.1
  missed in the same test). `buddy-mistral` is also available.
- Free by design: 20 questions a minute per IP and 180 a day in total, which keeps it inside the Workers AI free
  allowance (10,000 neurons a day; a screenshot question costs about 42).
- Privacy: nothing is stored except one counter per day. Screenshots go to Workers AI to be answered.
- `GET /health` shows how many questions are left today.
- Scout sometimes writes a coordinate as a 0..1 fraction (`[POINT:183,0.440]`); the Worker rewrites those to
  0..1000 while streaming, so every client gets the format it expects.

Deploy your own: create a D1 database `buddy-brain` with
`CREATE TABLE usage (day TEXT PRIMARY KEY, count INTEGER NOT NULL)`, put its id in `wrangler.toml`, then
`npx wrangler deploy`. Point Buddy at it with `BUDDY_FREE_URL` (Windows) or Brain… (Mac).
