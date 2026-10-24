# 0004. Serve Gemma 4 locally through Ollama, model pinned in configuration

- Status: Proposed
- Date: 2026-10-24
- Deciders: OpenTutor team

## Context
The product needs a local multimodal model (image input), JSON-schema structured output
for verdicts and practice sets (REQ-006, REQ-007), streaming, and several requests in
parallel on one GPU (REQ-009). Gemma 4 is mandated (hackathon category; product.md).
The GPU model on the hub is not confirmed (Q-1).

## Options considered
1. **Ollama** — Pros: one-command model download with digest verification, local HTTP
   API with streaming, image input and JSON-schema `format`, parallel requests setting,
   official Python client. Cons: less tuning control than llama.cpp; feature support per
   model tag must be checked.
2. **llama.cpp server** — Pros: fine control (quantization, grammar-constrained output).
   Cons: manual GGUF and multimodal projector handling; harder operator setup.
3. **vLLM / transformers** — Pros: high throughput. Cons: heavy setup, GPU-specific,
   overkill for 5 active students.

## Decision
We will run Gemma 4 through Ollama listening on loopback only, with **E4B as the default
and E2B as the fallback**, and keep the model tag and its digest in the hub configuration,
because Ollama gives operators the simplest setup with the features the spec needs. The
final default is confirmed by the benchmark spike (T-001) on the hub laptop.

## Consequences
- Positive: operator setup is `ollama pull` plus our start command; models swap by config
  (AC-012.6).
- Negative / risks: exact tags, sizes, structured-output and image support for Gemma 4
  must be verified on the official Ollama library (Q-3) — the spike tests them; the raw
  Ollama API must never be reachable from the LAN (NFR-001) — the start command checks it.
- Follow-ups: record model license terms (Q-5); revisit if the spike fails (e.g. move to
  llama.cpp in a superseding ADR).
