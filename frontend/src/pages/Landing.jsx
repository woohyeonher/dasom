import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSession } from '../hooks/useSession'
import dasomLogo from '../assets/dasom_logo.png'

export default function Landing() {
  const [showJoin, setShowJoin] = useState(false)
  const [code, setCode] = useState('')
  const [copied, setCopied] = useState(false)
  const navigate = useNavigate()
  const { sessionCode, userId, error, loading, createSession, joinSession, clearError } = useSession()

  // User B navigates immediately after joining
  useEffect(() => {
    if (sessionCode && userId === 'b') {
      navigate('/intake', { state: { sessionCode, userId } })
    }
  }, [sessionCode, userId, navigate])

  const handleCopy = () => {
    navigator.clipboard.writeText(sessionCode).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  const handleContinue = () => {
    navigate('/intake', { state: { sessionCode, userId } })
  }

  const handleJoinSubmit = () => joinSession(code)

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleJoinSubmit()
  }

  const btnBase = {
    width: '100%',
    padding: '0.6rem 1.5rem',
    border: 'none',
    borderRadius: '8px',
    fontFamily: 'Inter, sans-serif',
    fontWeight: 500,
    fontSize: '15px',
    cursor: 'pointer',
    transition: 'opacity 0.15s',
  }

  return (
    <div style={{
      minHeight: '100vh',
      background: 'var(--color-background)',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '1.25rem 2rem',
    }}>
      {/* Logo */}
      <img
        src={dasomLogo}
        alt="Dasom logo"
        style={{ width: '280px', marginBottom: 0, mixBlendMode: 'darken' }}
      />

      {/* Wordmark */}
      <h1 style={{
        fontFamily: 'Inter, sans-serif',
        fontWeight: 600,
        fontSize: '24px',
        color: 'var(--color-text)',
        marginTop: 0,
        marginBottom: '0.25rem',
        letterSpacing: '-0.02em',
      }}>
        다솜 <span style={{ color: 'var(--color-primary)' }}>Dasom</span>
      </h1>

      {/* Tagline */}
      <p style={{
        fontFamily: 'Inter, sans-serif',
        fontSize: '14px',
        color: 'var(--color-text)',
        opacity: 0.5,
        margin: 0,
        marginBottom: '0.6rem',
        letterSpacing: '0.01em',
      }}>
        For when talking isn't enough.
      </p>

      {/* Explanation */}
      <p style={{
        fontFamily: 'Inter, sans-serif',
        fontSize: '13px',
        color: 'var(--color-text)',
        opacity: 0.6,
        lineHeight: 1.65,
        textAlign: 'center',
        maxWidth: '340px',
        margin: 0,
        marginBottom: '1.5rem',
      }}>
        Dasom gives each person a private space to share their side. AI agents simulate the
        conversation, and a neutral mediator finds the path forward. No confrontation. Just clarity.
      </p>

      {/* Code reveal after session creation */}
      {sessionCode && userId === 'a' ? (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: '0.85rem',
          width: '100%',
          maxWidth: '300px',
        }}>
          <p style={{
            fontFamily: 'Inter, sans-serif',
            fontSize: '13px',
            color: 'var(--color-text)',
            opacity: 0.6,
            margin: 0,
          }}>
            Your session code is:
          </p>

          <div style={{
            fontFamily: 'Inter, sans-serif',
            fontSize: '32px',
            fontWeight: 700,
            letterSpacing: '0.18em',
            color: 'var(--color-primary)',
            background: 'var(--color-surface)',
            border: '1.5px solid var(--color-border)',
            borderRadius: '10px',
            padding: '0.5rem 1.5rem',
            userSelect: 'all',
          }}>
            {sessionCode}
          </div>

          <button
            onClick={handleCopy}
            style={{
              ...btnBase,
              background: copied ? 'var(--color-surface-alt)' : 'transparent',
              color: copied ? 'var(--color-primary)' : 'var(--color-text)',
              border: '1.5px solid var(--color-border)',
              opacity: 1,
              fontSize: '13px',
            }}
          >
            {copied ? 'Copied!' : 'Copy code'}
          </button>

          <p style={{
            fontFamily: 'Inter, sans-serif',
            fontSize: '12px',
            color: 'var(--color-text)',
            opacity: 0.5,
            margin: 0,
            textAlign: 'center',
            lineHeight: 1.6,
            maxWidth: '260px',
          }}>
            Share this code with your partner before continuing.
          </p>

          <button
            onClick={handleContinue}
            style={{
              ...btnBase,
              background: 'var(--color-primary)',
              color: '#fff',
              marginTop: '0.25rem',
            }}
          >
            Continue
          </button>
        </div>
      ) : (
        /* Actions — start or join */
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: '0.5rem',
          width: '100%',
          maxWidth: '300px',
        }}>
          <button
            onClick={createSession}
            disabled={loading}
            style={{
              ...btnBase,
              background: 'var(--color-primary)',
              color: '#fff',
              opacity: loading && !showJoin ? 0.7 : 1,
            }}
          >
            {loading && !showJoin ? 'Starting…' : 'Start a session'}
          </button>

          {!showJoin ? (
            <button
              onClick={() => { clearError(); setShowJoin(true) }}
              style={{
                ...btnBase,
                background: 'transparent',
                color: 'var(--color-accent)',
                border: '1.5px solid var(--color-accent)',
              }}
            >
              Join with a code
            </button>
          ) : (
            <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: '0.45rem' }}>
              <input
                autoFocus
                value={code}
                onChange={e => { clearError(); setCode(e.target.value) }}
                onKeyDown={handleKeyDown}
                placeholder="Enter session code"
                maxLength={8}
                style={{
                  width: '100%',
                  padding: '0.6rem 1rem',
                  border: '1.5px solid var(--color-border)',
                  borderRadius: '8px',
                  fontFamily: 'Inter, sans-serif',
                  fontSize: '15px',
                  color: 'var(--color-text)',
                  background: 'var(--color-surface)',
                  outline: 'none',
                  textTransform: 'uppercase',
                  letterSpacing: '0.12em',
                  textAlign: 'center',
                  boxSizing: 'border-box',
                }}
              />
              <button
                onClick={handleJoinSubmit}
                disabled={loading || !code.trim()}
                style={{
                  ...btnBase,
                  background: 'var(--color-accent)',
                  color: '#fff',
                  opacity: loading || !code.trim() ? 0.6 : 1,
                }}
              >
                {loading ? 'Joining…' : 'Join'}
              </button>
            </div>
          )}

          {error && (
            <p style={{
              marginTop: '0.25rem',
              fontFamily: 'Inter, sans-serif',
              fontSize: '13px',
              color: '#C0574A',
              textAlign: 'center',
              maxWidth: '300px',
              lineHeight: 1.5,
            }}>
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
