import type { ModelMessage } from 'ai';
import { createResponsesCall } from './responses.server';
import { tutorInput, tutorInstructions } from './tutor-input';

export async function handleTutor(request: Request) {
  const origin = request.headers.get('origin');
  if (origin && origin !== new URL(request.url).origin) return Response.json({ message: 'Use OpenTutor to send your exercise.' }, { status: 403 });
  const key = process.env['LOVABLE_API_KEY'];
  if (!key) return Response.json({ message: 'The AI tutor is not configured yet.' }, { status: 401 });
  if (Number(request.headers.get('content-length') ?? 0) > 15000000) return Response.json({ message: 'Photo is too large.' }, { status: 413 });
  let body: unknown;
  try { body = await request.json(); } catch { return Response.json({ message: 'Please send a valid exercise.' }, { status: 400 }); }
  const parsed = tutorInput.safeParse(body);
  if (!parsed.success) return Response.json({ message: parsed.error.issues[0]?.message ?? 'Invalid exercise.' }, { status: 400 });
  const { subject, action, level, turns } = parsed.data;
  const messages: ModelMessage[] = turns.map(turn => turn.role === 'tutor'
    ? { role: 'assistant', content: turn.text }
    : { role: 'user', content: [{ type: 'text', text: turn.text || 'Help me with the exercise in this photo.' }, ...(turn.image ? [{ type: 'image' as const, image: new URL(turn.image) }] : [])] });
  const call = createResponsesCall(request, { baseURL: 'https://ai.gateway.lovable.dev/v1', apiKey: key, model: 'openai/gpt-6-astra' }, messages, tutorInstructions(subject, action, level));
  return call.response();
}