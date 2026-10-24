export type TutorId = 'maths' | 'english' | 'science';
export type Verdict = 'correct' | 'partly_correct' | 'not_yet_correct';

export const LIMITS = { sessions: 15, message: 2000, uploadBytes: 10 * 1024 * 1024, pinTries: 5, lockMinutes: 5, idleMinutes: 30, hints: 3, practice: 3 };

export const tutors: Record<TutorId, { name: string; short: string; description: string; topics: string[]; example: string; exercise: string; solution: string; hints: string[]; answer: string; practice: { text: string; answer: string }[] }> = {
  maths: {
    name: 'Maths', short: 'Mx', description: 'Equations, fractions and problems — one step at a time.', topics: ['linear equations', 'fractions', 'percentages', 'other'],
    example: 'Solve 2x + 6 = 14', exercise: '2x + 6 = 14', solution: '4',
    hints: ['What is being done to x? Try undoing the last thing first.', 'Use inverse operations: subtract 6 from both sides to keep the equation balanced.', 'Next step: 2x = 8. Now divide both sides by 2.'],
    answer: '1. 2x + 6 = 14\n2. Subtract 6: 2x = 8\n3. Divide by 2: x = 4\nCheck: 2 × 4 + 6 = 14 ✓\nWatch out: do the same thing to both sides.',
    practice: [{ text: '3x + 3 = 18', answer: '5' }, { text: '5x - 10 = 15', answer: '5' }, { text: '4x + 2 = 30', answer: '7' }],
  },
  english: {
    name: 'English', short: 'En', description: 'Grammar, vocabulary and reading — find the right words.', topics: ['past simple', 'vocabulary', 'reading', 'other'],
    example: 'Complete: Yesterday I ___ (go) to school.', exercise: 'Yesterday I ___ (go) to school.', solution: 'went',
    hints: ['Look at “yesterday”. Which tense does it point to?', 'Use the past simple. “Go” is irregular, so it does not end in -ed.', 'The next step: the past form begins with w and has four letters.'],
    answer: 'Yesterday I went to school.\n“Go” is irregular: go → went.\nWatch out: “goed” is not correct.',
    practice: [{ text: 'Last week she ___ (eat) pizza.', answer: 'ate' }, { text: 'We ___ (see) a film yesterday.', answer: 'saw' }, { text: 'He ___ (write) a letter.', answer: 'wrote' }],
  },
  science: {
    name: 'Science', short: 'Sc', description: 'Biology, chemistry and physics — explain what happens and why.', topics: ['photosynthesis', 'forces', 'states of matter', 'other'],
    example: 'Which gas do plants take in for photosynthesis?', exercise: 'Which gas do plants take in for photosynthesis?', solution: 'carbon dioxide',
    hints: ['Think about what plants need besides light and water.', 'Plants take this gas from the air. Humans breathe it out.', 'Next step: its formula is CO2. What is its name?'],
    answer: 'Plants take in carbon dioxide (CO2).\nThey use light energy to make glucose and release oxygen.\nWatch out: plants also respire, but photosynthesis takes in CO2.',
    practice: [{ text: 'Which gas do plants release in photosynthesis?', answer: 'oxygen' }, { text: 'Where in the leaf does photosynthesis mainly happen?', answer: 'chloroplasts' }, { text: 'What energy source does photosynthesis need?', answer: 'light' }],
  },
};

export function validateNickname(name: string, used: string[], activeSessions: number) {
  const nickname = name.trim();
  if (activeSessions >= LIMITS.sessions) return 'The hub is full right now. Please try again in a few minutes.';
  if (!/^[A-Za-z0-9 _-]{1,20}$/.test(nickname) || !/[A-Za-z0-9]/.test(nickname)) return 'Use 1–20 letters, numbers, spaces, - or _, including a letter or number.';
  if (used.some(item => item.toLowerCase() === nickname.toLowerCase())) return 'That nickname is already being used. Try another one.';
  return null;
}

export function validateMessage(text: string) {
  if (!text.trim()) return 'Write a question or add a photo first.';
  if (text.length > LIMITS.message) return `Please keep your message under ${LIMITS.message.toLocaleString('en')} characters.`;
  return null;
}

