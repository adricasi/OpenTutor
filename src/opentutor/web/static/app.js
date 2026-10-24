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
  document.querySelectorAll("[data-close-finish]").forEach((button) => button.addEventListener("click", () => finish.close()));

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
      document.querySelectorAll(".speak").forEach((b) => { b.hidden = !voice; });
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
      button.setAttribute("aria-pressed", speaking ? "true" : "false");
    }

    function toggle(button, value) {
      const wasMine = playing === button;
      synth.cancel();
      if (playing) label(playing, false);
      playing = null;
      if (wasMine || !voice) return;
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
      button.hidden = !voice;
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

  function scrollDown() { log.scrollTop = log.scrollHeight; }

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
      verdict.appendChild(iconFrom("tpl-verdict"));
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

  function notice(message) {
    clearEmpty();
    const box = document.createElement("div");
    box.className = "ot-notice";
    box.setAttribute("role", "status");
    const p = document.createElement("p");
    p.textContent = message;
    box.appendChild(p);
    log.appendChild(box);
    scrollDown();
  }

  function setLadder(level) {
    hintLevel = level;
    if (!ladder) return;
    ladder.querySelectorAll("li").forEach((li) => li.classList.toggle("done", Number(li.dataset.level) <= level));
  }

  function setDesk(value) {
    if (!desk) return;
    desk.hidden = !value;
    document.getElementById("desk-text").textContent = value;
  }

  function busy(on) {
    busyNow = on;
    document.querySelectorAll("#actions button, #ask button, #practice button").forEach((b) => { b.disabled = on; });
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
  }

  function clearPhoto() {
    photo = null;
    preview.hidden = true;
  }

  function renderPractice(exercises) {
    practiceBox.replaceChildren();
    const title = document.createElement("div");
    title.className = "ot-section-title";
    const h2 = document.createElement("h2");
    h2.textContent = "Your turn × 3";
    const kicker = document.createElement("span");
    kicker.textContent = "SAME IDEA, NEW CHALLENGE";
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
        send("check", exercise.number + ". " + input.value.trim(), null);
      });
      const result = document.createElement("small");
      body.append(strong, answerForm, result);
      item.append(number, body);
      practiceBox.appendChild(item);
    });
    practiceBox.hidden = false;
  }

  function practiceResult(number, verdict) {
    const item = practiceBox.querySelector('[data-number="' + number + '"]');
    if (!item) return;
    item.classList.toggle("solved", verdict === "correct");
    item.querySelector("small").textContent = verdict === "correct" ? "Correct. Nice work!" : "Not yet. Try again — you’ve got this.";
  }

  async function send(intent, message, file) {
    const data = new FormData();
    data.append("intent", intent);
    data.append("text", message);
    if (file) data.append("image", await shrink(file), "photo.jpg");
    if (message) turn("student", (intent === "check" ? "My answer: " : "") + message, file ? { image: photo.url } : null);
    else if (file) turn("student", "", { image: photo.url });
    const newExercise = (intent === "ask" && (hintLevel === 0 || file)) || intent === "new_exercise";
    busy(true);
    status.replaceChildren();
    let reply = null;
    let ok = false;
    try {
      const res = await fetch("/api/messages", { method: "POST", body: data });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        notice(body.detail || "Something went wrong, please try again.");
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
          } else if (ev.type === "notice" || ev.type === "error") {
            notice(ev.message);
          } else if (ev.type === "done") {
            ok = true;
          }
        }
      }
    } catch (e) {
      notice("Connection lost. Check you’re on the hub Wi-Fi and try again.");
    } finally {
      if (reply) speech.attach(reply);
      queueBox.hidden = true;
      status.replaceChildren();
      busy(false);
      if (ok) {
        if (newExercise) { setLadder(1); setDesk(message || "Your photo"); practiceBox.hidden = true; }
        else if (intent === "hint") setLadder(Math.min(Math.max(hintLevel, 1) + 1, 3));
        else if (intent === "answer") setLadder(4);
      }
    }
  }

  function submit() {
    const message = text.value.trim();
    if (!message && !photo) return;
    const intent = checking ? "check" : "ask";
    const file = photo ? photo.file : null;
    text.value = "";
    updateCount();
    setChecking(false);
    send(intent, message, file).finally(clearPhoto);
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); if (!busyNow) submit(); });
  text.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!busyNow) submit(); }
  });
  text.addEventListener("input", updateCount);
  imageInput.addEventListener("change", () => choosePhoto(imageInput));
  cameraInput.addEventListener("change", () => choosePhoto(cameraInput));
  document.getElementById("photo-remove").addEventListener("click", clearPhoto);
  document.querySelectorAll("[data-example]").forEach((b) => b.addEventListener("click", () => send("ask", b.dataset.example, null)));
  document.querySelectorAll("[data-intent]").forEach((b) => b.addEventListener("click", () => {
    const intent = b.dataset.intent;
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
  if (form.dataset.autofill && !text.value) { text.value = form.dataset.autofill; updateCount(); }
  scrollDown();
})();
