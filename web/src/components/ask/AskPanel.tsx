import { LoaderCircle, Play, RotateCcw } from 'lucide-react'
import { useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import type { GuardConfig, PresetName, RunRequest, SourceInput, Trap } from '../../api/types'
import { PresetSwitch } from './PresetSwitch'
import { SourcesEditor } from './SourcesEditor'
import { TrapList } from './TrapList'

type Tab = 'generate' | 'answer' | 'traps'
const TABS: { id: Tab; label: string }[] = [
  { id: 'generate', label: 'Generate' },
  { id: 'answer', label: 'Bring your own answer' },
  { id: 'traps', label: 'Trap questions' },
]

interface Props {
  presets: Record<string, GuardConfig>
  traps: Trap[] | null
  trapsError: string | null
  busy: boolean
  demo: boolean
  initial?: { question: string; preset: PresetName; sources: SourceInput[] }
  onSubmit: (req: RunRequest) => void
  onReset: () => void
  canReset: boolean
}

const field =
  'mt-1.5 block w-full resize-y rounded-md border border-ink-300 bg-white px-3 py-2 text-sm text-ink-900 placeholder:text-ink-500 focus:border-action'

export function AskPanel({ presets, traps, trapsError, busy, demo, initial, onSubmit, onReset, canReset }: Props) {
  const [tab, setTab] = useState<Tab>('generate')
  const [question, setQuestion] = useState(initial?.question ?? '')
  const [answer, setAnswer] = useState('')
  const [sources, setSources] = useState<SourceInput[]>(initial?.sources ?? [])
  const [preset, setPreset] = useState<PresetName>(initial?.preset ?? 'balanced')
  const tabRefs = useRef<Record<Tab, HTMLButtonElement | null>>({ generate: null, answer: null, traps: null })

  const onTabKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = TABS.findIndex((t) => t.id === tab)
    let next = i
    if (e.key === 'ArrowRight') next = (i + 1) % TABS.length
    else if (e.key === 'ArrowLeft') next = (i - 1 + TABS.length) % TABS.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = TABS.length - 1
    else return
    e.preventDefault()
    const id = TABS[next]!.id
    setTab(id)
    tabRefs.current[id]?.focus()
  }

  const pickTrap = (t: Trap) => {
    setQuestion(t.question)
    setSources(t.sources ?? [])
    setTab('generate')
    window.setTimeout(() => tabRefs.current.generate?.focus(), 0)
  }

  const cleanSources = sources.filter((s) => s.text.trim()).map((s) => ({ ...s, title: s.title?.trim() || undefined }))
  const canSubmit = !busy && (demo || (tab === 'answer' ? answer.trim().length > 0 : question.trim().length > 0))

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return
    const req: RunRequest = { preset, sources: cleanSources.length ? cleanSources : undefined }
    if (question.trim()) req.question = question.trim()
    if (tab === 'answer') req.answer = answer.trim()
    onSubmit(req)
  }

  return (
    <section aria-labelledby="ask-title" className="rounded-lg border border-ink-200 bg-white shadow-panel">
      <h2 id="ask-title" className="sr-only">
        Ask
      </h2>
      <div role="tablist" aria-label="Input mode" onKeyDown={onTabKey} className="flex border-b border-ink-200 px-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            ref={(el) => {
              tabRefs.current[t.id] = el
            }}
            id={`tab-${t.id}`}
            role="tab"
            type="button"
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            tabIndex={tab === t.id ? 0 : -1}
            onClick={() => setTab(t.id)}
            className={`-mb-px border-b-2 px-2.5 py-2.5 text-sm font-medium transition-colors duration-150 ${
              tab === t.id ? 'border-ink-900 text-ink-900' : 'border-transparent text-ink-600 hover:text-ink-900'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'traps' ? (
        <div id="panel-traps" role="tabpanel" aria-labelledby="tab-traps" className="p-4">
          <TrapList traps={traps} error={trapsError} onPick={pickTrap} />
        </div>
      ) : (
        <form id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`} onSubmit={submit} className="space-y-4 p-4">
          <div>
            <label htmlFor="q" className="text-sm font-medium text-ink-900">
              Question {tab === 'answer' ? <span className="font-normal text-ink-500">(optional)</span> : null}
            </label>
            <textarea
              id="q"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              rows={tab === 'answer' ? 2 : 3}
              placeholder="Ask something factual, for example a question that needs a citation"
              className={field}
            />
          </div>
          {tab === 'answer' ? (
            <div>
              <label htmlFor="a" className="text-sm font-medium text-ink-900">
                Answer to check
              </label>
              <textarea
                id="a"
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                rows={7}
                placeholder="Paste an answer from any model. sAIfe checks it without generating a new one."
                className={field}
              />
            </div>
          ) : null}
          <SourcesEditor sources={sources} onChange={setSources} />
          <PresetSwitch value={preset} presets={presets} onChange={setPreset} />
          {demo ? (
            <p className="rounded-md bg-ink-50 px-3 py-2 text-xs text-ink-700">
              Demo mode replays one recorded run, whatever you type here.
            </p>
          ) : null}
          <div className="flex items-center gap-2">
            <button
              type="submit"
              disabled={!canSubmit}
              className="inline-flex flex-1 items-center justify-center gap-2 rounded-md bg-action px-4 py-2.5 text-sm font-semibold text-white hover:bg-action-hover disabled:cursor-not-allowed disabled:bg-ink-300 disabled:text-ink-700"
            >
              {busy ? (
                <LoaderCircle aria-hidden="true" className="h-4 w-4 motion-safe:animate-spin" />
              ) : (
                <Play aria-hidden="true" className="h-4 w-4" />
              )}
              {busy ? 'Checking…' : demo ? 'Replay the recorded run' : tab === 'answer' ? 'Check answer' : 'Generate and check'}
            </button>
            {canReset ? (
              <button
                type="button"
                onClick={onReset}
                className="inline-flex items-center gap-1.5 rounded-md border border-ink-300 px-3 py-2.5 text-sm font-medium text-ink-800 hover:bg-ink-50"
              >
                <RotateCcw aria-hidden="true" className="h-4 w-4" />
                Clear
              </button>
            ) : null}
          </div>
        </form>
      )}
    </section>
  )
}
