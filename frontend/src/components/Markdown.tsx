'use client'
import ReactMarkdown from 'react-markdown'

const components: any = {
  p: ({children}:any) => <p className="mb-1 last:mb-0">{children}</p>,
  pre: ({children}:any) => <pre className="bg-gray-950 rounded p-2 my-1.5 overflow-x-auto text-[10px] leading-relaxed">{children}</pre>,
  code: ({children}:any) => <code className="bg-gray-700 px-1 rounded text-[10px]">{children}</code>,
  ul: ({children}:any) => <ul className="list-disc pl-4 my-1">{children}</ul>,
  ol: ({children}:any) => <ol className="list-decimal pl-4 my-1">{children}</ol>,
  li: ({children}:any) => <li className="my-0.5">{children}</li>,
  h3: ({children}:any) => <h3 className="text-xs font-bold text-gray-200 mt-2 mb-1">{children}</h3>,
  h4: ({children}:any) => <h4 className="text-[11px] font-semibold text-gray-300 mt-1.5 mb-0.5">{children}</h4>,
  strong: ({children}:any) => <strong className="font-semibold text-gray-100">{children}</strong>,
  table: ({children}:any) => (
    <div className="overflow-x-auto my-1.5">
      <table className="w-full border-collapse text-[10px]">{children}</table>
    </div>
  ),
  thead: ({children}:any) => <thead>{children}</thead>,
  tbody: ({children}:any) => <tbody>{children}</tbody>,
  tr: ({children}:any) => <tr className="border-b border-gray-700">{children}</tr>,
  th: ({children}:any) => <th className="border border-gray-600 px-2 py-1 bg-gray-700 text-left font-medium text-gray-200">{children}</th>,
  td: ({children}:any) => <td className="border border-gray-600 px-2 py-1 text-gray-300">{children}</td>,
}

export default function Markdown({ text }: { text: string }) {
  return <ReactMarkdown components={components}>{text}</ReactMarkdown>
}
