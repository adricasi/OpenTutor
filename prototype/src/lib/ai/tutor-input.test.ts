import { describe, expect, it } from 'vitest';
import { tutorInput } from './tutor-input';
describe('AI tutor input', () => {
  const input = { subject: 'maths', action: 'hint', level: 1, turns: [{ role: 'student', text: 'Solve 7x + 3 = 24' }] };
  it('allows a written exercise and attempt', () => expect(tutorInput.safeParse({ ...input, turns: [...input.turns, { role: 'student', text: '7x = 21' }] }).success).toBe(true));
  it('enforces the 2000-character student limit', () => expect(tutorInput.safeParse({ ...input, turns: [{ role: 'student', text: 'a'.repeat(2001) }] }).success).toBe(false));
  it('allows photos but rejects external image addresses', () => {
    expect(tutorInput.safeParse({ ...input, turns: [{ role: 'student', text: '', image: 'data:image/png;base64,AAAA' }] }).success).toBe(true);
    expect(tutorInput.safeParse({ ...input, turns: [{ role: 'student', text: '', image: 'https://example.com/image.png' }] }).success).toBe(false);
  });
});