export function validateImage(file: { size: number; type: string }) {
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) return 'Please choose a JPEG, PNG or WebP photo.';
  if (file.size > LIMITS.uploadBytes) return 'This photo is larger than 10 MB. Choose a smaller one.';
  return null;
}

export function nextHintLevel(level: number) { return Math.min(level + 1, LIMITS.hints); }
export function hintFor(tutor: TutorId, level: number) { return tutors[tutor].hints[Math.max(1, Math.min(level, 3)) - 1] ?? tutors[tutor].hints[0]; }

type Linear = { a: number; b: number };
function parseSide(input: string): Linear | null {
  const value = input.replace(/\s+/g, '').replace(/−/g, '-');
  if (!value || !/^[+-]?(\d*x|\d+)([+-](\d*x|\d+))*$/.test(value)) return null;
  return (value.match(/[+-]?(\d*x|\d+)/g) ?? []).reduce((acc, term) => {
    if (!term.includes('x')) return { ...acc, b: acc.b + Number(term) };
    const k = term.replace('x', '');
    return { ...acc, a: acc.a + (k === '' || k === '+' ? 1 : k === '-' ? -1 : Number(k)) };
  }, { a: 0, b: 0 });
}
export function solveLinear(equation: string) {
  const sides = equation.split('=');
  if (sides.length !== 2) return null;
  const left = parseSide(sides[0] ?? ''); const right = parseSide(sides[1] ?? '');
  if (!left || !right || left.a === right.a) return null;
  return (right.b - left.b) / (left.a - right.a);
}

export function checkMaths(exercise: string, attempt: string): { decided: boolean; verdict?: Verdict; firstErrorStep?: number; message: string } {
  const target = solveLinear(exercise);
  const steps = attempt.split(/\n|;/).map(s => s.trim().replace(/^\d+[.)]\s*/, '')).filter(Boolean);
  if (target === null || !steps.length) return { decided: false, message: 'I can’t check that format automatically.' };
  for (let i = 0; i < steps.length; i += 1) {
    const current = steps[i] ?? '';
    const step = /^-?\d+(\.\d+)?$/.test(current) ? `x=${current}` : current;
    const solved = solveLinear(step);
    if (solved === null) return { decided: false, message: 'I can’t read one of these steps.' };
    if (Math.abs(solved - target) > 1e-9) return { decided: true, verdict: 'not_yet_correct', firstErrorStep: i + 1, message: `Step ${i + 1} changes the solution. Look again at what you did to both sides.` };
  }
  const last = (steps.at(-1) ?? '').replace(/\s/g, '');
  if (new RegExp(`^(x=)?${target}$`).test(last)) return { decided: true, verdict: 'correct', message: 'Correct — every step keeps the equation balanced.' };
  return { decided: true, verdict: 'partly_correct', message: 'Good so far. Every step is right, but you haven’t finished yet.' };
}

export function checkText(answer: string, expected: string) {
  const clean = (s: string) => s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]/g, '');
  const value = clean(answer);
  return value === clean(expected) || (clean(expected) === 'carbondioxide' && value === 'co2');
}

export function queueMessage(position: number) { return position <= 1 ? 'You’re next' : `${position - 1} ${position === 2 ? 'student' : 'students'} ahead of you`; }

export function checkPin(pin: string, expected: string, tries: number, lockedUntil: number, now: number) {
  if (lockedUntil > now) return { ok: false, tries, lockedUntil, message: 'Too many tries. Try again in 5 minutes.' };
  if (/^\d{6}$/.test(pin) && pin === expected) return { ok: true, tries: 0, lockedUntil: 0, message: '' };
  const nextTries = tries + 1;
  if (nextTries >= LIMITS.pinTries) return { ok: false, tries: 0, lockedUntil: now + LIMITS.lockMinutes * 60_000, message: 'Too many tries. Try again in 5 minutes.' };
  return { ok: false, tries: nextTries, lockedUntil: 0, message: `Wrong PIN. ${LIMITS.pinTries - nextTries} tries left.` };
}