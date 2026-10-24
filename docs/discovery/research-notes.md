# Research notes: OpenTutor

Light research during the hackathon (2026-10-24); secondary sources, verify key facts.

## Gemma 4 local
- Lineup reported as E2B, E4B (small devices), 26B MoE (A4B), 31B dense; image input; runs locally via Ollama. Sources disagree on audio input and context length; verify tags on the official Ollama library page.
  - https://www.hongkiat.com/blog/run-gemma-4-locally/
  - https://getdeploying.com/guides/local-gemma4
  - https://www.mindstudio.ai/blog/how-to-run-gemma-4-locally-ollama (sizes inconsistent with others; unreliable)
- Default E4B Ollama pull reported ~9–10 GB (Hongkiat).

## Offline education landscape
- Kolibri forum: team prototyping local models (Phi, Gemma, Llama), nothing broadly testable yet. https://community.learningequality.org/t/intergrating-ai-on-kolibri-offline-connections/3733
- "AI Learning Box": Raspberry Pi 5 + Ollama gemma2:2b + Kolibri content, offline. https://online.coolestprojects.org/projects/24549
- Paper: Kolibri/RACHEL lack generative, conversational tutoring; proposes local RAG tutor on low-cost device. https://revistas.unilibre.edu.co/index.php/entramado/article/download/13645/13839/49864
- Learning Equality "Offline Squared" (WhatsApp AI coaching for teachers). https://solve.mit.edu/solutions/113251
