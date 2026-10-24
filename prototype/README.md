# OpenTutor interactive prototype

Isolated copy of the verified Lovable demo. All prototype code is inside `prototype/`; the original OpenTutor application and specs are unchanged.

## Run locally

Install Bun, then from this directory run:

```sh
bun install --frozen-lockfile
bun run dev
```

Open the local URL printed by Vite. Run tests with `bun run test`.
This export retains the Lovable TanStack/Vite configuration; a hosting environment must support that configuration and the server route to use Live AI.

## What is included

- Nickname-only student entry, subject selection, typed exercises and photos.
- Demo tutoring: progressive hints, attempts, answer reveal and three practice items.
- Optional Live AI: streaming, image reading, adaptive hints, stop control and rendered maths.
- Teacher insights, prototype PIN `123456`, and illustrative hub/QR/settings.
- Transient React sessions and transcripts: no student accounts or saved history.

## Optional Live AI configuration

Demo mode needs no AI key. Live AI requires internet and explicit student consent. Configure `LOVABLE_API_KEY` privately in the server environment using your authorized Lovable AI Gateway credentials. No key is bundled; never expose it in a `VITE_*` variable. The exported `/api/tutor` helper uses the same AI Gateway Responses integration as the preview.

The export omits Lovable-generated account clients and their unused bearer middleware: this prototype has no student accounts and does not call protected server functions. Request error and CSRF middleware remain.

## Prototype boundaries

The Linux hub, Ollama/Gemma, Wi-Fi hotspot, queue, session-capacity scenarios, QR/tunnel and evaluation targets are simulations, not installed infrastructure. Teacher access and PIN checks are client-side demonstration controls, not production authorization. Live AI is an optional online extension, not the local/offline model described by the core project. Sessions/insights are in-memory only. Do not expose this prototype publicly as a production classroom service without real authorization, hub infrastructure and abuse controls.

## Preview

https://id-preview--5fe438c3-b9ff-4eb5-91b4-21c34d23b4ec.lovable.app

The preview requires access to the Lovable project. The existing Claude brief remains at `docs/prototype/claude-brief.md` in the repository.
