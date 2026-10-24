// OpenTutor client: Finish dialog on every page; on the chat page it sends messages, reads the
// NDJSON event stream and renders it in the prototype's layout. Model output and nicknames are
// only ever inserted as text (textContent), never as HTML.
"use strict";
(function () {
  // Finish dialog (prototype: "Call it a day?"). Falls back to confirm() without <dialog>.
  const finish = document.getElementById("finish-dialog");
  document.querySelectorAll("[data-open-finish]").forEach((button) => button.addEventListener("click", () => {
    if (finish && typeof finish.showModal === "function") finish.showModal();
    else if (window.confirm("Everything will be deleted. This cannot be undone.")) finish.querySelector("form").submit();
  }));
  const newDialog = document.getElementById("new-dialog");
  document.querySelectorAll("[data-open-new]").forEach((button) => button.addEventListener("click", () => {
    if (typeof newDialog.showModal === "function") newDialog.showModal();
    else if (window.confirm("Start a new exercise? This conversation will be cleared.")) newDialog.querySelector("form").submit();
  }));
  document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));

  const form = document.getElementById("ask");
  if (!form) return;
  const log = document.getElementById("log");
  const status = document.getElementById("status");
  const text = document.getElementById("text");
  const count = document.getElementById("count");
  const imageInput = document.getElementById("image");
  const cameraInput = document.getElementById("camera");
  const preview = document.getElementById("photo-preview");
  const queueBox = document.getElementById("queue");
  const practiceBox = document.getElementById("practice");
  const ladder = document.getElementById("ladder");
  const desk = document.getElementById("desk");
  const announcer = document.getElementById("announce");
  const hintLine = document.getElementById("hint-line");
  const checkChip = document.getElementById("check-chip");
  const coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
  let lastRequest = null;
  const nickname = form.dataset.nickname;
  const VERDICTS = { correct: "Correct", partly_correct: "Partly correct", not_yet_correct: "Not yet correct" };
  let hintLevel = Number(form.dataset.hintLevel || 0);
  let photo = null;
  let checking = false;
  let busyNow = false;

  function iconFrom(id) {
    return document.getElementById(id).content.firstElementChild.cloneNode(true);
  }

  // Read aloud (REQ-018): the phone's own text-to-speech, on-device English voices only,
  // so no reply text leaves the phone. Nothing is sent to the hub.
  const speech = (function () {
    const synth = window.speechSynthesis;
    let voice = null;
    let playing = null;

    function pickVoice() {
      if (!synth) return;
      const local = synth.getVoices().filter((v) => v.localService && /^en([-_]|$)/i.test(v.lang));
      voice = local.find((v) => /^en[-_](GB|US)/i.test(v.lang)) || local[0] || null;
    }

    function words(value) {
      return value
        .replace(/\*\*/g, "")
        .replace(/sqrt\(/g, "square root of (")
        .replace(/\^2(?!\d)/g, " squared")
        .replace(/\^3(?!\d)/g, " cubed")
        .replace(/\^/g, " to the power of ")
        .replace(/(\d)\s*\/\s*(\d)/g, "$1 over $2")
        .replace(/\*/g, " times ")
        .replace(/\s-\s/g, " minus ")
        .replace(/=/g, " equals ");
    }

    function label(button, speaking) {
      button.replaceChildren(iconFrom(speaking ? "tpl-stop" : "tpl-volume"), document.createTextNode(speaking ? "Stop" : "Read aloud"));
    }

    function toggle(button, value) {
      const wasMine = playing === button;
      synth.cancel();
      if (playing) label(playing, false);
      playing = null;
      if (wasMine) return;
      if (!voice) {
        // Never fall back to an online voice: the reply text would leave the phone (AC-018.3).
        notice("This phone has no offline English voice yet. Add one in your phone’s settings (Text-to-speech or Spoken content), then tap Read aloud again.");
        return;
      }
      const utterance = new SpeechSynthesisUtterance(words(value));
      utterance.voice = voice;
      utterance.lang = voice.lang;
      utterance.onend = utterance.onerror = () => { if (playing === button) { label(button, false); playing = null; } };
      playing = button;
      label(button, true);
      synth.speak(utterance);
    }

    function attach(textEl) {
      if (!synth || textEl.dataset.speak) return;
      textEl.dataset.speak = "1";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "speak";
      label(button, false);
      button.addEventListener("click", () => toggle(button, textEl.textContent));
      textEl.after(button);
    }

    if (synth) {
      pickVoice();
      synth.addEventListener("voiceschanged", pickVoice);
    }
    return { attach };
  })();

  function scrollDown() {
    log.scrollTop = log.scrollHeight;
    const last = log.lastElementChild;
    // On phones the page scrolls, not the log: bring the newest turn into view.
    if (last && last.getBoundingClientRect().bottom > window.innerHeight) last.scrollIntoView({ block: "nearest" });
  }

  function announce(message) {
    announcer.textContent = "";
    window.setTimeout(() => { announcer.textContent = message; }, 50);
  }

  function clearEmpty() {
    const empty = document.getElementById("empty");
    if (empty) empty.remove();
  }

  // One conversation turn in the prototype layout; returns its text paragraph.
  function turn(role, content, options) {
    clearEmpty();
    const wrap = document.createElement("div");
    wrap.className = "ot-turn " + role;
    if (role === "tutor") {
      const avatar = document.createElement("span");
      avatar.className = "ot-turn-avatar";
      avatar.setAttribute("aria-hidden", "true");
      avatar.appendChild(iconFrom("tpl-avatar"));
      wrap.appendChild(avatar);
    }
    const body = document.createElement("div");
    const name = document.createElement("span");
    name.className = "ot-turn-name";
    name.textContent = role === "tutor" ? "OpenTutor" : nickname;
    body.appendChild(name);
    if (options && options.verdict) {
      const verdict = document.createElement("div");
      verdict.className = "ot-verdict " + options.verdict;
      verdict.appendChild(iconFrom("tpl-" + options.verdict));
      verdict.appendChild(document.createTextNode((options.prefix || "") + VERDICTS[options.verdict]));
      body.appendChild(verdict);
    }
    if (options && options.image) {
      const img = document.createElement("img");
      img.className = "ot-turn-photo";
      img.alt = "Your exercise photo";
      img.src = options.image;
      body.appendChild(img);
    }
    const p = document.createElement("p");
    p.className = "ot-turn-text";
    if (content) p.textContent = content;
    body.appendChild(p);
    wrap.appendChild(body);
    log.appendChild(wrap);
    if (role === "tutor" && content) speech.attach(p);
    scrollDown();
    return p;
  }

  function notice(message, retry) {
    clearEmpty();
    const lastBox = log.lastElementChild;
    if (!retry && lastBox && lastBox.classList.contains("ot-notice") && lastBox.textContent === message) return;
    const box = document.createElement("div");
    box.className = "ot-notice";
    box.setAttribute("role", "status");
    const p = document.createElement("p");
    p.textContent = message;
    box.appendChild(p);
    if (retry) {
      const again = document.createElement("button");
      again.type = "button";
      again.className = "btn btn-outline";
      again.textContent = "Try again";
      again.addEventListener("click", () => { if (!busyNow) { box.remove(); retry(); } });
      box.appendChild(again);
    }
    log.appendChild(box);
    announce(message);
    scrollDown();
  }

  function setLadder(level) {
    hintLevel = level;
    if (!ladder) return;
    let current = "";
    ladder.querySelectorAll("li").forEach((li) => {
      const done = Number(li.dataset.level) <= level;
      li.classList.toggle("done", done);
      li.querySelector(".ladder-done").textContent = done ? " (done)" : "";
      if (done) current = li.dataset.label;
    });
    hintLine.textContent = level > 0 ? "Hint " + Math.min(level, 4) + " of 4: " + current : "";
  }

  function updateActions() {
    const ready = hasExercise();
    document.querySelectorAll("#actions [data-intent]").forEach((b) => b.setAttribute("aria-disabled", ready && !busyNow ? "false" : "true"));
  }

  function setDesk(value) {
    if (!desk) return;
    desk.hidden = !value;
    document.getElementById("desk-text").textContent = value;
    updateActions();
  }

  function busy(on) {
    // aria-disabled instead of disabled: the focused button keeps focus during the request.
    busyNow = on;
    document.querySelectorAll("#ask button, #practice button, [data-example]").forEach((b) => b.setAttribute("aria-disabled", on ? "true" : "false"));
    log.setAttribute("aria-busy", on ? "true" : "false");
    updateActions();
  }

  function queueMessage(position) {
    return position === 1 ? "You’re next in line." : "You’re number " + position + " in line.";
  }

  function updateCount() {
    count.textContent = text.value.length.toLocaleString("en") + " / 2,000";
  }

  function setChecking(on) {
    checking = on;
    text.placeholder = on ? "Write your answer or working…" : "What are you working on?";
    checkChip.hidden = !on;
  }

  // Downscale photos on the phone before upload: faster on hub Wi-Fi, still readable.
  async function shrink(file) {
    try {
      const bitmap = await createImageBitmap(file);
      const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(bitmap.width * scale);
      canvas.height = Math.round(bitmap.height * scale);
      canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      return await new Promise((resolve) => canvas.toBlob((b) => resolve(b || file), "image/jpeg", 0.85));
    } catch (e) {
      return file;
    }
  }

  function choosePhoto(input) {
    const file = input.files[0];
    input.value = "";
    if (!file) return;
    if (photo) URL.revokeObjectURL(photo.url);
    photo = { file: file, url: URL.createObjectURL(file) };
    document.getElementById("photo-thumb").src = photo.url;
    document.getElementById("photo-name").textContent = file.name || "Photo";
    preview.hidden = false;
    announce("Photo added. Tap send when you’re ready.");
  }

  function clearPhoto() {
    photo = null;
    preview.hidden = true;
  }

  function restorePhoto(file, handwriting) {
    photo = { file: file, url: URL.createObjectURL(file), handwriting: !!handwriting };
    document.getElementById("photo-thumb").src = photo.url;
    document.getElementById("photo-name").textContent = handwriting ? "Your writing" : file.name || "Photo";
    preview.hidden = false;
  }

  function renderPractice(exercises) {
    practiceBox.replaceChildren();
    const title = document.createElement("div");
    title.className = "ot-section-title";
    const h2 = document.createElement("h2");
    h2.textContent = "Practice: 3 to try";
    const kicker = document.createElement("span");
    kicker.textContent = "Same idea, new numbers";
    title.append(h2, kicker);
    practiceBox.appendChild(title);
    exercises.forEach((exercise) => {
      const item = document.createElement("div");
      item.className = "ot-practice-item";
      item.dataset.number = exercise.number;
      const number = document.createElement("span");
      number.className = "ot-practice-number";
      number.textContent = "0" + exercise.number;
      const body = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = exercise.text;
      const answerForm = document.createElement("form");
      const input = document.createElement("input");
      input.setAttribute("aria-label", "Answer to practice " + exercise.number);
      input.placeholder = "Your answer…";
      input.maxLength = 300;
      const submit = document.createElement("button");
      submit.type = "submit";
      submit.className = "btn btn-ghost btn-icon";
      submit.setAttribute("aria-label", "Check practice " + exercise.number);
      submit.appendChild(iconFrom("tpl-check"));
      answerForm.append(input, submit);
      // Numbered answers are checked one by one on the hub (AC-007.2).
      answerForm.addEventListener("submit", (e) => {
        e.preventDefault();
        if (!input.value.trim() || busyNow) return;
        send("check", exercise.number + ". " + input.value.trim(), null, { practice: true });
      });
      const result = document.createElement("small");
      body.append(strong, answerForm, result);
      item.append(number, body);
      practiceBox.appendChild(item);
    });
    practiceBox.hidden = false;
  }

  // Writing pad (REQ-019): finger, stylus or Apple Pencil via pointer events. "Use" turns
  // the writing into an image on white, attached like a photo but flagged as handwriting.
  const pad = (function () {
    const box = document.getElementById("pad");
    const canvas = document.getElementById("pad-canvas");
    const ctx = canvas.getContext("2d");
    const marksBox = document.getElementById("pad-marks");
    const readingList = document.getElementById("pad-reading");
    const padStatus = document.getElementById("pad-status");
    const ROW = 52; // CSS px between the ruled lines (matches the pad background)
    const PAUSE_MS = 1800;
    const MARK_GLYPH = { correct: "✓", wrong: "✗", unknown: "?" };
    const MARK_WORDS = { correct: "correct", wrong: "check this step", unknown: "can’t tell yet" };
    let strokes = [];
    let current = null;
    let version = 0;
    let timer = null;
    let pending = null;

    function size() {
      const ratio = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.round(rect.width * ratio);
      canvas.height = Math.round(rect.height * ratio);
      redraw();
    }

    function redraw(target) {
      const c = target || ctx;
      const w = c.canvas.width;
      const h = c.canvas.height;
      if (target) { c.fillStyle = "#ffffff"; c.fillRect(0, 0, w, h); } else c.clearRect(0, 0, w, h);
      c.strokeStyle = "#1b231d";
      c.lineCap = "round";
      c.lineJoin = "round";
      c.lineWidth = Math.max(2.5, w / 220);
      strokes.forEach((stroke) => {
        c.beginPath();
        stroke.forEach((pt, i) => { const x = pt[0] * w; const y = pt[1] * h; if (i === 0) c.moveTo(x, y); else c.lineTo(x, y); });
        if (stroke.length === 1) c.lineTo(stroke[0][0] * w + 0.1, stroke[0][1] * h);
        c.stroke();
      });
    }

    // Live marking (AC-019.6): after a pause the hub reads the pad and marks each line,
    // the way ipad-tutor marks a page. Strokes are grouped by the ruled line they sit on.
    function rows() {
      const height = canvas.getBoundingClientRect().height;
      const byRow = new Map();
      strokes.forEach((stroke) => {
        const meanY = stroke.reduce((sum, pt) => sum + pt[1], 0) / stroke.length;
        const row = Math.floor((meanY * height) / ROW);
        const maxX = Math.max(...stroke.map((pt) => pt[0]));
        byRow.set(row, Math.max(byRow.get(row) || 0, maxX));
      });
      return [...byRow.entries()].sort((a, b) => a[0] - b[0]).map(([row, maxX]) => ({ row: row, maxX: maxX }));
    }

    function clearMarks(message) {
      marksBox.replaceChildren();
      readingList.replaceChildren();
      padStatus.textContent = message || "";
    }

    function schedule() {
      window.clearTimeout(timer);
      if (strokes.length) timer = window.setTimeout(liveCheck, PAUSE_MS);
    }

    function showMarks(event) {
      if (!event.readable) { padStatus.textContent = "I can’t read that yet. Try writing a little bigger."; return; }
      const width = canvas.getBoundingClientRect().width;
      const lineRows = rows();
      event.lines.forEach((line, i) => {
        const place = lineRows[i];
        if (place) {
          const mark = document.createElement("span");
          mark.className = "ot-mark " + line.mark;
          mark.textContent = MARK_GLYPH[line.mark];
          mark.style.left = Math.min(place.maxX * width + 10, width - 34) + "px";
          mark.style.top = place.row * ROW + ROW / 2 + "px";
          marksBox.appendChild(mark);
        }
        const item = document.createElement("li");
        item.className = line.mark;
        item.textContent = line.text + " — " + MARK_WORDS[line.mark];
        readingList.appendChild(item);
      });
      const marks = event.lines.map((l) => l.mark);
      padStatus.textContent = marks.includes("wrong")
        ? "Look again at the line marked ✗. Tap Another hint if you’re stuck."
        : event.verdict === "correct" ? "All correct. Well done!"
        : event.verdict === "partly_correct" ? "Good so far. Keep going."
        : "Keep going. I’ll mark each line when you pause.";
      announce(padStatus.textContent);
    }

    async function liveCheck() {
      if (box.hidden || !strokes.length) return;
      if (!hasExercise()) { clearMarks("Send the exercise first, then write your working here and I’ll mark each line."); return; }
      if (busyNow || pending) { schedule(); return; }
      const asked = version;
      padStatus.textContent = "Reading your writing…";
      pending = (async () => {
        try {
          const out = document.createElement("canvas");
          out.width = canvas.width;
          out.height = canvas.height;
          redraw(out.getContext("2d"));
          const blob = await new Promise((resolve) => out.toBlob(resolve, "image/png"));
          const data = new FormData();
          data.append("intent", "mark");
          data.append("handwriting", "1");
          data.append("image", blob, "pad.png");
          const res = await fetch("/api/messages", { method: "POST", body: data });
          if (!res.ok) {
            if (asked !== version) return;
            if (res.status === 409) { padStatus.textContent = ""; schedule(); } // busy: try again after the next pause
            else padStatus.textContent = "Couldn’t check right now. Keep writing.";
            return;
          }
          const lines = (await res.text()).split("\n").filter(Boolean).map((l) => JSON.parse(l));
          if (asked !== version) return; // the student kept writing: these marks are stale
          clearMarks();
          lines.forEach((ev) => {
            if (ev.type === "marks") showMarks(ev);
            else if (ev.type === "error" || ev.type === "notice") padStatus.textContent = ev.message;
          });
        } catch (e) {
          if (asked === version) padStatus.textContent = "Couldn’t check right now. Keep writing.";
        } finally {
          pending = null;
        }
      })();
      return pending;
    }

    function point(e) {
      const rect = canvas.getBoundingClientRect();
      return [(e.clientX - rect.left) / rect.width, (e.clientY - rect.top) / rect.height];
    }

    canvas.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      canvas.setPointerCapture(e.pointerId);
      window.clearTimeout(timer);
      version++;
      clearMarks();
      current = [point(e)];
      strokes.push(current);
      redraw();
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!current) return;
      (e.getCoalescedEvents ? e.getCoalescedEvents() : [e]).forEach((ev) => current.push(point(ev)));
      redraw();
    });
    const end = () => { if (current) schedule(); current = null; };
    canvas.addEventListener("pointerup", end);
    canvas.addEventListener("pointercancel", end);

    function open() {
      box.hidden = false;
      size();
      clearMarks(hasExercise() ? "" : "Send the exercise first, then write your working here and I’ll mark each line.");
      box.scrollIntoView({ block: "nearest" });
      announce("Writing pad open. Write one step per line; I mark each line when you pause. Tap Send to chat to ask about it.");
    }

    function close() {
      box.hidden = true;
      strokes = [];
      current = null;
      version++;
      window.clearTimeout(timer);
      clearMarks();
    }

    function use() {
      if (!strokes.length) { announce("The pad is empty. Write something first."); return; }
      const out = document.createElement("canvas");
      out.width = canvas.width;
      out.height = canvas.height;
      redraw(out.getContext("2d"));
      out.toBlob((blob) => {
        const file = new File([blob], "handwriting.png", { type: "image/png" });
        restorePhoto(file, true);
        close();
        announce("Your writing is attached. Tap send, or Check my answer.");
        text.focus();
      }, "image/png");
    }

    document.getElementById("pad-undo").addEventListener("click", () => { strokes.pop(); version++; clearMarks(); redraw(); schedule(); });
    document.getElementById("pad-clear").addEventListener("click", () => { strokes = []; version++; clearMarks(); redraw(); window.clearTimeout(timer); });
    document.getElementById("pad-cancel").addEventListener("click", close);
    document.getElementById("pad-use").addEventListener("click", use);
    window.addEventListener("resize", () => { if (!box.hidden) { size(); version++; clearMarks(); schedule(); } });
    return { open: open, whenIdle: () => pending || Promise.resolve() };
  })();

  // Speak instead of typing (REQ-020): the browser's speech recognition where this page may
  // use it, otherwise the keyboard's own microphone key. Audio never reaches the hub.
  const dictation = (function () {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const buttons = document.querySelectorAll("[data-speak]");
    let recognition = null;
    let listening = false;
    let told = false;
    try { told = window.sessionStorage.getItem("ot-speak-told") === "1"; } catch (e) { told = false; }

    function setListening(on) {
      listening = on;
      buttons.forEach((b) => {
        b.classList.toggle("listening", on);
        if (b.id === "speak") b.setAttribute("aria-label", on ? "Stop listening" : "Speak");
      });
      if (on) {
        const dot = document.createElement("span");
        dot.className = "ot-live-dot";
        status.replaceChildren(dot, document.createTextNode("Listening… tap the microphone again when you’ve finished."));
        announce("Listening.");
      } else if (!busyNow) {
        status.replaceChildren();
      }
    }

    function keyboardInstead() {
      text.focus();
      notice("Tap the microphone on your keyboard to speak, then check the words and send.");
    }

    function toggle() {
      if (busyNow) return;
      if (!told) {
        told = true;
        try { window.sessionStorage.setItem("ot-speak-told", "1"); } catch (e) { /* private mode */ }
        notice("Your phone turns your voice into text (on some phones that uses the internet). OpenTutor never records your voice.");
      }
      if (listening && recognition) { recognition.stop(); return; }
      if (!Recognition) { keyboardInstead(); return; }
      recognition = new Recognition();
      recognition.lang = /^en/i.test(navigator.language || "") ? navigator.language : "en-GB";
      recognition.interimResults = true;
      recognition.continuous = false;
      const before = text.value.trim() ? text.value.trim() + " " : "";
      recognition.onresult = (e) => {
        let words = "";
        for (let i = 0; i < e.results.length; i++) words += e.results[i][0].transcript;
        text.value = (before + words).slice(0, 2000);
        updateCount();
      };
      recognition.onerror = (e) => {
        setListening(false);
        if (e.error === "no-speech") notice("I didn’t hear anything. Tap the microphone and try again.");
        else if (e.error !== "aborted") keyboardInstead();
      };
      recognition.onend = () => { setListening(false); if (text.value.trim()) text.focus(); };
      try { recognition.start(); setListening(true); } catch (e) { keyboardInstead(); }
    }

    buttons.forEach((b) => b.addEventListener("click", toggle));
    return { listening: () => listening };
  })();

  function practiceResult(number, verdict) {
    const item = practiceBox.querySelector('[data-number="' + number + '"]');
    if (!item) return;
    item.classList.toggle("solved", verdict === "correct");
    item.querySelector("small").textContent = verdict === "correct" ? "Correct. Nice work!" : "Not yet. Try again — you’ve got this.";
  }

  const HTTP_MESSAGES = {
    401: "Your session has ended. Go back to the start page to join again.",
    409: "Please wait for the current answer first.",
    413: "That photo is too big. Send a JPEG, PNG or WebP photo under 10 MB.",
  };

  async function send(intent, message, file, options) {
    if (busyNow) return;
    busy(true);
    await pad.whenIdle(); // a live pad check may be in flight: one request at a time
    const practice = options && options.practice;
    lastRequest = { intent: intent, message: message, file: file, options: options };
    const data = new FormData();
    data.append("intent", intent);
    data.append("text", message);
    if (practice) data.append("practice", "1");
    if (options && options.handwriting) data.append("handwriting", "1");
    const imageUrl = file ? URL.createObjectURL(file) : null;
    if (message) turn("student", (intent === "check" && !practice ? "My answer: " : "") + message, imageUrl ? { image: imageUrl } : null);
    else if (file) turn("student", "", { image: imageUrl });
    let state = null;
    let failed = false;
    status.replaceChildren();
    let reply = null;
    let ok = false;
    try {
      if (file) data.append("image", await shrink(file), "photo.jpg");
      const res = await fetch("/api/messages", { method: "POST", body: data });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        failed = res.status !== 401 && res.status !== 400;
        notice(HTTP_MESSAGES[res.status] || body.detail || "Something went wrong, please try again.");
        if (res.status === 401) window.setTimeout(() => { window.location.href = "/"; }, 2500);
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, nl);
          buffer = buffer.slice(nl + 1);
          if (!line) continue;
          const ev = JSON.parse(line);
          if (ev.type === "queued") {
            if (queueBox.hidden) announce(queueMessage(ev.position) + " Your place is saved.");
            queueBox.hidden = false;
            document.getElementById("queue-number").textContent = "#" + ev.position;
            document.getElementById("queue-message").textContent = queueMessage(ev.position);
          } else if (ev.type === "started") {
            queueBox.hidden = true;
            const dot = document.createElement("span");
            dot.className = "ot-live-dot";
            status.replaceChildren(dot, document.createTextNode("Thinking with you…"));
          } else if (ev.type === "token") {
            if (!reply) { reply = turn("tutor", ""); status.replaceChildren(); }
            reply.textContent += ev.text;
            scrollDown();
          } else if (ev.type === "verdict") {
            const prefix = ev.practice_number ? "Exercise " + ev.practice_number + ": " : "";
            const lines = [ev.explanation, ev.first_error_step ? "First step to fix: " + ev.first_error_step : "", ev.hint ? "Hint: " + ev.hint : ""];
            turn("tutor", lines.filter(Boolean).join("\n"), { verdict: ev.verdict, prefix: prefix });
            if (ev.practice_number) practiceResult(ev.practice_number, ev.verdict);
          } else if (ev.type === "practice") {
            turn("tutor", "Here are 3 to try. Answer them below — I’ll check each one.");
            renderPractice(ev.exercises);
          } else if (ev.type === "reading") {
            const p = turn("tutor", "I read your writing as:\n" + ev.text);
            p.classList.add("ot-reading");
          } else if (ev.type === "notice") {
            notice(ev.message);
          } else if (ev.type === "error") {
            failed = !!ev.retryable;
            if (!failed) notice(ev.message);
          } else if (ev.type === "state") {
            state = ev;
          } else if (ev.type === "done") {
            ok = true;
          }
        }
      }
    } catch (e) {
      failed = true;
    } finally {
      if (reply) speech.attach(reply);
      queueBox.hidden = true;
      status.replaceChildren();
      busy(false);
      if (failed) {
        // Keep what the student wrote: offer the same request again (AC-003.3).
        const again = lastRequest;
        notice("Something went wrong — your message wasn’t lost. Check you’re on the hub Wi-Fi, then try again.",
          () => send(again.intent, again.message, again.file, again.options));
      } else if (reply) {
        announce("Reply ready.");
      }
      if (state) {
        const previous = desk ? document.getElementById("desk-text").textContent : "";
        if (!state.practice) practiceBox.hidden = true; // the hub cleared practice (new exercise)
        setLadder(intent === "answer" ? 4 : state.hint_level);
        setDesk(state.exercise || "");
      }
    }
  }

  function submit() {
    const message = text.value.trim();
    if (busyNow || (!message && !photo)) return;
    const intent = checking ? "check" : "ask";
    const file = photo ? photo.file : null;
    const options = photo && photo.handwriting ? { handwriting: true } : undefined;
    text.value = "";
    updateCount();
    setChecking(false);
    clearPhoto();
    send(intent, message, file, options);
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); if (!busyNow) submit(); });
  text.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !coarse) { e.preventDefault(); submit(); }
  });
  text.addEventListener("input", updateCount);
  imageInput.addEventListener("change", () => choosePhoto(imageInput));
  cameraInput.addEventListener("change", () => choosePhoto(cameraInput));
  document.getElementById("photo-remove").addEventListener("click", () => { clearPhoto(); announce("Photo removed."); });
  document.querySelectorAll("[data-open-pad]").forEach((b) => b.addEventListener("click", () => { if (!busyNow) pad.open(); }));
  document.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => document.getElementById(b.dataset.pick).click()));
  document.getElementById("check-cancel").addEventListener("click", () => { setChecking(false); text.focus(); });
  document.querySelectorAll("[data-example]").forEach((b) => b.addEventListener("click", () => send("ask", b.dataset.example, null)));
  function hasExercise() {
    return desk ? !desk.hidden : hintLevel > 0;
  }

  document.querySelectorAll("[data-intent]").forEach((b) => b.addEventListener("click", () => {
    if (busyNow) return;
    const intent = b.dataset.intent;
    if (intent !== "check") setChecking(false); // check mode only lasts until another action
    if (intent === "check" && !hasExercise()) {
      // Without an exercise, the next message must start one, not be checked as an answer.
      notice("First send the exercise — a photo or type it. Then you can check your answer.");
      text.focus();
      return;
    }
    if (intent === "check" && !text.value.trim() && !photo) {
      setChecking(true);
      notice("Write your answer or working below, or add a photo. Then tap send.");
      text.focus();
      return;
    }
    if (intent === "check") { setChecking(true); submit(); return; }
    send(intent, "", null);
  }));

  document.querySelectorAll(".ot-turn.tutor .ot-turn-text").forEach((p) => speech.attach(p));
  const savedPractice = JSON.parse(practiceBox.dataset.exercises || "[]");
  if (savedPractice.length) renderPractice(savedPractice.map((t, i) => ({ number: i + 1, text: t })));
  setLadder(hintLevel);
  updateActions();
  if (form.dataset.autofill && !text.value) { text.value = form.dataset.autofill; updateCount(); }
  scrollDown();
})();
