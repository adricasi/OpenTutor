# Models

Researched October 2026. Re-verify before pinning weights; this area moves monthly. Everything below targets a MacBook Pro M3 Pro with 32 GB unified memory using MLX. Rule of thumb: ≤ ~14B params in bf16, or ≤ ~30B at 4-bit, fits comfortably.

## Roles and picks

| Role | Pick | Fallback / alternative | Size on disk | Notes |
|---|---|---|---|---|
| Tier 1 · handwritten math (strokes → LaTeX) | **mathnote-ocr** (stroke-based, ~1.2M params, CPU) | UniMERNet / TexTeller (image → LaTeX, ~300M) | tiny | Incremental: strokes can be added/removed; supports pinning a confirmed subtree. Gives stroke→symbol map, which the underline marks need. |
| Tier 1 · handwritten text, CJK, labels | **Qwen3.5-4B** MLX 4-bit (vision) | OvisOCR2 0.8B MLX 4-bit; dots.ocr 1.5 (3B) | ~2.5 GB | Ask for JSON with bboxes (grounding) so anchors map to strokes. |
| Tier 1 · paper photos | **Chandra OCR 2** MLX mixed 4/8-bit | olmOCR-2-7B (GGUF/MLX; needs its toolkit prompt format) | 3.3 GB | Load on demand, unload after. Best open model for handwriting + math + diagram pages (85.9 olmOCR-bench). |
| Tier 2 · tutor (vision + reasoning, local) | **Qwen3.6-35B-A3B** MLX ~4.8-bit, vision | Qwen3.6-27B / Qwen3.8-27B dense (better reasoning, ~3× slower decode); Gemma 4 26B-A4B (fast MoE, MATH-Vision 82.4) | ~20 GB | MoE with ~3B active params → fast decode on M3 Pro. Served via mlx-vlm server or LM Studio (OpenAI-compatible). |
| Tier 2 · tutor (cloud toggle) | Claude Sonnet 5.5 (speed) / Opus 5.5 (hard problems) | OpenAI GPT-5 class | — | Same prompts, same JSON-lines output contract. |
| Voice · VAD | Silero VAD v5 | — | tiny | |
| Voice · STT | **Parakeet TDT** via mlx-audio (streaming, sub-100 ms) | Whisper large-v3-turbo MLX (broader languages) | ~1 GB | Parakeet v3 covers 25 European languages; use Whisper for others. |
| Voice · TTS | **Kokoro-82M** via mlx-audio | Qwen3-TTS (voice design/cloning) | ~0.3 GB | Fast; many voices. |
| Fine-tuning tier 1 on real handwriting | mlx-tune (OCR fine-tuning on Mac) | — | — | Only if accuracy lags after fixtures exist; HG-Bench shows an in-domain fine-tuned 9B beats frontier zero-shot on handwritten homework. |

## Switching the local tier-2 model

