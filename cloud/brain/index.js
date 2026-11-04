// Buddy's free brain: an OpenAI-compatible /v1/chat/completions endpoint on Cloudflare Workers AI.
//
// Free by design: a per-IP rate limit plus a global daily cap keep usage inside Workers AI's free
// daily allowance, so this never costs anything. Nothing is stored except one counter per day.

// "buddy-free" is the stable name clients ask for; it points at whichever model points best.
// Tested on a Notepad screenshot: Llama 4 Scout lands on the right menu item, Mistral Small misses.
const MODELS = {
  "buddy-free": "@cf/meta/llama-4-scout-17b-16e-instruct",
  "buddy-scout": "@cf/meta/llama-4-scout-17b-16e-instruct",
  "buddy-mistral": "@cf/mistralai/mistral-small-3.1-24b-instruct",
};
const DEFAULT_MODEL = "buddy-free";
const DAILY_CAP = 180;              // ~50 neurons per screenshot question => well under 10,000/day
const MAX_BODY_BYTES = 4_000_000;   // a 1280 px JPEG screenshot is ~100-300 KB
const MAX_TOKENS = 500;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...CORS },
  });
}

function fail(message, status, code) {
  return json({ error: { message, type: code || "error", code: code || null } }, status);
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

async function usedToday(env) {
  const row = await env.DB.prepare("SELECT count FROM usage WHERE day = ?1").bind(today()).first();
  return row ? row.count : 0;
}

async function countOne(env) {
  const row = await env.DB.prepare(
    "INSERT INTO usage (day, count) VALUES (?1, 1) ON CONFLICT(day) DO UPDATE SET count = count + 1 RETURNING count"
  ).bind(today()).first();
  return row.count;
}

// Keep only what the models understand: role + text / image_url parts.
function cleanMessages(messages) {
  if (!Array.isArray(messages) || messages.length === 0 || messages.length > 24) {
    return null;
  }
  const cleaned = [];
  for (const message of messages) {
    const role = ["system", "user", "assistant"].includes(message?.role) ? message.role : null;
    if (!role) {
      return null;
    }
    if (typeof message.content === "string") {
      cleaned.push({ role, content: message.content.slice(0, 8000) });
    } else if (Array.isArray(message.content)) {
      const parts = [];
      for (const part of message.content) {
        if (part?.type === "text" && typeof part.text === "string") {
          parts.push({ type: "text", text: part.text.slice(0, 8000) });
        } else if (part?.type === "image_url" && typeof part.image_url?.url === "string"
                   && part.image_url.url.startsWith("data:image/")) {
          parts.push({ type: "image_url", image_url: { url: part.image_url.url } });
        }
      }
      cleaned.push({ role, content: parts });
    } else {
      return null;
    }
  }
  return cleaned;
}

// Scout often writes y on a 0..1 scale ("[POINT:183,0.440:Save]"). Clients expect 0..1000, so a
// number <= 1 with a decimal point is scaled up. Whole numbers are left alone.
const POINT_TAG = /\[\s*POINT\s*:\s*([\d.]+)\s*,\s*([\d.]+)/gi;

function toThousand(value) {
  const number = Number(value.startsWith(".") ? `0${value}` : value);
  if (!Number.isFinite(number)) {
    return value;
  }
  if (value.includes(".") && number <= 1) {
    return String(Math.round(number * 1000));
  }
  return String(Math.round(number));
}

function fixPoints(text) {
  return text.replace(POINT_TAG, (_, x, y) => `[POINT:${toThousand(x)},${toThousand(y)}`);
}

// Holds back text from an unfinished "[" until its "]" arrives, so a tag split across stream
// chunks can still be fixed. Gives up holding after 120 characters (not a tag then).
function pointStreamFixer() {
  let pending = "";
  return {
    push(text) {
      pending += text;
      const open = pending.lastIndexOf("[");
      if (open === -1 || pending.indexOf("]", open) !== -1 || pending.length - open > 120) {
        const out = fixPoints(pending);
        pending = "";
        return out;
      }
      const out = fixPoints(pending.slice(0, open));
      pending = pending.slice(open);
      return out;
    },
    flush() {
      const out = fixPoints(pending);
      pending = "";
      return out;
    },
  };
}

// Workers AI streams {"response": "..."} chunks (newer models may send OpenAI-style chunks).
// Turn either into OpenAI chat.completion.chunk events so every OpenAI client just works.
function toOpenAIStream(aiStream, model) {
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  let buffer = "";
  const fixer = pointStreamFixer();
  const id = `chatcmpl-${crypto.randomUUID()}`;
  const send = (controller, content) => {
    const chunk = { id, object: "chat.completion.chunk", model, choices: [{ index: 0, delta: { content } }] };
    controller.enqueue(encoder.encode(`data: ${JSON.stringify(chunk)}\n\n`));
  };
  return aiStream.pipeThrough(new TransformStream({
    transform(bytes, controller) {
      buffer += decoder.decode(bytes, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) {
          continue;
        }
        const payload = trimmed.slice(5).trim();
        if (payload === "[DONE]") {
          continue;
        }
        try {
          const data = JSON.parse(payload);
          // Workers AI sends number-only tokens (e.g. 450) as JSON numbers: always forward text
          const piece = data.response ?? data.choices?.[0]?.delta?.content ?? "";
          const text = piece === null || piece === undefined ? "" : String(piece);
          const fixed = text ? fixer.push(text) : "";
          if (fixed) {
            send(controller, fixed);
          }
        } catch {
          // ignore partial / keep-alive lines
        }
      }
    },
    flush(controller) {
      const rest = fixer.flush();
      if (rest) {
        send(controller, rest);
      }
      controller.enqueue(encoder.encode("data: [DONE]\n\n"));
    },
  }));
}

function replyText(result) {
  if (typeof result === "string") {
    return result;
  }
  const text = result?.response ?? result?.choices?.[0]?.message?.content ?? "";
  return String(text ?? "");
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }
    if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
      const used = await usedToday(env);
      return json({
        ok: true,
        service: "buddy-brain",
        models: Object.keys(MODELS),
        today: { used, left: Math.max(0, DAILY_CAP - used) },
        privacy: "Screenshots and questions are sent to Cloudflare Workers AI to answer, and are not stored here.",
      });
    }
    if (url.pathname !== "/v1/chat/completions" || request.method !== "POST") {
      return fail("Not found. POST /v1/chat/completions", 404, "not_found");
    }

    const ip = request.headers.get("cf-connecting-ip") || "unknown";
    const { success } = await env.RL.limit({ key: ip });
    if (!success) {
      return fail("Slow down a little: too many questions this minute. Try again shortly.", 429, "rate_limited");
    }
    if (Number(request.headers.get("content-length") || 0) > MAX_BODY_BYTES) {
      return fail("That screenshot is too big. Send a JPEG no wider than 1280 px.", 413, "too_large");
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return fail("Send a JSON body.", 400, "bad_request");
    }
    const messages = cleanMessages(body.messages);
    if (!messages) {
      return fail("messages must be a list of {role, content}.", 400, "bad_request");
    }

    const count = await countOne(env);
    if (count > DAILY_CAP) {
      return fail(
        "The free brain is out of questions for today. Try again tomorrow, or switch Buddy to a local brain.",
        429,
        "daily_cap"
      );
    }

    const alias = MODELS[body.model] ? body.model : DEFAULT_MODEL;
    const options = {
      messages,
      max_tokens: Math.min(Number(body.max_tokens) || 400, MAX_TOKENS),
      temperature: typeof body.temperature === "number" ? body.temperature : 0.2,
      stream: body.stream === true,
    };
    try {
      const result = await env.AI.run(MODELS[alias], options);
      if (options.stream) {
        return new Response(toOpenAIStream(result, alias), {
          headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", ...CORS },
        });
      }
      return json({
        id: `chatcmpl-${crypto.randomUUID()}`,
        object: "chat.completion",
        model: alias,
        choices: [{ index: 0, message: { role: "assistant", content: fixPoints(replyText(result)) }, finish_reason: "stop" }],
      });
    } catch (error) {
      const message = String(error?.message || error);
      if (/neuron|quota|limit|429/i.test(message)) {
        return fail("The free brain is out of questions for today. Try again tomorrow.", 429, "daily_cap");
      }
      return fail(`The free brain had a problem: ${message.slice(0, 200)}`, 502, "upstream");
    }
  },
};
