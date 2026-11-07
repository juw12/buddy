/* Buddy Island for phones, tablets and any browser.
 *
 * The island sits in (and grows out of) the Dynamic Island, notch or punch-hole camera, or floats at the top
 * of plain screens. Tap it to type, press and hold it to talk. Share a screenshot to Buddy (Android share
 * sheet), add a photo, or open ?q=... from a Shortcut / Back Tap. Answers stream from Buddy's free brain,
 * can be read aloud, and Buddy's [POINT] tags become markers on your photo.
 */
(() => {
  "use strict";

  const BRAIN = "https://buddy-brain.juwenmondial.workers.dev";
  const CHAT_URL = `${BRAIN}/v1/chat/completions`;
  const HOLD_MS = 350;                 // held longer than this = talk, shorter = type (same as the desktop apps)
  const MAX_SIDE = 1280;               // photos are shrunk to this before they are sent
  const HISTORY_TURNS = 4;
  const SHARE_CACHE = "buddy-share";

  const SYSTEM_PROMPT = `You are Buddy, a friendly expert helper that lives in the Dynamic Island / notch at the top of the user's screen.
Your reply may be read aloud.

Rules:
- Reply in 1 to 4 short, natural sentences. No markdown, no lists, no emojis, no URLs.
- If the user sent a photo or screenshot, be concrete and refer to what is actually visible in it.
- When the question is about where something is on the photo, point at it: put one tag per element at the very END
  of your reply, in the order to use them, at most 3 tags: [POINT:x,y:short label]
  x and y are the CENTER of the element from 0 to 1000 (0,0 top-left, 1000,1000 bottom-right of the photo).
- If there is no photo or nothing to point at, do not add any tag.`;

  const POINT_TAG = /\[\s*POINT\s*:\s*(\.?\d+(?:\.\d+)?)\s*,\s*(\.?\d+(?:\.\d+)?)\s*(?::\s*([^\]]*))?\]/gi;

  const $ = (id) => document.getElementById(id);
  const islandEl = $("island");
  const labelEl = $("islandLabel");
  const answerEl = $("answer");
  const answerText = $("answerText");
  const answerShot = $("answerShot");
  const answerImg = $("answerImg");
  const answerMarks = $("answerMarks");
  const form = $("composer");
  const input = $("question");
  const photoInput = $("photoInput");
  const photoThumb = $("photoThumb");
  const removePhoto = $("removePhoto");
  const micBtn = $("micBtn");

  const state = {
    history: [],
    photo: null,            // {dataUrl}
    busy: false,
    controller: null,
    lastAnswer: "",
    spokenQuestion: false,
    recognition: null,
    heard: "",
  };

  // ---------------------------------------------------------------- reply parsing (same rules as the apps)
  function thousandths(value) {
    const number = Number(value.startsWith(".") ? `0${value}` : value);
    if (!Number.isFinite(number)) return NaN;
    return value.includes(".") && number <= 1 ? number * 1000 : number;
  }

  function parseReply(text) {
    const points = [];
    for (const match of String(text).matchAll(POINT_TAG)) {
      const x = thousandths(match[1]);
      const y = thousandths(match[2]);
      if (x >= 0 && x <= 1000 && y >= 0 && y <= 1000 && points.length < 3) {
        points.push({ x, y, label: (match[3] || "").trim() });
      }
    }
    const spoken = String(text)
      .replace(POINT_TAG, "")
      .split("[")[0]                                   // an unfinished tag while streaming
      .replace(/[*_#`]+/g, "")
      .replace(/\s+/g, " ")
      .trim();
    return { spoken, points };
  }

  // ---------------------------------------------------------------- the island
  function setIsland(mode, label) {
    islandEl.dataset.state = mode;
    const text = label || { idle: "Ask Buddy", listening: "Listening\u2026", thinking: "Thinking\u2026",
                            answer: "Buddy" }[mode];
    labelEl.textContent = text;
    const aria = {
      idle: "Ask Buddy. Tap to type, press and hold to talk.",
      listening: "Buddy is listening. Release to send.",
      thinking: "Buddy is thinking.",
      answer: "Buddy answered. Tap to close.",
    }[mode];
    islandEl.setAttribute("aria-label", aria);
  }

  function showAnswer(text) {
    answerEl.hidden = false;
    answerText.textContent = text;
  }

  function closeAnswer() {
    const controller = state.controller;
    state.controller = null;
    if (controller) controller.abort();
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    answerEl.hidden = true;
    answerShot.hidden = true;
    answerMarks.textContent = "";
    setIsland("idle");
  }

  function drawMarks(points) {
    answerMarks.textContent = "";
    if (!state.photo || !points.length) return;
    answerImg.src = state.photo.dataUrl;
    answerShot.hidden = false;
    for (const point of points) {
      const mark = document.createElement("div");
      mark.className = "mark";
      mark.style.left = `${point.x / 10}%`;
      mark.style.top = `${point.y / 10}%`;
      if (point.label) {
        const caption = document.createElement("span");
        caption.textContent = point.label;
        mark.append(caption);
      }
      answerMarks.append(mark);
    }
  }

  function speak(text) {
    if (!("speechSynthesis" in window) || !text) return;
    try {
      speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = navigator.language || "en-US";
      speechSynthesis.speak(utterance);
    } catch (_) {
      /* no voice on this device: the answer is still on screen */
    }
  }

  // ---------------------------------------------------------------- asking the brain
  function friendlyError(status, body) {
    const code = body && body.error && body.error.code;
    if (code === "daily_cap") return "The free brain is out of questions for today. It's back tomorrow.";
    if (code === "rate_limited") return "That's a lot of questions in a minute. Give me a few seconds and ask again.";
    if (code === "too_large") return "That photo is too big. Try a smaller one.";
    if (body && body.error && body.error.message) return body.error.message;
    return `I can't reach my brain right now (${status || "offline"}). Check the connection and try again.`;
  }

  async function ask(question) {
    question = (question || "").trim();
    if (!question && !state.photo) {
      input.focus();
      return;
    }
    if (!question) question = "What is this, and what should I do here?";
    if (state.busy && state.controller) state.controller.abort();
    state.busy = true;
    const photo = state.photo;
    const controller = new AbortController();
    state.controller = controller;
    const timer = setTimeout(() => controller.abort(), 60000);
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    answerShot.hidden = true;
    answerMarks.textContent = "";
    showAnswer(`\u201c${question}\u201d`);
    setIsland("thinking", photo ? "Looking at your photo\u2026" : "Thinking\u2026");

    const userContent = photo
      ? [{ type: "text", text: question }, { type: "image_url", image_url: { url: photo.dataUrl } }]
      : question;
    const messages = [{ role: "system", content: SYSTEM_PROMPT }, ...state.history.slice(-2 * HISTORY_TURNS),
                      { role: "user", content: userContent }];
    let reply = "";
    try {
      const response = await fetch(CHAT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: "buddy-free", messages, stream: true, max_tokens: 400 }),
        signal: controller.signal,
      });
      if (!response.ok) {
        let body = null;
        try { body = await response.json(); } catch (_) { /* not JSON */ }
        throw Object.assign(new Error(friendlyError(response.status, body)), { friendly: true });
      }
      setIsland("answer", "Buddy");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith("data:")) continue;
          const payload = trimmed.slice(5).trim();
          if (payload === "[DONE]") continue;
          try {
            const data = JSON.parse(payload);
            const piece = data.choices?.[0]?.delta?.content ?? data.response ?? "";
            reply += piece == null ? "" : String(piece);
            const shown = parseReply(reply).spoken;
            if (shown) showAnswer(shown);
          } catch (_) { /* keep-alive or partial line */ }
        }
      }
    } catch (error) {
      clearTimeout(timer);
      state.busy = false;
      if (controller.signal.aborted && !error.friendly && state.controller !== controller) return;   // replaced
      const message = error.friendly ? error.message : friendlyError(0, null);
      showAnswer(message);
      setIsland("answer", "Buddy");
      return;
    }
    clearTimeout(timer);
    state.busy = false;
    const { spoken, points } = parseReply(reply);
    const finalText = spoken || "Hmm, I'm not sure. Try asking another way?";
    state.lastAnswer = finalText;
    showAnswer(finalText);
    drawMarks(points);
    setIsland("answer", "Buddy");
    state.history.push({ role: "user", content: question }, { role: "assistant", content: finalText });
    const readAloud = state.spokenQuestion;
    state.spokenQuestion = false;
    clearPhoto();
    if (readAloud) speak(finalText);
    refreshQuota();
  }

  // ---------------------------------------------------------------- voice (Web Speech API)
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  function startListening() {
    if (!Recognition) {
      showAnswer("Voice isn't available in this browser. Type your question below instead.");
      setIsland("answer", "Buddy");
      input.focus();
      return;
    }
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    state.heard = "";
    const recognition = new Recognition();
    recognition.lang = navigator.language || "en-US";
    recognition.interimResults = true;
    recognition.continuous = false;
    recognition.onresult = (event) => {
      let text = "";
      for (const result of event.results) text += result[0].transcript;
      state.heard = text;
      setIsland("listening", `\u201c${text}\u201d`);
    };
    recognition.onerror = (event) => {
      state.recognition = null;
      micBtn.setAttribute("aria-pressed", "false");
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        showAnswer("Buddy needs the microphone to listen. Allow it for this site, or type instead.");
        setIsland("answer", "Buddy");
      } else if (event.error !== "aborted" && event.error !== "no-speech") {
        showAnswer(`I couldn't listen (${event.error}). Type your question below instead.`);
        setIsland("answer", "Buddy");
      } else {
        setIsland("idle");
      }
    };
    recognition.onend = () => {
      const heard = state.heard.trim();
      state.recognition = null;
      micBtn.setAttribute("aria-pressed", "false");
      if (heard) {
        state.spokenQuestion = true;
        ask(heard);
      } else if (islandEl.dataset.state === "listening") {
        setIsland("idle");
      }
    };
    try {
      recognition.start();
      state.recognition = recognition;
      micBtn.setAttribute("aria-pressed", "true");
      setIsland("listening");
    } catch (error) {
      setIsland("idle");
    }
  }

  function stopListening() {
    if (state.recognition) state.recognition.stop();         // onend sends what was heard
  }

  // Tap = type, hold = talk, on any element (the island and the mic button)
  function holdToTalk(element, onTap) {
    let timer = null;
    let held = false;
    let down = false;
    element.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      down = true;
      held = false;
      element.setPointerCapture?.(event.pointerId);
      timer = setTimeout(() => {
        held = true;
        startListening();
      }, HOLD_MS);
    });
    const release = () => {
      if (!down) return;
      down = false;
      clearTimeout(timer);
      if (held) stopListening();
      else onTap();
    };
    element.addEventListener("pointerup", release);
    element.addEventListener("pointercancel", () => {
      down = false;
      clearTimeout(timer);
      if (held) stopListening();
    });
    element.addEventListener("contextmenu", (event) => event.preventDefault());
    element.addEventListener("click", (event) => {
      if (event.detail === 0) onTap();          // keyboard (Enter / Space): pointer events don't fire
    });
  }

  function islandTap() {
    const mode = islandEl.dataset.state;
    if (mode === "answer") {
      closeAnswer();
      return;
    }
    if (mode === "listening") {
      stopListening();
      return;
    }
    if (pipWindow) pipWindow.focus();
    input.focus();
  }

  holdToTalk(islandEl, islandTap);
  holdToTalk(micBtn, () => {
    if (state.recognition) stopListening();
    else startListening();
  });

  // ---------------------------------------------------------------- composer + photos
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value;
    input.value = "";
    input.blur();
    ask(question);
  });

  $("photoBtn").addEventListener("click", () => photoInput.click());
  photoInput.addEventListener("change", () => {
    const file = photoInput.files && photoInput.files[0];
    if (file) usePhoto(file);
    photoInput.value = "";
  });
  removePhoto.addEventListener("click", clearPhoto);

  async function shrink(blob) {
    const url = URL.createObjectURL(blob);
    try {
      const image = await new Promise((resolve, reject) => {
        const element = new Image();
        element.onload = () => resolve(element);
        element.onerror = () => reject(new Error("not an image"));
        element.src = url;
      });
      const scale = Math.min(1, MAX_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
      return canvas.toDataURL("image/jpeg", 0.82);
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  async function usePhoto(blob) {
    try {
      const dataUrl = await shrink(blob);
      state.photo = { dataUrl };
      photoThumb.src = dataUrl;
      photoThumb.hidden = false;
      removePhoto.hidden = false;
      input.placeholder = "What do you want to know about it?";
      input.focus();
    } catch (error) {
      showAnswer("I couldn't open that picture. Try a PNG or JPEG.");
      setIsland("answer", "Buddy");
    }
  }

  function clearPhoto() {
    state.photo = null;
    photoThumb.hidden = true;
    photoThumb.removeAttribute("src");
    removePhoto.hidden = true;
    input.placeholder = "Ask Buddy\u2026";
  }

  $("closeAnswer").addEventListener("click", closeAnswer);
  $("speakAgain").addEventListener("click", () => speak(state.lastAnswer || answerText.textContent));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !answerEl.hidden) closeAnswer();
  });

  // ---------------------------------------------------------------- free-brain quota
  async function refreshQuota() {
    const quota = $("quota");
    try {
      const response = await fetch(`${BRAIN}/health`, { cache: "no-store" });
      const data = await response.json();
      const left = data && data.today ? data.today.left : null;
      quota.textContent = left == null
        ? "Free brain: shared by everyone, with a daily limit."
        : `Free brain: ${left} questions left today, shared by everyone.`;
    } catch (_) {
      quota.textContent = "Free brain: can't check the daily limit right now.";
    }
  }

  // ---------------------------------------------------------------- per-device setup tips
  function deviceKind() {
    const ua = navigator.userAgent || "";
    if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "ios";
    if (/Android/.test(ua)) return "android";
    return "desktop";
  }

  const kind = deviceKind();
  const list = $("setupList");
  const mine = list.querySelector(`[data-os="${kind}"]`);
  if (mine) {
    mine.classList.add("here");
    list.prepend(mine);
  }
  // A Shortcut on the phone needs a public https address; keep the canonical one otherwise
  // (e.g. when the page is opened from a local test server).
  if (location.protocol === "https:") {
    $("iosUrl").textContent = `${location.host}${location.pathname}?talk=1`;
  }

  let installEvent = null;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installEvent = event;
    $("installBtn").hidden = false;
    $("installHint").hidden = true;
  });
  $("installBtn").addEventListener("click", async () => {
    if (!installEvent) return;
    installEvent.prompt();
    await installEvent.userChoice.catch(() => null);
    installEvent = null;
    $("installBtn").hidden = true;
  });

  // ---------------------------------------------------------------- pop out (always on top, any computer)
  let pipWindow = null;
  const popoutBtn = $("popoutBtn");
  if (!("documentPictureInPicture" in window)) {
    const note = document.createElement("span");
    note.textContent = "In Chrome or Edge on a computer you can pop Buddy out into a tiny window that stays on top.";
    popoutBtn.replaceWith(note);
  } else {
    popoutBtn.addEventListener("click", async () => {
      if (pipWindow) {
        pipWindow.close();
        return;
      }
      const wrap = document.querySelector(".island-wrap");
      pipWindow = await window.documentPictureInPicture.requestWindow({ width: 420, height: 320 });
      for (const sheet of document.querySelectorAll('link[rel="stylesheet"]')) {
        const link = pipWindow.document.createElement("link");
        link.rel = "stylesheet";
        link.href = sheet.href;
        pipWindow.document.head.append(link);
      }
      pipWindow.document.title = "Buddy";
      pipWindow.document.body.className = "pip";
      pipWindow.document.body.append(wrap);
      popoutBtn.textContent = "Bring Buddy back";
      pipWindow.addEventListener("pagehide", () => {
        document.body.prepend(wrap);
        pipWindow = null;
        popoutBtn.textContent = "Pop Buddy out";
      });
    });
  }

  // ---------------------------------------------------------------- arriving from a share, a Shortcut or a link
  async function takeShared() {
    if (!("caches" in window)) return;
    const cache = await caches.open(SHARE_CACHE);
    const image = await cache.match("shared-image");
    const meta = await cache.match("shared-meta");
    await cache.delete("shared-image");
    await cache.delete("shared-meta");
    let text = "";
    if (meta) {
      const data = await meta.json().catch(() => ({}));
      text = [data.title, data.text, data.url].filter(Boolean).join(" ").trim();
    }
    if (image) await usePhoto(await image.blob());
    if (text) input.value = text;
    if (image || text) input.focus();
  }

  const params = new URLSearchParams(location.search);
  if (params.has("shared")) {
    takeShared().catch(() => null);
    history.replaceState(null, "", location.pathname);
  }
  if (params.get("q")) {
    ask(params.get("q"));
    history.replaceState(null, "", location.pathname);
  } else if (params.has("talk")) {
    setIsland("idle", "Hold to talk");
  }
  refreshQuota();

  if ("serviceWorker" in navigator && location.protocol === "https:") {
    navigator.serviceWorker.register("sw.js").catch(() => null);
  }

  // For tests and the curious: the pure parts
  window.BuddyIsland = { parseReply, ask, setIsland };
})();
