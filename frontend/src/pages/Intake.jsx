import { useState, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useSSE } from '../hooks/useSSE'
import SessionNotice from '../components/SessionNotice'
import ErrorState from '../components/ErrorState'
import ChatPanel from '../components/ChatPanel'
import ReasoningPanel from '../components/ReasoningPanel'
import AnalysisPanel from '../components/AnalysisPanel'

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export default function Intake() {
  const location = useLocation()
  const navigate = useNavigate()
  const { sessionCode, userId } = location.state ?? {}

  const [messages, setMessages] = useState([])
  const [streamingText, setStreamingText] = useState('')
  const [reasoning, setReasoning] = useState([])
  const [analysis, setAnalysis] = useState(null)
  const [sending, setSending] = useState(false)

  const streamingRef = useRef('')

  const sseUrl = sessionCode && userId
    ? `${API_BASE}/stream/intake/${sessionCode}/${userId}`
    : null

  const commitStreaming = () => {
    if (streamingRef.current) {
      const committed = streamingRef.current
      setMessages(prev => [...prev, { role: 'agent', text: committed }])
      streamingRef.current = ''
      setStreamingText('')
    }
  }

  useSSE(sseUrl, {
    token: (data) => {
      const chunk = typeof data === 'string' ? data : (data?.text ?? '')
      streamingRef.current += chunk
      setStreamingText(streamingRef.current)
    },
    turn_complete: () => {
      console.log('[turn_complete] received — streamingRef length:', streamingRef.current.length, '— committing text and re-enabling input')
      commitStreaming()
      setSending(false)
    },
    reasoning: (data) => {
      // turn_complete should have already committed; this is a fallback.
      commitStreaming()
      const summary = typeof data === 'string' ? data : (data?.summary ?? data?.text ?? '')
      if (summary) setReasoning(prev => [...prev, summary])
    },
    analysis_update: (data) => {
      setAnalysis(data)
    },
    done: () => {
      commitStreaming()
      navigate('/waiting', { state: { sessionCode, userId } })
    },
  })

  // Guard: if no session in router state, session has ended or user navigated directly
  if (!sessionCode || !userId) {
    return <ErrorState message="Your session has ended." />
  }

  const sendMessage = async (text) => {
    setSending(true)
    setMessages(prev => [...prev, { role: 'user', text }])
    try {
      await fetch(`${API_BASE}/intake/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_code: sessionCode, user_id: userId, message: text }),
      })
    } catch {
      setMessages(prev => [...prev, {
        role: 'error',
        text: 'Something went wrong sending that. Please try again.',
      }])
    } finally {
      setSending(false)
    }
  }

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100vh',
      background: 'var(--color-background)',
    }}>
      <SessionNotice sessionCode={sessionCode} />

      <div style={{
        flex: 1,
        display: 'flex',
        gap: '0.75rem',
        padding: '0.75rem',
        overflow: 'hidden',
        minHeight: 0,
      }}>
        <div style={{ flex: '0 0 45%', minHeight: 0 }}>
          <ChatPanel
            messages={messages}
            streamingText={streamingText}
            onSend={sendMessage}
            disabled={sending}
            onStreamTimeout={(text) => {
              setMessages(prev => [...prev, { role: 'agent', text }])
              streamingRef.current = ''
              setStreamingText('')
              setSending(false)
            }}
          />
        </div>
        <div style={{ flex: '0 0 25%', minHeight: 0 }}>
          <ReasoningPanel reasoning={reasoning} />
        </div>
        <div style={{ flex: 1, minHeight: 0 }}>
          <AnalysisPanel analysis={analysis} />
        </div>
      </div>
    </div>
  )
}
