import { useCallback, useEffect, useReducer, useRef } from 'react'
import { ApiError, api, streamRun } from '../api/client'
import type { RunRequest, Source } from '../api/types'
import { loadDemoRun, replayDemo } from '../demo/replay'
import { initialRunState, runReducer } from './runState'

export function useRun(demo: boolean) {
  const [state, dispatch] = useReducer(runReducer, initialRunState)
  const stop = useRef<(() => void) | null>(null)

  const cancel = useCallback(() => {
    stop.current?.()
    stop.current = null
  }, [])

  useEffect(() => cancel, [cancel])

  const start = useCallback(
    async (req: RunRequest) => {
      cancel()
      if (demo) {
        const run = await loadDemoRun()
        dispatch({ type: 'connect', sources: run.sources, question: run.question, preset: run.preset })
        stop.current = replayDemo(run, (event) => dispatch({ type: 'event', event }))
        return
      }

      const sources: Source[] = (req.sources ?? []).map((s) => ({ ...s, title: s.title ?? null, url: null }))
      dispatch({ type: 'connect', sources, question: req.question ?? null, preset: req.preset })
      let runId: string
      try {
        runId = (await api.createRun(req)).run_id
      } catch (err) {
        const message =
          err instanceof ApiError
            ? `The server rejected the run: ${err.message}`
            : 'Could not reach the sAIfe server. Start it, or open the demo with ?demo=1.'
        dispatch({ type: 'error', message })
        return
      }
      let finished = false
      stop.current = streamRun(runId, {
        onEvent: (event) => {
          dispatch({ type: 'event', event })
          if (event.type === 'run.completed') {
            finished = true
            // The completed event normally carries the report; fetch it if it didn't.
            if (!event.data.report) {
              api.report(runId).then(
                (report) => dispatch({ type: 'report', report }),
                () => dispatch({ type: 'error', message: 'The run finished but its report could not be loaded.' }),
              )
            }
          }
          if (event.type === 'run.failed') finished = true
        },
        onError: (message) => {
          if (!finished) dispatch({ type: 'error', message })
        },
      })
    },
    [cancel, demo],
  )

  const reset = useCallback(() => {
    cancel()
    dispatch({ type: 'reset' })
  }, [cancel])

  return { state, start, reset }
}
