import { describe, expect, it } from 'vitest';
import { checkMaths, checkPin, LIMITS, nextHintLevel, queueMessage, validateImage, validateMessage, validateNickname } from './opentutor';

describe('OpenTutor spec rules', () => {
  it('limits hint requests to three before the answer action', () => expect(nextHintLevel(nextHintLevel(nextHintLevel(nextHintLevel(0))))).toBe(3));
  it('caps the hub at 15 sessions and rejects duplicate nicknames', () => {
    expect(validateNickname('Ana', [], 15)).toMatch(/full/);
    expect(validateNickname('ana', ['Ana'], 1)).toMatch(/already/);
    expect(validateNickname('Ana_7', [], 14)).toBeNull();
    expect(validateNickname('A', [], 0)).toBeNull();
    expect(validateNickname('--', [], 0)).not.toBeNull();
  });
  it('enforces 2,000 characters and 10 MB uploads', () => {
    expect(validateMessage('a'.repeat(2000))).toBeNull();
    expect(validateMessage('a'.repeat(2001))).not.toBeNull();
    expect(validateImage({ size: LIMITS.uploadBytes, type: 'image/jpeg' })).toBeNull();
    expect(validateImage({ size: LIMITS.uploadBytes + 1, type: 'image/jpeg' })).not.toBeNull();
    expect(validateImage({ size: 100, type: 'image/gif' })).not.toBeNull();
  });
  it('lets the maths checker decide correct, unfinished and first wrong steps', () => {
    expect(checkMaths('2x + 6 = 14', '2x = 8\nx = 4').verdict).toBe('correct');
    expect(checkMaths('2x + 6 = 14', '2x = 8').verdict).toBe('partly_correct');
    expect(checkMaths('2x + 6 = 14', '2x = 20\nx = 10')).toMatchObject({ verdict: 'not_yet_correct', firstErrorStep: 1 });
    expect(checkMaths('2x + 6 = 14', 'x^2 = 16').decided).toBe(false);
  });
  it('locks the teacher PIN after five wrong tries for five minutes', () => {
    let state = { tries: 0, lockedUntil: 0 };
    for (let i = 0; i < 5; i += 1) state = checkPin('000000', '123456', state.tries, state.lockedUntil, 1000);
    expect(state.lockedUntil).toBe(301000);
    expect(checkPin('123456', '123456', 0, state.lockedUntil, 2000).ok).toBe(false);
  });
  it('shows queue position in plain language', () => expect(queueMessage(3)).toBe('2 students ahead of you'));
});