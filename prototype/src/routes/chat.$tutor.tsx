import { createFileRoute } from '@tanstack/react-router';
import { Shell } from '@/components/opentutor/shell';
import { Chat } from '@/components/opentutor/chat';
import { tutors, type TutorId } from '@/lib/opentutor';
export const Route = createFileRoute('/chat/$tutor')({
  validateSearch: (search: Record<string, unknown>): { example?: boolean } => ({ example: search['example'] === true || search['example'] === 'true' }),
  head: ({ params }) => { const name = tutors[params.tutor as TutorId]?.name ?? 'Subject'; return { meta: [{ title: `${name} tutor · OpenTutor` }, { name: 'description', content: `Learn ${name} with hints, check your working and try three practice exercises.` }, { property: 'og:title', content: `${name} tutor · OpenTutor` }, { property: 'og:description', content: 'A nudge. A method. The next step. Your lightbulb moment.' }, { property: 'og:type', content: 'website' }, { name: 'twitter:card', content: 'summary_large_image' }] }; },
  component: Page,
});
function Page() { const { tutor } = Route.useParams(); const { example } = Route.useSearch(); if (!['maths', 'english', 'science'].includes(tutor)) return <Shell section="Tutor"><h1>This tutor isn’t available.</h1></Shell>; return <Shell section={tutors[tutor as TutorId].name}><Chat key={tutor} id={tutor as TutorId} autoExample={example ?? false} /></Shell>; }