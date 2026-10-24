import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { ArrowUp, ArrowUpRight, BookOpen, Camera, Check, ChevronRight, CircleCheck, Eye, ImagePlus, Lightbulb, Plus, RotateCcw, ShieldCheck, Square, Volume2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { checkMaths, checkText, hintFor, LIMITS, nextHintLevel, queueMessage, solveLinear, tutors, validateImage, validateMessage, type TutorId, type Verdict } from '@/lib/opentutor';
import { useSession } from './session';
import { TutorText } from './tutor-text';

type Turn = { role: 'student' | 'tutor'; text: string; image?: string | undefined; verdict?: Verdict | undefined; reasoning?: string };
type Photo = { url: string; name: string };
type Scenario = 'normal' | 'busy' | 'unreadable' | 'several' | 'timeout' | 'unavailable';

export function Chat({ id, autoExample }: { id: TutorId; autoExample: boolean }) {
  const { nickname, record, busy, enabled } = useSession();
  const tutor = tutors[id];
  const [live, setLive] = useState(false);
  const [consent, setConsent] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const [exercise, setExercise] = useState('');
  const [hint, setHint] = useState(0);
  const [photo, setPhoto] = useState<Photo | null>(null);
  const [notice, setNotice] = useState('');
  const [scenario, setScenario] = useState<Scenario>('normal');
  const [stage, setStage] = useState<'idle' | 'queued' | 'streaming'>('idle');
  const [position, setPosition] = useState(3);
  const [practice, setPractice] = useState(false);
  const [practiceIndex, setPracticeIndex] = useState<number | null>(null);
  const [practiceAnswers, setPracticeAnswers] = useState<Record<number, string>>({});
  const [results, setResults] = useState<Record<number, boolean>>({});
  const [checking, setChecking] = useState(false);
  const [retry, setRetry] = useState<string | null>(null);
  const [suggested, setSuggested] = useState<TutorId | null>(null);
  const [several, setSeveral] = useState(false);
  const [voice, setVoice] = useState<SpeechSynthesisVoice | null>(null);
  const [speaking, setSpeaking] = useState<number | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const generation = useRef(0);
  const sending = useRef(false);
  const photoUrls = useRef<string[]>([]);
  const initialExample = useRef(false);
  const topic = tutor.topics[0] ?? 'other';

  useEffect(() => {
    if (!('speechSynthesis' in window)) return;
    const voices = () => setVoice(window.speechSynthesis.getVoices().find(v => v.localService && v.lang.startsWith('en')) ?? null);
    voices(); window.speechSynthesis.addEventListener('voiceschanged', voices);
    return () => { window.speechSynthesis.removeEventListener('voiceschanged', voices); window.speechSynthesis.cancel(); };
  }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }, [turns, stage]);
  useEffect(() => () => { abort.current?.abort(); generation.current += 1; photoUrls.current.forEach(url => URL.revokeObjectURL(url)); }, []);
  useEffect(() => { if (autoExample && !initialExample.current && nickname) { initialExample.current = true; startExercise(tutor.example); } }, [autoExample, nickname]);

  const add = (turn: Turn) => setTurns(old => [...old, turn].slice(-20));
  async function respond(text: string, verdict?: Verdict) {
    if (sending.current) return;
    sending.current = true;
    const token = generation.current;
    setNotice('');
    if (scenario === 'timeout' || scenario === 'unavailable') {
      setStage('queued');
      await new Promise(resolve => setTimeout(resolve, 600));
      if (token !== generation.current) return;
      setStage('idle'); sending.current = false; setRetry(text);
      setNotice(scenario === 'timeout' ? 'Something went wrong. The tutor took too long. Please try again.' : 'The tutor is unavailable. Your conversation is safe here. Please try again.');
      return;
    }
    if (scenario === 'busy' || busy) {
      setStage('queued');
      for (let p = 3; p > 0; p -= 1) {
        setPosition(p); await new Promise(resolve => setTimeout(resolve, 550));
        if (token !== generation.current) return;
      }
    }
    setStage('streaming');
    add({ role: 'tutor', text: '', verdict });
    const words = text.split(' ');
    for (let i = 0; i < words.length; i += 3) {
      await new Promise(resolve => setTimeout(resolve, 32));
      if (token !== generation.current) return;
      setTurns(old => { const last = old.at(-1); if (!last) return old; return [...old.slice(0, -1), { ...last, text: words.slice(0, i + 3).join(' ') }]; });
    }
    setStage('idle'); sending.current = false; setRetry(null);
  }
  function clearExercise() {
    abort.current?.abort(); generation.current += 1; sending.current = false; setStage('idle'); setTurns([]); setExercise(''); setHint(0); setPractice(false); setPracticeIndex(null); setResults({}); setPracticeAnswers({}); setInput(''); setPhoto(null); setNotice(''); setChecking(false); setSuggested(null); setSeveral(false); setRetry(null);
  }
  function startExercise(text: string, image?: string) {
    setExercise(id === 'maths' ? text.replace(/^solve\s+/i, '') : text); setHint(1); setPractice(false); setPracticeIndex(null); setTurns([{ role: 'student', text, image }]);
    record({ subject: id, topic, kind: 'question' });
    if (live) void requestAI('hint', [{ role: 'student', text, image }], 1);
    else void respond(hintFor(id, 1) ?? 'What do you notice about this exercise?');
  }
  function submit() {
    if (stage !== 'idle') { setNotice('Please wait for your current answer.'); return; }
    const error = input.trim() ? validateMessage(input) : photo ? null : validateMessage(input);
    if (error) { setNotice(error); return; }
    const text = input.trim();
    if (live) {
      const next: Turn[] = [...turns, { role: 'student', text: text || 'Help me with this photo.', image: photo?.url }];
      if (!exercise) { setExercise(text || 'Exercise in your photo'); setHint(1); record({ subject: id, topic, kind: 'question' }); }
      setTurns(next); setInput(''); setPhoto(null);
      void requestAI(checking || exercise ? 'attempt' : 'hint', next, Math.max(1, hint)); setChecking(false); return;
    }
    if (/\b\d{7,}\b|\b(my (full )?name is|my address is|my phone number)\b/i.test(text)) { add({ role: 'student', text: '[Personal details removed]' }); void respond('Please don’t share personal details here. A nickname is enough. Let’s return to your schoolwork.'); setInput(''); return; }
    if (/ignore (all|your|previous)|system prompt|bypass.*rules/i.test(text)) { add({ role: 'student', text }); void respond('I’ll stick to helping you learn. What schoolwork are you working on?'); setInput(''); return; }
    if (/kill|self.?harm|suicide|sexual|porn|weapon|hurt (myself|someone)/i.test(text)) { add({ role: 'student', text }); void respond('I can’t help with that. If you feel unsafe, please talk to a trusted adult. I’m here to help with schoolwork.'); setInput(''); return; }
    if (/casino|betting|crypto|dating/i.test(text)) { add({ role: 'student', text }); void respond('I’m here for schoolwork. Let’s try a question about Maths, English or Science.'); setInput(''); return; }
    const other: TutorId | null = /photosynth|plants|oxygen/i.test(text) ? 'science' : /past tense|grammar|verb|yesterday/i.test(text) ? 'english' : /\d\s*x|equation|solve/i.test(text) ? 'maths' : null;
    if (other && other !== id && enabled.includes(other)) { setSuggested(other); setNotice(`This looks like ${tutors[other].name}. You can switch tutor or stay here.`); return; }
    if (photo && scenario === 'unreadable') { setNotice('I can’t read this photo. Try a flat page, good light and one exercise in the frame — or type it instead.'); return; }
    if (photo && scenario === 'several' && !several) { setSeveral(true); setNotice('There are several exercises in this demo photo. Which one shall we work on?'); return; }
    if (!exercise || (photo && !checking)) {
      const current = text || tutor.example;
      if (photo) setNotice('Photo recognition is simulated. We’ll use this subject’s example exercise.');
      startExercise(current, photo?.url); setPhoto(null); setInput(''); return;
    }
    add({ role: 'student', text: text || 'Please check my working in this photo.', image: photo?.url });
    setInput(''); setPhoto(null);
    if (checking || practiceIndex !== null || /^(x\s*=\s*)?-?\d+|^went$|^carbon dioxide$|^co2$/i.test(text)) {
      checkAttempt(text); setChecking(false); return;
    }
    if (/answer|solution/i.test(text)) { showAnswer(); return; }
    if (/hint|help/i.test(text)) { anotherHint(); return; }
    void respond(`Let’s focus on “${exercise}”. ${hintFor(id, Math.min(hint + 1, 3))} Try writing your next step, then use Check my answer.`);
  }
  function checkAttempt(text: string) {
    const practiceItem = practiceIndex !== null ? tutor.practice[practiceIndex] : null;
    const maths = id === 'maths' ? checkMaths(practiceItem?.text ?? exercise, text) : null;
    const correct = maths?.decided ? maths.verdict === 'correct' : checkText(text, practiceItem?.answer ?? tutor.solution);
    const verdict = maths?.decided ? maths.verdict ?? 'not_yet_correct' : correct ? 'correct' : 'not_yet_correct';
    if (maths && !maths.decided) { void respond('I can’t check that format in this prototype. Try a simple equation or use the example. The real hub falls back to Gemma for this case.'); return; }
    if (practiceIndex !== null) setResults(old => ({ ...old, [practiceIndex]: correct }));
    record({ subject: id, topic, kind: verdict });
    void respond(maths?.message ?? (correct ? 'That’s correct! Nice work. Try the practice exercises to make it stick.' : 'Not yet — that’s okay. Look back at the clue and try again. We won’t give away the answer.'), verdict);
  }
  function anotherHint() {
    if (!exercise) { setNotice('Which exercise would you like help with?'); return; }
    const next = nextHintLevel(hint);
    if (live) { setHint(next); record({ subject: id, topic, kind: 'hint' }); void requestAI('hint', turns, next); return; } setHint(next); record({ subject: id, topic, kind: 'hint' });
    if (practiceIndex !== null) { void respond(id === 'maths' ? 'Undo the addition or subtraction first, then divide by the number next to x. Do the same to both sides.' : 'Use the idea from the original exercise. Look for the key word, then try your answer.'); return; }
    void respond(hintFor(id, next) ?? 'Try one step at a time.');
  }
  function showAnswer() {
    if (!exercise) { setNotice('Send an exercise first.'); return; }
    setHint(4);
    if (live) { void requestAI('answer', turns, 4); return; }
    const item = practiceIndex !== null ? tutor.practice[practiceIndex] : null;
    const answer = item ? `${item.text}\nThe answer is ${item.answer}. ${id === 'maths' ? 'Undo the addition or subtraction, then divide both sides by the coefficient of x.' : 'Compare this with the rule in your original exercise.'}` : tutor.answer;
    void respond(`${answer}\n\nAI can make mistakes. You can check with your teacher.`);
  }
  async function upload(selected?: File) {
    if (!selected) return;
    const error = validateImage(selected); if (error) { setNotice(error); return; }
    try {
      const bitmap = await createImageBitmap(selected);
      const scale = Math.min(1, 1024 / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement('canvas'); canvas.width = Math.round(bitmap.width * scale); canvas.height = Math.round(bitmap.height * scale);
      const context = canvas.getContext('2d'); if (!context) throw new Error('Image unavailable');
      context.drawImage(bitmap, 0, 0, canvas.width, canvas.height); bitmap.close();
      const url = canvas.toDataURL('image/jpeg', .85); setPhoto({ url, name: selected.name }); setNotice('');
    } catch { setNotice('We couldn’t open that photo. Choose a JPEG, PNG or WebP image.'); }
  }
  async function requestAI(action: 'hint' | 'attempt' | 'answer' | 'practice', history: Turn[], level: number) {
    if (sending.current) return;
    sending.current = true; setStage('streaming'); setNotice('');
    const token = generation.current;
    const controller = new AbortController(); abort.current = controller;
    setTurns([...history, { role: 'tutor', text: '' }]);
    let received = false;
    try {
      const response = await fetch('/api/tutor', { method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal, body: JSON.stringify({ subject: id, action, level, turns: history.map(({ role, text, image }) => ({ role, text, ...(image?.startsWith('data:') ? { image } : {}) })) }) });
      if (!response.ok) { const error = await response.json(); throw new Error(error.message || `AI request failed (${response.status}).`); }
      const reader = response.body?.getReader(); if (!reader) throw new Error('No response received.');
      const decoder = new TextDecoder(); let buffer = '';
      while (true) {
        const chunk = await reader.read(); if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        const lines = buffer.split('\n'); buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.startsWith('data: ') || line === 'data: [DONE]') continue;
          const event = JSON.parse(line.slice(6));
          if (event.type === 'error') throw new Error(event.errorText || 'AI request failed.');
          if (event.type === 'text-delta' || event.type === 'reasoning-delta') {
            if (token !== generation.current) return;
            const reasoning = event.type === 'reasoning-delta'; if (!reasoning) received = true;
            setTurns(old => { const last = old.at(-1); if (!last) return old; return [...old.slice(0, -1), { ...last, ...(reasoning ? { reasoning: (last.reasoning ?? '') + event.delta } : { text: last.text + event.delta }) }]; });
          }
        }
      }
      if (!received) throw new Error('The tutor returned no answer. Please check with your teacher.');
    } catch (error) {
      if (token !== generation.current) return;
      setNotice(controller.signal.aborted ? 'Stopped. Any partial hint is kept below.' : error instanceof Error ? error.message : 'The AI tutor could not respond.');
      setTurns(old => { const last = old.at(-1); return last && !last.text ? old.slice(0, -1) : old; });
    } finally {
      if (token === generation.current) { sending.current = false; setStage('idle'); abort.current = null; composer.current?.focus(); }
    }
  }
  function speak(index: number, text: string) {
    if (!voice) return;
    window.speechSynthesis.cancel();
    if (speaking === index) { setSpeaking(null); return; }
    const words = text.replace(/x\^2/g, 'x squared').replace(/sqrt\(([^)]+)\)/g, 'square root of $1').replace(/\*/g, ' times ').replace(/=/g, ' equals ');
    const utterance = new SpeechSynthesisUtterance(words); utterance.voice = voice; utterance.lang = 'en'; utterance.onend = () => setSpeaking(null); setSpeaking(index); window.speechSynthesis.speak(utterance);
  }
  if (!nickname) return <div className="ot-access"><ShieldCheck /><h1>Your conversation stays yours.</h1><p>Join with a nickname to start learning.</p><Button asChild><Link to="/">Join the hub</Link></Button></div>;
  if (!enabled.includes(id)) return <div className="ot-access"><h1>This tutor is switched off.</h1><Button asChild><Link to="/subjects">Choose another tutor</Link></Button></div>;
  return <div className="ot-chat-layout"><section className="ot-conversation"><div className="ot-chat-top"><div><span className={`ot-tutor-symbol ${id}`}>{tutor.short}</span><div><h1>{tutor.name} tutor<span className="ot-live-dot" /></h1><p>Not the answer. The way to get there.</p></div></div><Button variant="ghost" onClick={clearExercise} disabled={stage !== 'idle'}><Plus />New exercise</Button></div><div className="flex flex-wrap items-center gap-2 border-b border-border py-3"><Button variant={!live ? 'default' : 'outline'} size="sm" disabled={stage !== 'idle'} onClick={() => { clearExercise(); setLive(false); }}>Demo</Button><Button variant={live ? 'default' : 'outline'} size="sm" disabled={stage !== 'idle'} onClick={() => setConsent(true)}><Lightbulb />Live AI</Button><span className="text-xs text-muted-foreground">{live ? 'Online · exercise and attempts sent to Lovable AI' : 'Prepared hints · no AI requests'}</span></div>{consent && <div className="ot-notice"><p>Live AI needs internet. Your exercise, photos and attempts are sent to Lovable AI; your nickname is not. Avoid personal details. Nothing is saved by this app. AI requests use workspace credits.</p><Button size="sm" onClick={() => { clearExercise(); setLive(true); setConsent(false); composer.current?.focus(); }}>Enable live AI</Button><Button variant="ghost" size="sm" onClick={() => setConsent(false)}>Cancel</Button></div>}<div className="ot-chat-scroll">{!turns.length && <div className="ot-empty-chat"><span className="ot-big-question">?</span><span className="ot-page-kicker">EVERY QUESTION IS A GOOD START</span><h2>Let’s figure it out.</h2><p>Type your exercise or share a photo.<br />I’ll help you think, one step at a time.</p><Button variant="outline" onClick={() => startExercise(tutor.example)}>{tutor.example}<ArrowUpRight /></Button><div className="ot-photo-choices"><Button variant="ghost" onClick={() => camera.current?.click()}><Camera />Take a photo</Button><Button variant="ghost" onClick={() => file.current?.click()}><ImagePlus />Choose a photo</Button></div></div>}{turns.map((turn, i) => <div className={`ot-turn ${turn.role}`} key={i}>{turn.role === 'tutor' && <span className="ot-turn-avatar"><BookOpen size={17} /></span>}<div><span className="ot-turn-name">{turn.role === 'tutor' ? 'OpenTutor' : nickname}</span>{turn.verdict && <div className={`ot-verdict ${turn.verdict}`}><CircleCheck size={15} />{turn.verdict.replaceAll('_', ' ')}</div>}{turn.image && <img src={turn.image} alt="Your exercise photo" className="ot-turn-photo" />}{turn.reasoning && <details className="text-xs text-muted-foreground mt-2"><summary>Thinking summary</summary><p>{turn.reasoning}</p></details>}<TutorText text={turn.text} />{turn.role === 'tutor' && voice && (stage === 'idle' || i !== turns.length - 1) && <Button variant="ghost" size="sm" onClick={() => speak(i, turn.text)}>{speaking === i ? <Square /> : <Volume2 />}{speaking === i ? 'Stop' : 'Read aloud'}</Button>}</div></div>)}{stage === 'queued' && <div className="ot-queue"><span className="ot-queue-number">#{position}</span><div><strong>{queueMessage(position)}</strong><p>Your place is saved. Everyone gets a turn.</p></div><span className="ot-pulse">•••</span></div>}{stage === 'streaming' && <div className="ot-stream-label"><span className="ot-live-dot" />Thinking with you…</div>}<div ref={bottom} /></div>{practice && <div className="ot-practice"><div className="ot-section-title"><h2>Your turn × 3</h2><span>SAME IDEA, NEW CHALLENGE</span></div>{tutor.practice.map((item, i) => <div key={i} className={`ot-practice-item ${results[i] === true ? 'solved' : ''}`}><span className="ot-practice-number">0{i + 1}</span><div><strong>{item.text}</strong><form onSubmit={e => { e.preventDefault(); const answer = practiceAnswers[i] ?? ''; if (!answer.trim()) return; setPracticeIndex(i); const correct = id === 'maths' ? checkMaths(item.text, answer).verdict === 'correct' : checkText(answer, item.answer); setResults(old => ({ ...old, [i]: correct })); record({ subject: id, topic, kind: correct ? 'correct' : 'not_yet_correct' }); }}><input aria-label={`Answer to practice ${i + 1}`} placeholder="Your answer…" value={practiceAnswers[i] ?? ''} onChange={e => setPracticeAnswers(old => ({ ...old, [i]: e.target.value }))} /><Button variant="ghost" size="icon" type="submit" aria-label={`Check practice ${i + 1}`}><Check /></Button></form>{results[i] !== undefined && <small>{results[i] ? 'Correct. Nice work!' : 'Not yet. Try again — you’ve got this.'}</small>}</div><Button variant="ghost" size="icon" aria-label={`Work on practice ${i + 1}`} title="Work on this exercise" onClick={() => { setPracticeIndex(i); setHint(1); add({ role: 'tutor', text: `Let’s work on practice ${i + 1}: ${item.text}` }); }}><ChevronRight /></Button></div>)}</div>}{notice && <div className="ot-notice" role="status"><p>{notice}</p>{retry && <Button variant="outline" size="sm" onClick={() => { const text = retry; setScenario('normal'); setRetry(null); setNotice(''); setTimeout(() => { setScenario('normal'); add({ role: 'tutor', text }); }, 0); }}><RotateCcw />Try again</Button>}{suggested && <div className="ot-dialog-actions"><Button asChild size="sm"><Link to="/chat/$tutor" params={{ tutor: suggested }}>Switch to {tutors[suggested].name}</Link></Button><Button variant="ghost" size="sm" onClick={() => { setSuggested(null); startExercise(input); setInput(''); }}>Stay here</Button></div>}{several && <div className="ot-dialog-actions">{[1, 2, 3].map(n => <Button variant="outline" size="sm" key={n} onClick={() => { setSeveral(false); startExercise(tutor.example, photo?.url); setPhoto(null); setNotice(''); }}>Exercise {n}</Button>)}</div>}</div>}<div className="ot-composer-wrap"><div className="ot-actions">{live && stage !== 'idle' && <Button variant="outline" onClick={() => abort.current?.abort()}><Square />Stop generation</Button>}<Button variant="outline" disabled={stage !== 'idle'} onClick={anotherHint}><Lightbulb />Another hint</Button><Button variant="ghost" disabled={stage !== 'idle'} onClick={() => { if (!exercise) { setNotice('Which exercise does your answer belong to?'); return; } setChecking(true); setNotice('Write your answer or working below, or add a photo.'); }}><Check />Check my answer</Button><Button variant="ghost" disabled={stage !== 'idle'} onClick={showAnswer}><Eye />Show the answer</Button><Button variant="ghost" disabled={stage !== 'idle'} onClick={() => { if (!exercise) { setNotice('Which topic would you like to practise? Send an exercise first.'); return; } if (live) { void requestAI('practice', turns, Math.max(1, hint)); return; } setPractice(true); record({ subject: id, topic, kind: 'question' }); }}><Plus />Practice</Button></div>{photo && <div className="ot-photo-preview"><img src={photo.url} alt="Photo ready to send" /><span>{photo.name}</span><Button variant="ghost" size="icon" aria-label="Remove photo" onClick={() => setPhoto(null)}><X /></Button></div>}<form className="ot-composer" onSubmit={e => { e.preventDefault(); submit(); }}><Button variant="ghost" size="icon" type="button" aria-label="Add a photo" title="Add a photo" onClick={() => file.current?.click()} disabled={stage !== 'idle'}><ImagePlus /></Button><textarea ref={composer} autoFocus aria-label="Your question or answer" placeholder={checking ? 'Write your answer or working…' : 'What are you working on?'} rows={2} value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }} /><Button type="submit" size="icon" disabled={stage !== 'idle' || (!input.trim() && !photo)} aria-label="Send message"><ArrowUp /></Button></form><div className="ot-composer-foot"><span>{live ? 'Live AI · uses internet. Check with your teacher.' : 'Simulated tutor · AI can make mistakes. Check with your teacher.'}</span><span className={input.length > LIMITS.message ? 'ot-error' : ''}>{input.length.toLocaleString('en')} / 2,000</span></div></div><input ref={file} type="file" accept="image/jpeg,image/png,image/webp" className="hidden" onChange={e => { void upload(e.target.files?.[0]); e.target.value = ''; }} /><input ref={camera} type="file" accept="image/jpeg,image/png,image/webp" capture="environment" className="hidden" onChange={e => { void upload(e.target.files?.[0]); e.target.value = ''; }} /></section><aside className="ot-chat-aside"><span className="ot-page-kicker">YOUR WAY FORWARD</span><h2>Aha!<br />Not just<br /><span>an answer.</span></h2><div className="ot-hint-ladder">{['A nudge', 'The method', 'The next step', 'The answer'].map((label, i) => <div key={label} className={hint >= i + 1 ? 'done' : ''}><span>{hint >= i + 1 ? <Check size={12} /> : i + 1}</span>{label}{i === 3 && <Eye size={12} />}</div>)}</div><p className="ot-aside-note">The answer is yours to ask for.<br />The thinking is yours to keep.</p>{exercise && <div className="ot-current-exercise"><span>ON YOUR DESK</span><p>{exercise}</p></div>}<details className="ot-scenarios"><summary>Demo scenarios</summary><label>Try a state<select aria-label="Demo scenario" value={scenario} onChange={e => setScenario(e.target.value as Scenario)}><option value="normal">Normal</option><option value="busy">Busy queue</option><option value="unreadable">Unreadable photo</option><option value="several">Several exercises</option><option value="timeout">Model timeout</option><option value="unavailable">Model unavailable</option></select></label><p>Prepared states, not a live model.</p></details><Button variant="ghost" asChild><Link to="/subjects">Change subject <ArrowUpRight /></Link></Button><span className="ot-aside-privacy"><ShieldCheck size={15} />Only in this session</span></aside></div>;
}