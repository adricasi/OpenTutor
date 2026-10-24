// Chat client: sends messages, reads the NDJSON event stream, renders text safely (never as HTML).
"use strict";
(function () {
  const form = document.getElementById("ask");
  if (!form) return;
  const log = document.getElementById("log");
  const status = document.getElementById("status");
  const text = document.getElementById("text");
  const imageInput = document.getElementById("image");
  const photoName = document.getElementById("photo-name");
  const buttons = Array.from(document.querySelectorAll("button"));
  const VERDICTS = { correct: "✅ Correct", partly_correct: "🟡 Partly correct", not_yet_correct: "❌ Not yet correct" };

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

    function words(text) {
      return text
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

    function reset(button) {
      button.textContent = "🔊 Read aloud";
      button.setAttribute("aria-pressed", "false");
    }

    function toggle(button, text) {
      const wasMine = playing === button;
      synth.cancel();
      if (playing) reset(playing);
      playing = null;
      if (wasMine || !voice) return;
      const utterance = new SpeechSynthesisUtterance(words(text));
      utterance.voice = voice;
      utterance.lang = voice.lang;
      utterance.onend = utterance.onerror = () => { if (playing === button) { reset(button); playing = null; } };
      playing = button;
      button.textContent = "⏹ Stop";
      button.setAttribute("aria-pressed", "true");
      synth.speak(utterance);
    }

    function attach(bubbleEl) {
      if (!synth || bubbleEl.dataset.speak) return;
      bubbleEl.dataset.speak = "1";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "speak";
      button.hidden = !voice;
      reset(button);
      button.addEventListener("click", () => toggle(button, bubbleEl.textContent));
      bubbleEl.after(button);
    }

    if (synth) {
      pickVoice();
      synth.addEventListener("voiceschanged", pickVoice);
    }
    return { attach };
  })();

  function bubble(kind, content) {
    const p = document.createElement("p");
    p.className = "msg " + kind;
    if (content) p.textContent = content;
    log.appendChild(p);
    if (kind === "tutor" && content) speech.attach(p);
    p.scrollIntoView({ block: "end" });
    return p;
  }

  function busy(on) {
    buttons.forEach((b) => { if (b.type !== "submit" || b.form === form) b.disabled = on; });
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

  async function send(intent) {
    const data = new FormData();
    data.append("intent", intent);
    data.append("text", text.value.trim());
    const file = imageInput.files[0];
    if (file) data.append("image", await shrink(file), "photo.jpg");
    if (!text.value.trim() && !file && ["ask", "check"].includes(intent)) {
      status.textContent = intent === "check" ? "Type your answer first, then tap Check my answer." : "Type a question or add a photo.";
      return;
    }
    if (text.value.trim()) bubble("student", (intent === "check" ? "My answer: " : "") + text.value.trim());
    if (file) { const p = bubble("student"); const img = document.createElement("img"); img.alt = "Your photo"; img.src = URL.createObjectURL(file); p.appendChild(img); }
    text.value = ""; imageInput.value = ""; photoName.textContent = "";
    busy(true);
    status.textContent = "Thinking…";
    let reply = null;
    try {
      const res = await fetch("/api/messages", { method: "POST", body: data });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        bubble("notice", body.detail || "Something went wrong, please try again.");
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
          const line = buffer.slice(0, nl); buffer = buffer.slice(nl + 1);
          if (!line) continue;
          const ev = JSON.parse(line);
          if (ev.type === "queued") status.textContent = "You're number " + ev.position + " in line…";
          else if (ev.type === "started") status.textContent = "Thinking…";
          else if (ev.type === "token") { if (!reply) { reply = bubble("tutor"); status.textContent = ""; } reply.textContent += ev.text; }
          else if (ev.type === "verdict") bubble("tutor", [(ev.practice_number ? "Exercise " + ev.practice_number + ": " : "") + VERDICTS[ev.verdict], ev.explanation, ev.first_error_step ? "First step to fix: " + ev.first_error_step : "", ev.hint ? "Hint: " + ev.hint : ""].filter(Boolean).join("\n"));
          else if (ev.type === "practice") bubble("tutor", "Try these:\n" + ev.exercises.map((x) => x.number + ". " + x.text).join("\n"));
          else if (ev.type === "notice") bubble("notice", ev.message);
          else if (ev.type === "error") bubble("notice", ev.message);
        }
      }
    } catch (e) {
      bubble("notice", "Connection lost. Check you're on the hub Wi-Fi and try again.");
    } finally {
      if (reply) speech.attach(reply);
      status.textContent = "";
      busy(false);
    }
  }

  document.querySelectorAll(".msg.tutor").forEach((p) => speech.attach(p));
  form.addEventListener("submit", (e) => { e.preventDefault(); send("ask"); });
  document.querySelectorAll("[data-intent]").forEach((b) => b.addEventListener("click", () => send(b.dataset.intent)));
  imageInput.addEventListener("change", () => { photoName.textContent = imageInput.files[0] ? "Photo ready" : ""; });
})();
