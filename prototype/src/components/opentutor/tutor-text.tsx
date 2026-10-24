import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';

export function TutorText({ text }: { text: string }) {
  const markdown = text.replace(/\\\((.*?)\\\)/gs, (_, value: string) => `$${value}$`).replace(/\\\[(.*?)\\\]/gs, (_, value: string) => `$$${value}$$`);
  return <div className="overflow-x-auto"><ReactMarkdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]}>{markdown || 'Thinking…'}</ReactMarkdown></div>;
}