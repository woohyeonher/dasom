import { useEffect, useRef, useCallback } from 'react'

/**
 * useSSE — manages a single EventSource connection.
 *
 * @param {string|null} url   Full SSE endpoint URL. Pass null to stay disconnected.
 * @param {object}      handlers  Map of event-type → callback(data).
 *                      Special key "error" receives the raw ErrorEvent.
 *                      Use the "message" key for unnamed SSE events.
 * @returns {{ close: () => void }}
 *
 * The hook reconnects automatically when `url` changes.
 * It tears down cleanly on unmount or when url goes null.
 */
export function useSSE(url, handlers) {
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers

  const esRef = useRef(null)

  const close = useCallback(() => {
    if (esRef.current) {
      esRef.current.close()
      esRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!url) return

    const es = new EventSource(url)
    esRef.current = es

    // Generic message handler — backend sends all events as unnamed SSE
    // with a JSON body: { type: "...", data: { ... } }
    es.onmessage = (evt) => {
      let parsed
      try {
        parsed = JSON.parse(evt.data)
      } catch {
        parsed = { type: 'raw', data: evt.data }
      }

      const { type, data } = parsed
      const h = handlersRef.current

      if (h[type]) {
        h[type](data)
      } else if (h['*']) {
        h['*'](parsed)
      }
    }

    es.onerror = (err) => {
      const h = handlersRef.current
      if (h['error']) h['error'](err)
    }

    return () => {
      es.close()
      esRef.current = null
    }
  }, [url])

  return { close }
}
