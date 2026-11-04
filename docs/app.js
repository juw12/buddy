/* Buddy site: live demo engine, page buddy, voices, brains. No libraries. */
(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const finePointer = matchMedia("(hover: hover) and (pointer: fine)").matches;

  /* =====================================================================
     Stage scaling: the fake desktop is drawn at 800x500 and scaled to fit.
     ===================================================================== */
  const STAGE_W = 800;
  const STAGE_H = 500;
  const stageWrap = $("#stageWrap");
  const stage = $("#stage");
  let scale = 1;

  function fitStage() {
    scale = stageWrap.clientWidth / STAGE_W;
    stage.style.transform = `scale(${scale})`;
    stageWrap.style.height = `${STAGE_H * scale}px`;
  }
  new ResizeObserver(fitStage).observe(stageWrap);
  fitStage();

  /** A point on an element, in stage coordinates (ax/ay are 0..1 across the element). */
  function pointOf(element, ax = 0.5, ay = 0.5) {
    const box = element.getBoundingClientRect();
    const origin = stage.getBoundingClientRect();
    return {
      x: (box.left - origin.left + box.width * ax) / scale,
      y: (box.top - origin.top + box.height * ay) / scale,
    };
  }

  /* =====================================================================
     Desktop pieces
     ===================================================================== */
  const el = {
    buddy: $("#sbuddy"),
    bubble: $("#sbBubble"),
    pulse: $("#sbPulse"),
    cursor: $("#sysCursor"),
    notepad: $("#notepad"),
    npTitle: $("#npTitle"),
    npTitleText: $("#npTitleText"),
    npDoc: $("#npDoc"),
    menuFile: $("#menuFile"),
    fileMenu: $("#fileMenu"),
    menuSaveAs: $("#menuSaveAs"),
    saveDialog: $("#saveDialog"),
    dlgSave: $("#dlgSave"),
    dlgDont: $("#dlgDont"),
    dlgCancel: $("#dlgCancel"),
    calc: $("#calc"),
    confirm: $("#confirmBox"),
    confirmText: $("#confirmText"),
    confirmYes: $("#confirmYes"),
    confirmNo: $("#confirmNo"),
    toast: $("#toast"),
    tbStart: $("#tbStart"),
    tbNotepad: $("#tbNotepad"),
    tbCalc: $("#tbCalc"),
    keys: $("#keys"),
    said: $("#saidText"),
    status: $("#status"),
    statusText: $("#statusText"),
    chips: $("#chips"),
    followups: $("#followups"),
    log: $("#log"),
    askForm: $("#askForm"),
    askInput: $("#askInput"),
  };

  const BASE_NOTES = [
    "meeting notes - Tue",
    "- send Batam the line 3 report",
    "- ship the Buddy website ✓",
    "",
    "Edge said: ",
  ].join("\n");
  const ERROR_TEXT = "ERR_PROXY_CONNECTION_FAILED";

  const desk = {
    notepadOpen: true,
    calcOpen: false,
    dirty: false,
    typed: "",
    highlight: false,
  };

  function escapeHtml(text) {
    return text.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }

  function renderDoc() {
    const error = desk.highlight
      ? `<mark id="npError">${ERROR_TEXT}</mark>`
      : `<span id="npError">${ERROR_TEXT}</span>`;
    el.npDoc.innerHTML = `${escapeHtml(BASE_NOTES)}${error}  ← ??${escapeHtml(desk.typed)}<span class="caret" id="npCaret"></span>`;
    el.npTitleText.textContent = `${desk.dirty ? "*" : ""}notes.txt - Notepad`;
  }

  function setVisible(element, visible) {
    element.classList.toggle("hidden", !visible);
  }

  function showNotepad(visible) {
    desk.notepadOpen = visible;
    setVisible(el.notepad, visible);
    el.tbNotepad.classList.toggle("on", visible);
  }

  function showCalc(visible) {
    desk.calcOpen = visible;
    setVisible(el.calc, visible);
    el.tbCalc.classList.toggle("on", visible);
  }

  function resetTransient() {
    el.fileMenu.classList.remove("open");
    el.menuFile.classList.remove("open");
    $$(".hot", stage).forEach((node) => node.classList.remove("hot"));
    setVisible(el.saveDialog, false);
    el.confirm.classList.remove("show");
    el.followups.innerHTML = "";
    if (desk.highlight) {
      desk.highlight = false;
      renderDoc();
    }
  }

  let toastTimer = 0;
  function toast(text) {
    el.toast.textContent = text;
    el.toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.toast.classList.remove("show"), 2200);
  }

  function ripple(point) {
    const dot = document.createElement("div");
    dot.className = "click-ripple";
    dot.style.left = `${point.x}px`;
    dot.style.top = `${point.y}px`;
    stage.appendChild(dot);
    setTimeout(() => dot.remove(), 600);
  }

  /* the user's own mouse pointer, and Buddy's resting spot next to it */
  const cursor = { x: 640, y: 318 };
  const REST_OFFSET = { x: 16, y: 20 };
  function moveCursor(x, y) {
    cursor.x = x;
    cursor.y = y;
    el.cursor.style.transform = `translate(${x}px, ${y}px)`;
  }
  const restSpot = () => ({ x: cursor.x + REST_OFFSET.x, y: cursor.y + REST_OFFSET.y });

  /* Buddy position */
  const pos = { x: 0, y: 0 };
  function placeBuddy(x, y) {
    pos.x = x;
    pos.y = y;
    el.buddy.style.transform = `translate(${x}px, ${y}px)`;
    el.buddy.classList.toggle("flip", x > STAGE_W - 300);
    el.buddy.classList.toggle("up", y > STAGE_H - 130);
  }

  /* =====================================================================
     Log, status, transcript
     ===================================================================== */
  function clock() {
    return new Date().toLocaleTimeString("en-GB", { hour12: false });
  }

  function log(verb, detail, kind = "") {
    const empty = $(".empty", el.log);
    if (empty) {
      empty.remove();
    }
    const row = document.createElement("li");
    const kindClass = kind ? `k-${kind}` : "";
    row.innerHTML = `<span class="t">${clock()}</span><span class="v ${kindClass}">${verb}</span><span class="d"></span>`;
    $(".d", row).textContent = detail;
    el.log.appendChild(row);
    el.log.scrollTop = el.log.scrollHeight;
  }

  let thinkTimer = 0;
  function setStatus(mode, text) {
    clearInterval(thinkTimer);
    el.status.dataset.mode = mode;
    el.statusText.textContent = text;
  }

  /* =====================================================================
     Scenario runner (a new request cancels whatever Buddy was doing)
     ===================================================================== */
  const CANCEL = Symbol("cancel");
  let token = 0;
  let pendingRejects = [];

  function cancelAll() {
    token += 1;
    pendingRejects.forEach((reject) => reject(CANCEL));
    pendingRejects = [];
    resetTransient();
    $$(".chip.running").forEach((chip) => chip.classList.remove("running"));
  }

  function makeApi(myToken) {
    const alive = () => {
      if (myToken !== token) {
        throw CANCEL;
      }
    };

    const sleep = (ms) => new Promise((resolve, reject) => {
      setTimeout(() => (myToken === token ? resolve() : reject(CANCEL)), ms);
    });

    function mode(name) {
      el.buddy.classList.toggle("listen", name === "listen");
      el.buddy.classList.toggle("think", name === "think");
    }

    function fly(target, ms = 650) {
      const from = { x: pos.x, y: pos.y };
      const dx = target.x - from.x;
      const dy = target.y - from.y;
      const distance = Math.hypot(dx, dy);
      if (distance < 1) {
        return Promise.resolve();
      }
      const bend = Math.min(70, distance * 0.22);
      const control = {
        x: (from.x + target.x) / 2 - (dy / distance) * bend,
        y: (from.y + target.y) / 2 + (dx / distance) * bend,
      };
      const duration = Math.max(280, Math.min(ms, 260 + distance * 1.1));
      return new Promise((resolve, reject) => {
        const started = performance.now();
        function step(now) {
          if (myToken !== token) {
            reject(CANCEL);
            return;
          }
          const k = Math.min(1, (now - started) / duration);
          const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
          const x = (1 - e) * (1 - e) * from.x + 2 * (1 - e) * e * control.x + e * e * target.x;
          const y = (1 - e) * (1 - e) * from.y + 2 * (1 - e) * e * control.y + e * e * target.y;
          placeBuddy(x, y);
          if (k < 1) {
            requestAnimationFrame(step);
          } else {
            resolve();
          }
        }
        requestAnimationFrame(step);
      });
    }

    async function typewrite(node, text, perChar) {
      node.textContent = "";
      for (const char of text) {
        alive();
        node.textContent += char;
        await sleep(perChar);
      }
    }

    async function say(text) {
      el.bubble.classList.add("show");
      await typewrite(el.bubble, text, 16);
    }

    function hush() {
      el.bubble.classList.remove("show");
    }

    function pulse() {
      el.pulse.classList.remove("go");
      void el.pulse.offsetWidth;
      el.pulse.classList.add("go");
    }

    /** Hold-to-talk (or tap-to-type) and the transcript. */
    async function listen(phrase, typed = false) {
      alive();
      hush();
      el.said.classList.remove("idle");
      el.said.textContent = "";
      el.keys.classList.add("down");
      if (typed) {
        await sleep(160);
        el.keys.classList.remove("down");
        setStatus("listening", "ask box");
        await typewrite(el.said, phrase, 12);
        await sleep(200);
      } else {
        mode("listen");
        setStatus("listening", "listening…");
        await sleep(350);
        await typewrite(el.said, phrase, 40);
        await sleep(300);
        el.keys.classList.remove("down");
      }
    }

    async function think(ms) {
      mode("think");
      const started = performance.now();
      setStatus("thinking", "thinking · 0.0 s");
      thinkTimer = setInterval(() => {
        el.statusText.textContent = `thinking · ${((performance.now() - started) / 1000).toFixed(1)} s`;
      }, 100);
      await sleep(ms);
      clearInterval(thinkTimer);
      mode("arrow");
      setStatus("pointing", `answered in ${(ms / 1000).toFixed(1)} s`);
    }

    /** Wait for the user to press one of several elements. Resolves with that entry's value. */
    function choose(options) {
      alive();
      return new Promise((resolve, reject) => {
        const cleanups = [];
        const finish = (value) => {
          cleanups.forEach((fn) => fn());
          pendingRejects = pendingRejects.filter((fn) => fn !== rejectNow);
          resolve(value);
        };
        const rejectNow = (reason) => {
          cleanups.forEach((fn) => fn());
          reject(reason);
        };
        options.forEach(({ node, value }) => {
          const handler = (event) => {
            event.preventDefault();
            finish(value);
          };
          node.addEventListener("click", handler);
          cleanups.push(() => node.removeEventListener("click", handler));
        });
        pendingRejects.push(rejectNow);
      });
    }

    function followUps(labels) {
      el.followups.innerHTML = "";
      return labels.map((label) => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "chip";
        chip.textContent = `“${label}”`;
        el.followups.appendChild(chip);
        return chip;
      });
    }

    const home = () => fly(restSpot(), 600);

    return { alive, sleep, mode, fly, say, hush, pulse, listen, think, choose, followUps, home };
  }

  /* ---------------- shared flows ---------------- */

  async function ensureNotepad(a) {
    if (desk.notepadOpen) {
      return;
    }
    await a.say("Notepad's closed, popping it back open first.");
    await a.fly(pointOf(el.tbNotepad, 0.5, 0.4));
    a.pulse();
    showNotepad(true);
    desk.typed = "";
    desk.dirty = false;
    renderDoc();
    log("open", "Notepad");
    await a.sleep(500);
    a.hush();
    await a.home();
  }

  /** Close Notepad by name, then handle Notepad's own save prompt without guessing. */
  async function closeNotepadFlow(a) {
    if (!desk.dirty) {
      showNotepad(false);
      log("close", "Notepad");
      await a.say("Closed Notepad.");
      return;
    }
    setVisible(el.saveDialog, true);
    await a.sleep(250);
    await a.fly(pointOf(el.saveDialog, 0.06, 0.95));
    setStatus("waiting", "waiting for you");
    await a.say("Notepad is asking about saving. Which one? I won't guess.");
    const [chipSave, chipDont, chipCancel] = a.followUps(["click Save", "click Don't Save", "click Cancel"]);
    const choice = await a.choose([
      { node: chipSave, value: { button: "save", spoken: true } },
      { node: chipDont, value: { button: "dont", spoken: true } },
      { node: chipCancel, value: { button: "cancel", spoken: true } },
      { node: el.dlgSave, value: { button: "save", spoken: false } },
      { node: el.dlgDont, value: { button: "dont", spoken: false } },
      { node: el.dlgCancel, value: { button: "cancel", spoken: false } },
    ]);
    el.followups.innerHTML = "";
    const buttons = { save: el.dlgSave, dont: el.dlgDont, cancel: el.dlgCancel };
    const labels = { save: "Save", dont: "Don't Save", cancel: "Cancel" };
    const target = buttons[choice.button];

    if (choice.spoken) {
      await a.listen(`click ${labels[choice.button]}`);
      await a.think(500);
      const spot = pointOf(target, 0.5, 0.55);
      const line = choice.button === "dont"
        ? "“Don't Save”, not “Save”. Every word has to match."
        : `Clicking “${labels[choice.button]}”.`;
      await Promise.all([a.fly(spot), a.say(line)]);
      log("check", "spot is still inside Notepad ✓", "ok");
      setStatus("acting", "clicking");
      await a.sleep(350);
      ripple(spot);
      target.classList.add("pressed");
      log("click", `“${labels[choice.button]}” (Notepad)`);
      await a.sleep(220);
      target.classList.remove("pressed");
    } else {
      log("you", `clicked “${labels[choice.button]}” yourself`, "ok");
      await a.say("Nice, you got it.");
    }

    setVisible(el.saveDialog, false);
    if (choice.button === "cancel") {
      await a.say("Cancelled. Notepad stays open.");
      return;
    }
    if (choice.button === "save") {
      toast("notes.txt saved");
    }
    desk.dirty = false;
    desk.typed = "";
    renderDoc();
    showNotepad(false);
    await a.sleep(300);
    await a.say(choice.button === "save" ? "Saved and closed." : "Closed without saving.");
  }

  /* ---------------- scenarios ---------------- */

  const scenarios = {
    async saveas(a) {
      await ensureNotepad(a);
      await a.listen("where's Save As?");
      await a.think(900);
      await Promise.all([a.fly(pointOf(el.menuFile, 0.55, 0.85)), a.say("It's in the File menu. Follow me.")]);
      el.menuFile.classList.add("open");
      el.fileMenu.classList.add("open");
      await a.sleep(450);
      await a.fly(pointOf(el.menuSaveAs, 0.3, 0.75), 500);
      el.menuSaveAs.classList.add("hot");
      a.pulse();
      log("point", "File ▸ Save As… (Notepad)");
      await a.say("Right here: Save As…, or press Ctrl+Shift+S.");
      await a.sleep(2600);
      el.fileMenu.classList.remove("open");
      el.menuFile.classList.remove("open");
      el.menuSaveAs.classList.remove("hot");
      a.hush();
      await a.home();
    },

    async explain(a) {
      await ensureNotepad(a);
      desk.highlight = true;
      renderDoc();
      const errorNode = $("#npError");
      const end = pointOf(errorNode, 1, 0.6);
      moveCursor(end.x + 4, end.y - 4);
      await a.sleep(650);
      await a.home();
      await a.listen("what does this error mean?");
      log("read", "highlighted text (via accessibility, not the clipboard)", "ok");
      await a.think(1100);
      await a.fly(pointOf(errorNode, 0.08, 0.9));
      a.pulse();
      log("point", "the error text (Notepad)");
      await a.say("Edge couldn't get online through your proxy. Check that it's running (like localhost:3128), then reload.");
      await a.sleep(3600);
      desk.highlight = false;
      renderDoc();
      a.hush();
      moveCursor(640, 318);
      await a.home();
    },

    async type(a) {
      await ensureNotepad(a);
      await a.listen("type my to-do list in Notepad");
      await a.think(800);
      await Promise.all([a.fly(pointOf($("#npCaret"), 0.5, 1)), a.say("On it. Typing in Notepad.")]);
      log("focus", "Notepad");
      setStatus("acting", "typing");
      const text = desk.typed
        ? "\n☐ more coffee ☕"
        : "\n\nto do:\n☐ email the Indonesia team\n☐ book a meeting room\n☐ coffee ☕";
      desk.dirty = true;
      for (const char of text) {
        a.alive();
        desk.typed += char;
        renderDoc();
        await a.sleep(char === "\n" ? 120 : 34);
      }
      log("type", `Notepad · ${text.trim().split("\n").length} line(s)`);
      await a.fly(pointOf($("#npCaret"), 0.5, 1), 300);
      await a.say("Done ✓");
      await a.sleep(1500);
      a.hush();
      await a.home();
    },

    async calc(a) {
      await a.listen("open Calculator");
      await a.think(700);
      if (desk.calcOpen) {
        await Promise.all([a.fly(pointOf(el.calc, 0.2, 0.08)), a.say("It's already open. Bringing it to the front.")]);
        log("focus", "Calculator");
      } else {
        await Promise.all([a.fly(pointOf(el.tbStart, 0.5, 0.5)), a.say("Opening Calculator.")]);
        el.tbStart.classList.add("hot");
        setStatus("acting", "opening");
        await a.sleep(500);
        el.tbStart.classList.remove("hot");
        showCalc(true);
        log("open", "Calculator");
        await a.sleep(300);
        await a.fly(pointOf(el.calc, 0.15, 0.06));
        a.pulse();
        await a.say("Calculator's open.");
      }
      await a.sleep(1800);
      a.hush();
      await a.home();
    },

    async close(a) {
      await ensureNotepad(a);
      await a.listen("close Notepad");
      await a.think(800);
      await Promise.all([a.fly(pointOf(el.npTitle, 0.42, 0.6)), a.say("Closing Notepad. Only Notepad.")]);
      setStatus("acting", "closing");
      await a.sleep(500);
      await closeNotepadFlow(a);
      await a.sleep(1200);
      a.hush();
      await a.home();
      if (!desk.notepadOpen) {
        offerReopen(a);
      }
    },

    async altf4(a) {
      await a.listen("press Alt+F4");
      await a.think(700);
      const front = desk.notepadOpen ? "Notepad" : desk.calcOpen ? "Calculator" : null;
      if (!front) {
        await a.say("Nothing's open. Alt+F4 on the desktop opens the shut-down box, so I'll skip it.");
        await a.sleep(2500);
        a.hush();
        return;
      }
      /* the ConfirmBox sits mid-screen; Buddy waits just below-left of it so
         neither the arrow nor the speech bubble covers the question */
      const box = { x: Math.round(STAGE_W / 2 - 130), y: 96 };
      el.confirm.style.left = `${box.x}px`;
      el.confirm.style.top = `${box.y}px`;
      await a.fly({ x: box.x - 28, y: box.y + 150 });
      el.confirmText.textContent = `Press Alt+F4? That closes the window in front: ${front}.`;
      el.confirm.classList.add("show");
      setStatus("waiting", "waiting for you");
      log("ask", "Press Alt+F4?", "ask");
      await a.say("That one's risky, so I'm asking first.");
      const yes = await a.choose([
        { node: el.confirmYes, value: true },
        { node: el.confirmNo, value: false },
      ]);
      el.confirm.classList.remove("show");
      if (!yes) {
        log("ask", "→ No. Nothing pressed.", "no");
        await a.say("Okay, I won't.");
        await a.sleep(1500);
        a.hush();
        return;
      }
      log("ask", "→ Yes", "ok");
      log("keys", `Alt+F4 → ${front}`);
      setStatus("acting", "pressing keys");
      if (front === "Notepad") {
        await closeNotepadFlow(a);
      } else {
        showCalc(false);
        await a.say("Calculator's closed.");
      }
      await a.sleep(1200);
      a.hush();
      await a.home();
      if (!desk.notepadOpen) {
        offerReopen(a);
      }
    },

    async notepad(a) {
      if (desk.notepadOpen) {
        await a.listen("open Notepad");
        await a.think(500);
        await a.say("Notepad's already open.");
        await a.sleep(1500);
        a.hush();
        return;
      }
      await a.listen("open Notepad");
      await a.think(600);
      await Promise.all([a.fly(pointOf(el.tbNotepad, 0.5, 0.4)), a.say("Opening Notepad.")]);
      a.pulse();
      desk.typed = "";
      desk.dirty = false;
      renderDoc();
      showNotepad(true);
      log("open", "Notepad");
      await a.sleep(1200);
      a.hush();
      await a.home();
    },

    async hello(a, text) {
      await a.listen(text, true);
      await a.think(400);
      await a.say("Hi! (◕‿◕) I'm Buddy. Try one of the things on the right.");
      await a.sleep(2800);
      a.hush();
    },

    async unknown(a, text) {
      await a.listen(text, true);
      await a.think(900);
      await a.say("The real Buddy can take a swing at that. This tiny demo only knows the tricks on the right, though.");
      await a.sleep(3800);
      a.hush();
    },
  };

  function offerReopen() {
    const [chip] = makeApi(token).followUps(["open Notepad"]);
    chip.addEventListener("click", () => start("notepad"));
  }

  function route(text) {
    const said = text.toLowerCase();
    if (/save ?as|where.*save/.test(said)) return "saveas";
    if (/alt\s*\+?\s*f4/.test(said)) return "altf4";
    if (/error|mean|explain|what('?s| is) (this|that)/.test(said)) return "explain";
    if (/calc/.test(said)) return "calc";
    if (/open.*note|^notepad/.test(said)) return "notepad";
    if (/close|quit|exit/.test(said)) return "close";
    if (/type|write|to-?do|list/.test(said)) return "type";
    if (/^(hi|hello|hey|yo)\b/.test(said)) return "hello";
    return "unknown";
  }

  let userTookOver = false;

  async function start(name, text = "", chip = null) {
    userTookOver = true;
    cancelAll();
    const myToken = token;
    const api = makeApi(myToken);
    if (chip) {
      chip.classList.add("running");
    }
    try {
      await scenarios[name](api, text);
      if (myToken === token) {
        setStatus("done", "done · your turn");
      }
    } catch (error) {
      if (error !== CANCEL) {
        console.error(error);
      }
    } finally {
      if (chip) {
        chip.classList.remove("running");
      }
    }
  }

  el.chips.addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (chip) {
      start(chip.dataset.run, "", chip);
    }
  });

  el.askForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = el.askInput.value.trim();
    if (!text) {
      return;
    }
    el.askInput.value = "";
    start(route(text), text);
  });

  /* autoplay a short tour the first time the demo scrolls into view */
  async function autoplay() {
    const myToken = token;
    const api = makeApi(myToken);
    try {
      for (const name of ["saveas", "explain", "type", "calc"]) {
        const chip = $(`.chip[data-run="${name}"]`);
        chip.classList.add("running");
        try {
          await scenarios[name](api);
        } finally {
          chip.classList.remove("running");
        }
        await api.sleep(700);
      }
      setStatus("done", "your turn → pick something");
      el.said.textContent = "your turn. Try “Close Notepad” or “Press Alt+F4”";
      el.said.classList.add("idle");
    } catch (error) {
      if (error !== CANCEL) {
        console.error(error);
      }
    }
  }

  const demoObserver = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting) && !userTookOver) {
      demoObserver.disconnect();
      setTimeout(() => {
        if (userTookOver) {
          return;
        }
        if (document.getElementById("heroVideo")) {
          // The hero clip already shows the tour, so here it's the visitor's turn
          setStatus("done", "your turn → pick something");
          el.said.textContent = "your turn. Try “Where's Save As?” or “Press Alt+F4”";
          el.said.classList.add("idle");
        } else {
          autoplay();
        }
      }, 600);
    }
  }, { threshold: 0.45 });
  demoObserver.observe(stageWrap);


  /* hero clip: respect reduced motion, and pause when it is off screen */
  const heroVideo = document.getElementById("heroVideo");
  if (heroVideo) {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      heroVideo.removeAttribute("autoplay");
      heroVideo.pause();
      heroVideo.controls = true;
    } else {
      new IntersectionObserver((entries) => {
        const visible = entries.some((entry) => entry.isIntersecting);
        if (visible) {
          heroVideo.play().catch(() => {
            heroVideo.controls = true;
          });
        } else {
          heroVideo.pause();
        }
      }, { threshold: 0.2 }).observe(heroVideo);
    }
  }

  /* initial desktop */
  renderDoc();
  showCalc(false);
  setVisible(el.saveDialog, false);
  moveCursor(cursor.x, cursor.y);
  placeBuddy(restSpot().x, restSpot().y);
  el.said.classList.add("idle");

  function tickClock() {
    const now = new Date();
    $("#clockTime").textContent = now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    $("#clockDate").textContent = now.toLocaleDateString("en-GB");
  }
  tickClock();
  setInterval(tickClock, 15000);

  /* =====================================================================
     The page buddy: a springy orange arrow that follows your real mouse
     ===================================================================== */
  const pageBuddy = $("#pageBuddy");
  const pageBubble = $(".pb-bubble", pageBuddy);
  const followToggle = $("#followToggle");
  let followOn = localStorage.getItem("buddy-follow") !== "off" && finePointer;
  const mouse = { x: -100, y: -100 };
  const spring = { x: -100, y: -100, vx: 0, vy: 0 };
  let seenMouse = false;

  function applyFollow() {
    pageBuddy.classList.toggle("off", !followOn);
    followToggle.setAttribute("aria-pressed", String(followOn));
    $("span", followToggle).textContent = followOn ? "buddy: on" : "buddy: off";
  }
  applyFollow();

  followToggle.addEventListener("click", () => {
    followOn = !followOn;
    localStorage.setItem("buddy-follow", followOn ? "on" : "off");
    applyFollow();
  });

  document.addEventListener("mousemove", (event) => {
    mouse.x = event.clientX + 14;
    mouse.y = event.clientY + 18;
    if (!seenMouse) {
      seenMouse = true;
      spring.x = mouse.x;
      spring.y = mouse.y;
    }
    const overStage = event.target.closest && event.target.closest("#stageWrap");
    pageBuddy.classList.toggle("away", Boolean(overStage));
  });
  document.addEventListener("mouseleave", () => pageBuddy.classList.add("away"));
  document.addEventListener("mouseenter", () => pageBuddy.classList.remove("away"));

  function springFrame() {
    spring.vx = (spring.vx + (mouse.x - spring.x) * 0.14) * 0.7;
    spring.vy = (spring.vy + (mouse.y - spring.y) * 0.14) * 0.7;
    spring.x += spring.vx;
    spring.y += spring.vy;
    const tilt = Math.max(-18, Math.min(18, spring.vx * 1.2));
    pageBuddy.style.transform = `translate(${spring.x}px, ${spring.y}px) rotate(${tilt}deg)`;
    requestAnimationFrame(springFrame);
  }
  if (finePointer) {
    requestAnimationFrame(springFrame);
  }

  document.addEventListener("mouseover", (event) => {
    const target = event.target.closest && event.target.closest("[data-say]");
    if (target) {
      pageBubble.textContent = target.dataset.say;
      const room = window.innerWidth - event.clientX;
      pageBubble.style.left = room < 280 ? "auto" : "26px";
      pageBubble.style.right = room < 280 ? "8px" : "auto";
      pageBuddy.classList.add("talk");
    }
  });
  document.addEventListener("mouseout", (event) => {
    const from = event.target.closest && event.target.closest("[data-say]");
    const to = event.relatedTarget && event.relatedTarget.closest && event.relatedTarget.closest("[data-say]");
    if (from && from !== to) {
      pageBuddy.classList.remove("talk");
    }
  });

  /* =====================================================================
     Look picker: the same colours and icons as Settings -> Look in the app
     ===================================================================== */
  const LOOK_COLORS = {
    Orange: "#ff8424", Blue: "#3b82f6", Green: "#22c55e", Pink: "#ec4899", Purple: "#8b5cf6",
    Teal: "#14b8a6", Red: "#ef4444", Yellow: "#facc15", White: "#ffffff", Black: "#1f2130",
  };
  const LOOK_ICONS = {
    arrow: { label: "Arrow", svg: '<polygon points="0,0 6,26 12,18 24,22" />' },
    pointer: { label: "Mouse pointer", svg: '<polygon points="0,0 0,23 6,18 10,27 14,25 10,16.5 17,16.5" />' },
    plane: { label: "Paper plane", svg: '<polygon points="0,0 26,9 13,13 9,26" /><path class="fold" d="M1 1 L13 13" />' },
    dot: { label: "Dot", svg: '<circle cx="9" cy="9" r="8" />' },
    star: { label: "Star", svg: '<polygon points="12,-1 15.2,7.6 24.4,8 17.2,13.7 19.7,22.5 12,17.4 4.3,22.5 6.8,13.7 -0.4,8 8.8,7.6" />' },
    heart: { label: "Heart", svg: '<path d="M12 23 C3 16 -1 11 1.5 5.5 C3.8 0.5 10 0.8 12 5.6 C14 0.8 20.2 0.5 22.5 5.5 C25 11 21 16 12 23 Z" />' },
  };
  const lookColors = $("#lookColors");
  const lookIcons = $("#lookIcons");
  const pbShape = $("#pbShape");
  const look = {
    color: localStorage.getItem("buddy-color") || LOOK_COLORS.Orange,
    icon: localStorage.getItem("buddy-icon") || "arrow",
  };

  function applyLook() {
    const dark = look.color === LOOK_COLORS.Black;
    pageBuddy.style.setProperty("--pb-color", look.color);
    pageBuddy.style.setProperty("--pb-stroke", dark ? "#ffffff" : "var(--ink)");
    pbShape.innerHTML = (LOOK_ICONS[look.icon] || LOOK_ICONS.arrow).svg;
    $$(".swatch", lookColors).forEach((swatch) => {
      swatch.setAttribute("aria-pressed", String(swatch.dataset.color === look.color));
    });
    $$(".icon-pick", lookIcons).forEach((pick) => {
      pick.setAttribute("aria-pressed", String(pick.dataset.icon === look.icon));
      pick.style.setProperty("--pick-color", look.color);
    });
    localStorage.setItem("buddy-color", look.color);
    localStorage.setItem("buddy-icon", look.icon);
  }

  if (lookColors && lookIcons) {
    Object.entries(LOOK_COLORS).forEach(([name, color]) => {
      const swatch = document.createElement("button");
      swatch.type = "button";
      swatch.className = "swatch";
      swatch.dataset.color = color;
      swatch.title = name;
      swatch.setAttribute("aria-label", name);
      swatch.style.background = color;
      swatch.addEventListener("click", () => {
        look.color = color;
        applyLook();
      });
      lookColors.appendChild(swatch);
    });
    Object.entries(LOOK_ICONS).forEach(([id, icon]) => {
      const pick = document.createElement("button");
      pick.type = "button";
      pick.className = "icon-pick";
      pick.dataset.icon = id;
      pick.title = icon.label;
      pick.setAttribute("aria-label", icon.label);
      pick.innerHTML = `<svg viewBox="-3 -3 32 32" width="24" height="24">${icon.svg}</svg>`;
      pick.addEventListener("click", () => {
        look.icon = id;
        applyLook();
      });
      lookIcons.appendChild(pick);
    });
    applyLook();
  }

  /* =====================================================================
     Voices
     ===================================================================== */
  const VOICES = [
    { file: "en-us-heart.wav", lang: "English · US", name: "Heart", line: "Hi! Click the blue Save button at the top left." },
    { file: "en-us-michael.wav", lang: "English · US", name: "Michael", line: "Hi! Click the blue Save button at the top left." },
    { file: "en-gb-emma.wav", lang: "English · UK", name: "Emma", line: "Hi! Click the blue Save button at the top left." },
    { file: "en-gb-george.wav", lang: "English · UK", name: "George", line: "Right, click the blue Save button at the top." },
    { file: "zh-xiaoyi.wav", lang: "中文 · Mandarin", name: "Xiaoyi", line: "你好，我是Buddy。這就是我的聲音。" },
    { file: "zh-yunxi.wav", lang: "中文 · Mandarin", name: "Yunxi", line: "好的，我已經幫你打開Notepad了。" },
    { file: "es-dora.wav", lang: "Español", name: "Dora", line: "Hola, soy Buddy. Así es como sueno." },
    { file: "fr-siwis.wav", lang: "Français", name: "Siwis", line: "Bonjour, je suis Buddy. Voici ma voix." },
    { file: "it-nicola.wav", lang: "Italiano", name: "Nicola", line: "Ciao, sono Buddy. Questa è la mia voce." },
    { file: "pt-alex.wav", lang: "Português · BR", name: "Alex", line: "Olá, eu sou o Buddy. Esta é a minha voz." },
    { file: "hi-omega.wav", lang: "हिन्दी · Hindi", name: "Omega", line: "नमस्ते, मैं बडी हूँ। मेरी आवाज़ ऐसी है।" },
  ];
  const PLAY_ICON = '<svg viewBox="0 0 16 16"><path fill="currentColor" d="M4 2.5v11l9-5.5z"/></svg>';
  const STOP_ICON = '<svg viewBox="0 0 16 16"><rect fill="currentColor" x="3.5" y="3.5" width="9" height="9" rx="1.5"/></svg>';
  const voiceGrid = $("#voiceGrid");
  const player = new Audio();
  let playingCard = null;

  function stopVoice() {
    player.pause();
    if (playingCard) {
      playingCard.classList.remove("playing");
      $(".play", playingCard).innerHTML = PLAY_ICON;
      playingCard = null;
    }
  }

  VOICES.forEach((voice) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "voice";
    card.innerHTML = `
      <span class="play">${PLAY_ICON}</span>
      <span class="meta">
        <span class="lang"></span>
        <span class="name"></span>
        <span class="line"></span>
      </span>
      <span class="eq">${"<i></i>".repeat(7)}</span>`;
    $(".lang", card).textContent = voice.lang;
    $(".name", card).textContent = voice.name;
    $(".line", card).textContent = voice.line;
    card.title = voice.line;
    card.addEventListener("click", () => {
      const wasThis = playingCard === card;
      stopVoice();
      if (wasThis) {
        return;
      }
      player.src = `voices/${voice.file}`;
      player.currentTime = 0;
      player.play().catch(() => stopVoice());
      playingCard = card;
      card.classList.add("playing");
      $(".play", card).innerHTML = STOP_ICON;
    });
    voiceGrid.appendChild(card);
  });
  player.addEventListener("ended", stopVoice);

  /* =====================================================================
     Brains
     ===================================================================== */
  const BRAINS = {
    free: {
      speed: "~5 s",
      race: "3s",
      where: "Buddy's free cloud brain (Llama 4 Scout on Cloudflare Workers AI)",
      leaves: "The screenshot and your question, to Cloudflare to answer. Not stored.",
      best: "Trying Buddy right now, on Windows or Mac",
      needs: "Nothing. Shared by everyone, so there's a daily limit",
    },
    smart: {
      speed: "~1–2 s",
      race: "1.2s",
      where: "your company's AI gateway (vision-model)",
      leaves: "The screenshot and your question, to the company gateway only",
      best: "Everyday use at work. Snappy and much smarter.",
      needs: "Your company MAAS login, on the company network",
    },
    gpu: {
      speed: "~5–15 s",
      race: "4s",
      where: "Your own PC's graphics chip, through Ollama",
      leaves: "Nothing. Everything stays on this PC.",
      best: "Private stuff, or working without the company network",
      needs: "Ollama with a small vision model (for example qwen3-vl:4b)",
    },
    cpu: {
      speed: "~1 min",
      race: "9s",
      where: "Your PC's processor, through Ollama",
      leaves: "Nothing. Everything stays on this PC.",
      best: "PCs without a usable GPU",
      needs: "Ollama, and a little patience ☕",
    },
  };
  const racer = $("#racer");

  function showBrain(name) {
    const brain = BRAINS[name];
    $$(".brain-tabs button").forEach((tab) => {
      const active = tab.dataset.brain === name;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    $("#brainSpeed").textContent = brain.speed;
    $("#brainWhere").textContent = brain.where;
    $("#brainLeaves").textContent = brain.leaves;
    $("#brainBest").textContent = brain.best;
    $("#brainNeeds").textContent = brain.needs;
    racer.classList.remove("go");
    void racer.offsetWidth;
    racer.style.setProperty("--race", brain.race);
    racer.classList.add("go");
  }
  $$(".brain-tabs button").forEach((tab) => tab.addEventListener("click", () => showBrain(tab.dataset.brain)));

  const brainObserver = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) {
      brainObserver.disconnect();
      showBrain("free");
    }
  }, { threshold: 0.5 });
  brainObserver.observe($(".brain-box"));

  /* =====================================================================
     A taste of KeyIt: a tiny synthesized clack
     ===================================================================== */
  const clackPad = $("#clackPad");
  let audioContext = null;

  function clack() {
    audioContext = audioContext || new (window.AudioContext || window.webkitAudioContext)();
    const ctx = audioContext;
    const length = Math.floor(ctx.sampleRate * 0.05);
    const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < length; i += 1) {
      data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, 6);
    }
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    const filter = ctx.createBiquadFilter();
    filter.type = "bandpass";
    filter.frequency.value = 1800 + Math.random() * 1600;
    filter.Q.value = 1.4;
    const gain = ctx.createGain();
    gain.gain.value = 0.7;
    source.connect(filter).connect(gain).connect(ctx.destination);
    source.start();
    clackPad.classList.add("hit");
    setTimeout(() => clackPad.classList.remove("hit"), 80);
  }
  clackPad.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") {
      event.preventDefault();
      clack();
    }
  });
  clackPad.addEventListener("click", clack);
})();
