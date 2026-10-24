<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

## Prototype architecture
- Keep sessions and transcripts in transient React state, without student accounts or saved history; deterministic demo rules remain independently testable.
- Keep optional live tutoring behind `/api/tutor` with server-only AI Gateway helpers; obtain explicit online consent and never send nicknames or classroom insights to the model.
