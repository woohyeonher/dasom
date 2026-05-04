import { useState, useCallback } from 'react'

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

/**
 * useSession — session lifecycle management.
 *
 * Exposes:
 *   sessionCode  — string | null
 *   userId       — string | null  ("a" or "b")
 *   error        — string | null
 *   loading      — bool
 *   createSession()          — POST /session/create → sets sessionCode + userId "a"
 *   joinSession(code)        — POST /session/join/{code} → sets sessionCode + userId "b"
 *   clearError()
 */
export function useSession() {
  const [sessionCode, setSessionCode] = useState(null)
  const [userId, setUserId] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const createSession = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_BASE}/session/create`, { method: 'POST' })
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      const data = await res.json()
      setSessionCode(data.session_code)
      setUserId('a')
    } catch (err) {
      setError('Could not start a session. Please try again.')
    } finally {
      setLoading(false)
    }
  }, [])

  const joinSession = useCallback(async (code) => {
    const trimmed = code.trim().toUpperCase()
    if (!trimmed) {
      setError('Please enter a session code.')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_BASE}/session/join/${trimmed}`, { method: 'POST' })
      if (res.status === 404) throw new Error('not_found')
      if (res.status === 409) throw new Error('full')
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      const data = await res.json()
      setSessionCode(data.session_code)
      setUserId('b')
    } catch (err) {
      if (err.message === 'not_found') {
        setError('That code wasn\'t found. Double-check and try again.')
      } else if (err.message === 'full') {
        setError('This session is already full.')
      } else {
        setError('Could not join the session. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }, [])

  const clearError = useCallback(() => setError(null), [])

  return { sessionCode, userId, error, loading, createSession, joinSession, clearError }
}