Registered in `models/manifest.toml` under `[tier2.*]`; selected with `LOCAL_MODEL` in `.env` or the picker in the web settings panel (runtime swaps the model server, one resident at a time). Gemma 4 26B-A4B is a serious contender for the tutor role: MATH-Vision 82.4, ~3.8B active params (similar speed), ~15 GB at 4-bit (more headroom for voice and Chandra), structured bbox output. **Gemma 4 is the default since 2026-10-24** (DECISIONS #25): on the reference Mac (M3 Pro, 36 GB) it runs fully resident (first token ~1 s, 43 tok/s) and scored 100 % on the maths judge and 1/1 on consulting, while Qwen3.6-35B-A3B (stronger handwriting evals: OmniDocBench 1.5 89.9, RefCOCO 92.0) usually had to run expert-offloaded (~6 s to first token). Qwen3.6 stays selectable.

## Why these (research summary)

- **Handwriting:** on the 2026 AIMultiple cursive benchmark, Gemini 3 Pro scored perfectly, GPT-5 and olmOCR-2 completed the top three; cloud OCR APIs (AWS/Google/Azure) were mid-pack. olmOCR-2 was the only open model in the top three. Chandra 2 and Qwen3-VL/3.5 are the other open models with explicit handwriting support.
- **Math:** Chandra 2 and olmOCR-2 handle handwritten equations → LaTeX on full pages; UniMERNet/TexTeller are small formula-only specialists (CROHME ~65–68% exact match for UniMERNet; TexTeller claims SOTA). Mathpix is the commercial reference.
- **Diagrams:** Chandra 2 extracts charts/diagrams to SVG; for *understanding* a diagram use a general VLM. On a plot-reconstruction benchmark Qwen3-VL-235B tied GPT-5 (72.0 vs 72.1).
- **Online (stroke) recognition:** MyScript iink is the commercial standard (72 languages, 250 math symbols, diagram shapes) but is cloud-only and metered (2000 free requests/month, then $10 per 1000; diagrams REST-only) — rejected for a local-first product. mathnote-ocr is the open stroke-based option built for real-time use. Google's MathWriting (230k online samples) is the dataset to fine-tune on if needed.
- **Printed-document leaderboards** (OmniDocBench: PaddleOCR-VL-1.6, MinerU2.5-Pro, GLM-OCR at ~1B params) are not evidence for handwriting; do not pick from them.

## Quantization notes

- Keep the **vision encoder in bf16**; quantize only the language model (the Chandra "mixed 4/8" MLX recipe does this).
- For math/LaTeX prefer 8-bit or mixed 4/8 over plain 4-bit; symbol-level errors appear first under aggressive quantization. Test both on fixtures.
- MLX (`mlx-vlm`, `mlx-lm`, `mlx-audio`) is the fast path on Apple Silicon. Ollama/GGUF is easier but vision support lags.
- olmOCR-2 expects its toolkit's rendering (longest side 1288 px) and prompt metadata; a bare Ollama run underperforms. Prefer Chandra for photos unless there is a reason.

## Memory budget (resident at once)

| Component | GB |
|---|---|
| Tier-2 Qwen3.6-35B-A3B 4.8-bit + KV cache | ~21 |
| Tier-1 Qwen3.5-4B 4-bit | ~2.5 |
| mathnote-ocr, Silero, Parakeet, Kokoro | ~2 |
| macOS + browser headroom | ~5 |
| **Total** | **~30.5** |

Chandra (3.3 GB) is loaded for a photo and unloaded afterwards; the runtime refuses to load it while memory is tight. Never load a second tier-2 model.

## Serving

```bash
# tier-2 local, OpenAI-compatible on :8080 (exact flags depend on mlx-vlm version)
python -m mlx_vlm.server --model <mlx-community/...Qwen3.6-35B-A3B...-vision> --port 8080
# or LM Studio: load the MLX model, enable the local server, same base URL

# tier-1 text/CJK: load in-process with mlx-vlm (apps/server/recognize/text.py)
# photos: Chandra via its MLX backend, loaded on demand
# voice: mlx-audio for Parakeet + Kokoro, loaded in-process
```

`models/manifest.toml` should pin exact HF repo ids and revisions once chosen; `make models` downloads and verifies them.

## Benchmarks to re-check when revisiting picks

OCR Arena (ocrarena.ai, community ELO incl. handwriting), olmOCR-Bench, OCRBench v2, IDP Leaderboard, AIMultiple cursive benchmark, MathWriting/CROHME for HMER.

## Measured on the reference Mac (M3 Pro, 36 GiB, macOS 14.3; 2026-10-24)

Tier 2, one tutoring request (a crop of a recorded integral + reading + checker verdict, ~300 output tokens), mlx-vlm 0.7.10:

| model / mode | resident | first token (warm) | decode | notes |
|---|---|---|---|---|
| Qwen3.6-35B-A3B, full | ~21 GiB wired | 0.8 s | 55 tok/s | best output; loading it with 2 GiB to spare pushed the Mac to 7.7 GiB swap |
| Qwen3.6-35B-A3B, expert offload, 12 GB cache | ~13–15 GiB | 6 s | 9–12 tok/s | 93 % expert-cache hits; a review takes 30–45 s |
| Qwen3.6-35B-A3B, expert offload, 6 GB cache | ~7 GiB | 6–7 s | 4.5 tok/s | 77 % hits |
| Gemma 4 26B-A4B QAT, full | ~14.5 GiB wired | 1.0 s | 43 tok/s | weaker level-3 hint; writes unescaped LaTeX in JSON (parser tolerates it) |

Tier 1 (Qwen3.5-4B, in-process): 2–3.5 s per handwritten line (dominated by output tokens; crop scale barely matters), 3.5–3.8 GiB peak.
"Compact LaTeX" prompting is ~30 % faster but misreads evaluation brackets, so the spaced prompt stays.

**Runtime policy** (`apps/server/runtime`): a tier-2 model loads fully resident only if `memory_gb` + 6 GiB stays free;
otherwise from its expert-offload store (`uv run models/fetch.py --offload KEY`, experts paged from the SSD) with the
largest cache that fits with 2 GiB headroom; otherwise it refuses. A watchdog moves a full model to offload if free
memory stays under 2 GiB. The first mark never waits for tier 2 anyway: checker marks arrive 1.7–3.9 s after pen-up.

**Judge on the real maths recording** (`make judge`, 4 checkpoints, local judge): Qwen3.6 (offload) 100 %, Gemma 4 (full) 100 %,
deterministic checks 100 %, median first mark 2.6 s. The fixture set is still small; re-judge when more recordings exist.
