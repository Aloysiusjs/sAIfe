import { Plus, Trash2, Upload } from 'lucide-react'
import type { ChangeEvent } from 'react'
import type { SourceInput } from '../../api/types'

interface Props {
  sources: SourceInput[]
  onChange: (sources: SourceInput[]) => void
}

function nextId(sources: SourceInput[]) {
  let n = sources.length + 1
  while (sources.some((s) => s.id === `s${n}`)) n++
  return `s${n}`
}

export function SourcesEditor({ sources, onChange }: Props) {
  const update = (i: number, patch: Partial<SourceInput>) =>
    onChange(sources.map((s, j) => (j === i ? { ...s, ...patch } : s)))

  const onFiles = async (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    e.target.value = ''
    const added: SourceInput[] = []
    for (const f of files) {
      const text = await f.text()
      added.push({ id: nextId([...sources, ...added]), title: f.name, text })
    }
    onChange([...sources, ...added])
  }

  return (
    <fieldset>
      <legend className="text-sm font-medium text-ink-900">
        Sources <span className="font-normal text-ink-500">(optional)</span>
      </legend>
      <p className="mt-0.5 text-xs text-ink-500">Paste documents the answer should be grounded in. Quotes are checked against them by code.</p>
      <ul className="mt-2 space-y-2">
        {sources.map((s, i) => (
          <li key={s.id} className="rounded-md border border-ink-200 bg-ink-50 p-2">
            <div className="flex items-center gap-2">
              <span className="font-mono text-2xs text-ink-600">{s.id}</span>
              <label className="sr-only" htmlFor={`src-title-${s.id}`}>
                Title for source {s.id}
              </label>
              <input
                id={`src-title-${s.id}`}
                value={s.title ?? ''}
                onChange={(e) => update(i, { title: e.target.value })}
                placeholder="Title"
                className="min-w-0 flex-1 rounded border border-ink-300 bg-white px-2 py-1 text-sm placeholder:text-ink-500"
              />
              <button
                type="button"
                onClick={() => onChange(sources.filter((_, j) => j !== i))}
                className="rounded p-1 text-ink-600 hover:bg-ink-100 hover:text-contradicted-fg"
                aria-label={`Remove source ${s.id}`}
              >
                <Trash2 aria-hidden="true" className="h-4 w-4" />
              </button>
            </div>
            <label className="sr-only" htmlFor={`src-text-${s.id}`}>
              Text of source {s.id}
            </label>
            <textarea
              id={`src-text-${s.id}`}
              value={s.text}
              onChange={(e) => update(i, { text: e.target.value })}
              rows={4}
              placeholder="Paste the source text"
              className="mt-2 block w-full resize-y rounded border border-ink-300 bg-white px-2 py-1.5 text-sm placeholder:text-ink-500"
            />
          </li>
        ))}
      </ul>
      <div className="mt-2 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onChange([...sources, { id: nextId(sources), title: '', text: '' }])}
          className="inline-flex items-center gap-1.5 rounded-md border border-ink-300 bg-white px-2.5 py-1.5 text-xs font-medium text-ink-800 hover:bg-ink-50"
        >
          <Plus aria-hidden="true" className="h-3.5 w-3.5" />
          Paste a source
        </button>
        <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-ink-300 bg-white px-2.5 py-1.5 text-xs font-medium text-ink-800 focus-within:outline focus-within:outline-2 focus-within:outline-action hover:bg-ink-50">
          <Upload aria-hidden="true" className="h-3.5 w-3.5" />
          Upload .txt or .md
          <input type="file" accept=".txt,.md,text/plain,text/markdown" multiple onChange={onFiles} className="sr-only" />
        </label>
      </div>
    </fieldset>
  )
}
