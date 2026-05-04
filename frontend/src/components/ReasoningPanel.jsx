import { useEffect, useRef } from 'react'

const PANEL_SHELL = {
  background: 'var(--color-surface)',
  border: '1px solid var(--color-border)',
  borderRadius: '12px',
  boxShadow: '0 1px 6px rgba(0,0,0,0.04)',
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
  height: '100%',
}

const PANEL_HEADER = {
  padding: '0.75rem 1.25rem',
  borderBottom: '1px solid var(--color-border)',
  fontFamily: 'Inter, sans-serif',
  fontWeight: 600,
  fontSize: '12px',
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  color: 'var(--color-primary)',
  flexShrink: 0,
}

export default function ReasoningPanel({ reasoning }) {
  const scrollRef = useRef(null)

  // Scroll to top whenever a new entry arrives (newest is always at top)
  useEffect(() => {
    if (reasoning.length > 0) {
      scrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
    }
  }, [reasoning.length])

  // Display newest first
  const entries = [...reasoning].reverse()

  return (
    <div style={PANEL_SHELL}>
      <div style={PANEL_HEADER}>Reasoning</div>

      <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto', padding: '1rem' }}>
        {entries.length === 0 ? (
          <p style={{
            fontFamily: 'Inter, sans-serif',
            fontSize: '13px',
            color: 'var(--color-text)',
            opacity: 0.35,
            lineHeight: 1.7,
            margin: 0,
            fontStyle: 'italic',
          }}>
            The agent's thinking will appear here as you share your story.
          </p>
        ) : (
          entries.map((text, i) => {
            const turnNumber = reasoning.length - i
            const isNewest = i === 0
            return (
              <div key={turnNumber}>
                {i > 0 && (
                  <div style={{
                    borderTop: '1px solid var(--color-border)',
                    margin: '0.85rem 0',
                  }} />
                )}

                <span style={{
                  display: 'block',
                  fontFamily: 'Inter, sans-serif',
                  fontSize: '10px',
                  fontWeight: 600,
                  letterSpacing: '0.07em',
                  textTransform: 'uppercase',
                  color: 'var(--color-text)',
                  opacity: 0.35,
                  marginBottom: '0.3rem',
                }}>
                  Turn {turnNumber}
                </span>

                <p style={{
                  fontFamily: 'Inter, sans-serif',
                  fontSize: '13px',
                  lineHeight: 1.7,
                  margin: 0,
                  color: isNewest ? '#2C2C2C' : 'var(--color-text)',
                  opacity: isNewest ? 1 : 0.6,
                  fontStyle: isNewest ? 'normal' : 'italic',
                }}>
                  {text}
                </p>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
