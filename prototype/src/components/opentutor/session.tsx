import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import type { TutorId, Verdict } from '@/lib/opentutor';

export type Insight = { subject: TutorId; topic: string; kind: 'question' | 'hint' | Verdict };
type Session = {
  nickname: string; join: (name: string) => void; finish: () => void;
  insights: Insight[]; record: (item: Insight) => void; teacher: boolean; setTeacher: (value: boolean) => void;
  online: boolean; setOnline: (value: boolean) => void; busy: boolean; setBusy: (value: boolean) => void;
  enabled: TutorId[]; toggleTutor: (id: TutorId) => void; restart: () => void; expired: boolean;
};
const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [nickname, setNickname] = useState('');
  const [insights, setInsights] = useState<Insight[]>([]);
  const [teacher, setTeacher] = useState(false);
  const [online, setOnline] = useState(false);
  const [busy, setBusy] = useState(false);
  const [enabled, setEnabled] = useState<TutorId[]>(['maths', 'english', 'science']);
  const [expired, setExpired] = useState(false);
  const activity = useRef(Date.now());
  useEffect(() => {
    const touch = () => { activity.current = Date.now(); };
    window.addEventListener('pointerdown', touch); window.addEventListener('keydown', touch);
    const timer = window.setInterval(() => {
      if (nickname && Date.now() - activity.current >= 30 * 60_000) { setNickname(''); setExpired(true); }
    }, 10000);
    return () => { window.removeEventListener('pointerdown', touch); window.removeEventListener('keydown', touch); window.clearInterval(timer); };
  }, [nickname]);
  return <SessionContext.Provider value={{ nickname, join: name => { setNickname(name); setExpired(false); activity.current = Date.now(); }, finish: () => setNickname(''), insights, record: item => setInsights(old => [...old, item]), teacher, setTeacher, online, setOnline, busy, setBusy, enabled, toggleTutor: id => setEnabled(old => old.includes(id) ? old.filter(item => item !== id) : [...old, id]), restart: () => { setNickname(''); setInsights([]); setTeacher(false); setOnline(false); setBusy(false); }, expired }}>{children}</SessionContext.Provider>;
}
export function useSession() { const session = useContext(SessionContext); if (!session) throw new Error('Session provider is missing'); return session; }