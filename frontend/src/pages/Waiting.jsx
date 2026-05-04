import { useState, useRef, useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useSSE } from '../hooks/useSSE'
import SessionNotice from '../components/SessionNotice'
import ErrorState from '../components/ErrorState'

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

const SENTENCES = [
  'Your partner is sharing their side of the story…',
  'Both perspectives are being carefully prepared…',
  'The agents are getting ready to understand your conflict…',
  'Almost there — waiting for both sides to be ready…',
]

const STAGE_ORDER = ['waiting', 'simulating', 'mediating', 'synthesizing']

const STAGE_LABELS = {
  waiting:     'Both partners ready',
  simulating:  'Simulation in progress',
  mediating:   'Mediator reviewing',
  synthesizing:'Preparing your results',
}

// ── Step dot (circle indicator in the progress bar) ──────────────────────────

function StepDot({ isDone, isActive }) {
  return (
    <div style={{
      width: '20px',
      height: '20px',
      borderRadius: '50%',
      flexShrink: 0,
      background: isDone || isActive ? 'var(--color-primary)' : 'var(--color-border)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      transition: 'background 0.4s',
    }}>
      {isDone ? (
        <svg width="10" height="8" viewBox="0 0 10 8" fill="none">
          <path
            d="M1 4L3.5 6.5L9 1"
            stroke="white"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      ) : isActive ? (
        <div style={{
          width: '8px',
          height: '8px',
          borderRadius: '50%',
          background: '#fff',
          animation: 'pulse-gentle 1.5s ease-in-out infinite',
        }} />
      ) : null}
    </div>
  )
}

// ── Chat bubble for one simulation turn ──────────────────────────────────────

