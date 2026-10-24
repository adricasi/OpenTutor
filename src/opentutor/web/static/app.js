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

  function bubble(kind, content) {
    const p = document.createElement("p");
    p.className = "msg " + kind;
    if (content) p.textContent = content;
    log.appendChild(p);
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
          else if (ev.type === "verdict") bubble("tutor", [VERDICTS[ev.verdict], ev.explanation, ev.first_error_step ? "First step to fix: " + ev.first_error_step : "", ev.hint ? "Hint: " + ev.hint : ""].filter(Boolean).join("\n"));
          else if (ev.type === "practice") bubble("tutor", "Try these:\n" + ev.exercises.map((x) => x.number + ". " + x.text).join("\n"));
          else if (ev.type === "notice") bubble("notice", ev.message);
          else if (ev.type === "error") bubble("notice", ev.message);
        }
      }
    } catch (e) {
      bubble("notice", "Connection lost. Check you're on the hub Wi-Fi and try again.");
    } finally {
      status.textContent = "";
      busy(false);
    }
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); send("ask"); });
  document.querySelectorAll("[data-intent]").forEach((b) => b.addEventListener("click", () => send(b.dataset.intent)));
  imageInput.addEventListener("change", () => { photoName.textContent = imageInput.files[0] ? "Photo ready" : ""; });
})();