function TurnBubble({ speaker, text, streaming }) {
  const isA = speaker === 'A'
  const avatarStyle = {
    width: '22px',
    height: '22px',
    borderRadius: '50%',
    fontFamily: 'Inter, sans-serif',
    fontSize: '11px',
    fontWeight: 700,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    alignSelf: 'flex-end',
    marginBottom: '1px',
  }

  return (
    <div style={{
      display: 'flex',
      justifyContent: isA ? 'flex-start' : 'flex-end',
      gap: '0.45rem',
      marginBottom: '0.7rem',
    }}>
      {isA && (
        <div style={{ ...avatarStyle, background: 'var(--color-primary)', color: '#fff' }}>
          A
        </div>
      )}

      <div style={{
        maxWidth: '68%',
        padding: '0.55rem 0.9rem',
        borderRadius: isA ? '4px 14px 14px 14px' : '14px 4px 14px 14px',
        fontFamily: 'Inter, sans-serif',
        fontSize: '14px',
        lineHeight: 1.65,
        background: isA ? '#7C9E8718' : '#C4A88218',
        border: `1px solid ${isA ? 'var(--color-primary)' : 'var(--color-accent)'}`,
        color: 'var(--color-text)',
        whiteSpace: 'pre-wrap',
      }}>
        {text}
        {streaming && (
          <span style={{
            display: 'inline-block',
            width: '5px',
            height: '13px',
            background: isA ? 'var(--color-primary)' : 'var(--color-accent)',
            borderRadius: '2px',
            marginLeft: '2px',
            verticalAlign: 'text-bottom',
            animation: 'cursor-blink 1s ease-in-out infinite',
          }} />
        )}
      </div>

      {!isA && (
        <div style={{ ...avatarStyle, background: 'var(--color-accent)', color: '#fff' }}>
          B
        </div>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Waiting() {
  const location = useLocation()
  const navigate = useNavigate()
  const { sessionCode, userId } = location.state ?? {}

  // Pre-simulation rotating sentences
  const [sentenceIdx, setSentenceIdx] = useState(0)
  const [visible, setVisible] = useState(true)

  // Simulation progress
  const [stage, setStage] = useState('waiting')
  const [turns, setTurns] = useState([])
  const [streamingTurn, setStreamingTurn] = useState(null) // { speaker, text } | null
  const [roundCount, setRoundCount] = useState(0)

  const streamingRef = useRef('')
  const streamingSpeakerRef = useRef(null)
  const bubblesEndRef = useRef(null)

  const advanceStage = (newStage) => {
    setStage(prev => {
      const prevIdx = STAGE_ORDER.indexOf(prev)
      const newIdx = STAGE_ORDER.indexOf(newStage)
      return newIdx > prevIdx ? newStage : prev
    })
  }

  // Rotating sentences (pre-simulation only)
  useEffect(() => {
    const interval = setInterval(() => {
      setVisible(false)
      setTimeout(() => {
        setSentenceIdx(prev => (prev + 1) % SENTENCES.length)
        setVisible(true)
      }, 350)
    }, 4000)
    return () => clearInterval(interval)
  }, [])

  // On mount: check if synthesis already exists (reconnect after refresh).
  // If found, skip Waiting entirely and go straight to Result.
  useEffect(() => {
    if (!sessionCode || !userId) return
    fetch(`${API_BASE}/result/${sessionCode}/${userId}`)
      .then(res => { if (res.ok) navigate('/result', { state: { sessionCode, userId } }) })
      .catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-scroll chat bubbles to bottom
  useEffect(() => {
    bubblesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, streamingTurn])

  const sseUrl = sessionCode
    ? `${API_BASE}/stream/simulation/${sessionCode}`
    : null

  useSSE(sseUrl, {
    simulation_token: (data) => {
      const speaker = data?.speaker
      const text = data?.text ?? ''
      if (speaker !== streamingSpeakerRef.current) {
        streamingRef.current = ''
        streamingSpeakerRef.current = speaker
      }
      streamingRef.current += text
      setStreamingTurn({ speaker, text: streamingRef.current })
      advanceStage('simulating')
    },

    simulation_turn: (data) => {
      const speaker = data?.speaker
      const text = data?.text ?? ''
      setTurns(prev => [...prev, { speaker, text }])
      streamingRef.current = ''
      streamingSpeakerRef.current = null
      setStreamingTurn(null)
      if (speaker === 'B') setRoundCount(prev => prev + 1)
      advanceStage('simulating')
    },

    reasoning: () => {},

    mediator_intervention: () => {
      advanceStage('mediating')
    },

    synthesis_token: () => {
      advanceStage('synthesizing')
    },

    synthesis_complete: () => {},

    done: () => {
      navigate('/result', { state: { sessionCode, userId } })
    },
  })

  if (!sessionCode) {
    return <ErrorState message="Your session has ended." />
  }

  const isWaiting = stage === 'waiting'
  const stageIndex = STAGE_ORDER.indexOf(stage)

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100vh',
      background: 'var(--color-background)',
    }}>
      <SessionNotice sessionCode={sessionCode} />

      {isWaiting ? (
        /* ── Pre-simulation: pulsing animation + waiting message ── */
        <div style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '1.75rem',
          padding: '2rem',
        }}>
          <div style={{ position: 'relative', width: '72px', height: '72px' }}>
            <div style={{
              position: 'absolute',
              inset: 0,
              borderRadius: '50%',
              background: 'var(--color-primary)',
              opacity: 0.18,
              animation: 'pulse-ring 2.2s ease-in-out infinite',
            }} />
            <div style={{
              position: 'absolute',
              inset: '12px',
              borderRadius: '50%',
              background: 'var(--color-primary)',
              animation: 'pulse-gentle 2.2s ease-in-out infinite',
            }} />
          </div>

          {/* Primary explanation — always visible */}
          <p style={{
            fontFamily: 'Inter, sans-serif',
            fontSize: '15px',
            fontWeight: 500,
            color: 'var(--color-text)',
            opacity: 0.75,
            textAlign: 'center',
            maxWidth: '320px',
            lineHeight: 1.65,
            margin: 0,
          }}>
            Waiting for your partner to finish sharing their side…
          </p>

          {/* Rotating secondary flavor text */}
          <p style={{
            fontFamily: 'Inter, sans-serif',
            fontSize: '13px',
            color: 'var(--color-text)',
            opacity: visible ? 0.4 : 0,
            textAlign: 'center',
            maxWidth: '300px',
            lineHeight: 1.7,
            margin: 0,
            transition: 'opacity 0.35s ease',
          }}>
            {SENTENCES[sentenceIdx]}
          </p>
        </div>
      ) : (
        /* ── Active simulation: progress bar + live chat bubbles ── */
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

          {/* Progress header */}
          <div style={{
            padding: '1rem 1.5rem 0.85rem',
            borderBottom: '1px solid var(--color-border)',
            background: 'var(--color-surface)',
            flexShrink: 0,
          }}>
            {/* Stage steps row */}
            <div style={{
              display: 'flex',
              alignItems: 'flex-start',
              maxWidth: '600px',
              margin: '0 auto',
            }}>
              {STAGE_ORDER.map((s, i) => {
                const isDone   = i < stageIndex
                const isActive = i === stageIndex
                const isFuture = i > stageIndex
                const isLast   = i === STAGE_ORDER.length - 1
                return (
                  <div
                    key={s}
                    style={{ display: 'flex', alignItems: 'flex-start', flex: isLast ? '0 0 auto' : 1 }}
                  >
                    {/* Circle + label */}
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.3rem' }}>
                      <StepDot isDone={isDone} isActive={isActive} />
                      <span style={{
                        fontFamily: 'Inter, sans-serif',
                        fontSize: '10px',
                        fontWeight: isActive ? 600 : 400,
                        color: isFuture ? 'var(--color-text)' : 'var(--color-primary)',
                        opacity: isFuture ? 0.3 : 1,
                        textAlign: 'center',
                        maxWidth: '72px',
                        lineHeight: 1.3,
                        transition: 'opacity 0.4s, color 0.4s',
                      }}>
                        {STAGE_LABELS[s]}
                      </span>
                    </div>

                    {/* Connector line */}
                    {!isLast && (
                      <div style={{
                        flex: 1,
                        height: '1px',
                        background: isDone ? 'var(--color-primary)' : 'var(--color-border)',
                        marginTop: '9px',
                        marginLeft: '4px',
                        marginRight: '4px',
                        transition: 'background 0.4s',
                      }} />
                    )}
                  </div>
                )
              })}
            </div>

            {/* Round counter */}
            {roundCount > 0 && (
              <p style={{
                fontFamily: 'Inter, sans-serif',
                fontSize: '12px',
                color: 'var(--color-text)',
                opacity: 0.45,
                textAlign: 'center',
                margin: '0.6rem 0 0',
              }}>
                Round {roundCount} of conversation
              </p>
            )}
          </div>

          {/* Scrollable chat bubbles */}
          <div style={{
            flex: 1,
            overflowY: 'auto',
            padding: '1.25rem 1.5rem',
          }}>
            <div style={{ maxWidth: '640px', margin: '0 auto' }}>
              {turns.map((turn, i) => (
                <TurnBubble key={i} speaker={turn.speaker} text={turn.text} />
              ))}
              {streamingTurn && (
                <TurnBubble
                  speaker={streamingTurn.speaker}
                  text={streamingTurn.text}
                  streaming
                />
              )}
              <div ref={bubblesEndRef} />
            </div>
          </div>

        </div>
      )}
    </div>
  )
}
