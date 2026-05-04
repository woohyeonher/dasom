const CONFIDENCE_PALETTE = {
  'Low':          { bg: '#E8A598', text: '#6B2A1E' },
  'Low-Medium':   { bg: '#E8C498', text: '#6B4A1E' },
  'Medium':       { bg: '#E8D898', text: '#6B5A1E' },
  'Medium-High':  { bg: '#A8C4A2', text: '#1E4A1E' },
  'High':         { bg: '#7C9E87', text: '#fff'    },
}

const FIELD_LABELS = {
  who: 'Who', when: 'When', where: 'Where',
  what: 'What', why: 'Why', how: 'How',
}

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

function ConfidenceBadge({ level }) {
  const palette = CONFIDENCE_PALETTE[level] ?? { bg: '#E8E4DF', text: '#2C2C2C' }
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 10px',
      borderRadius: '99px',
      fontSize: '12px',
      fontWeight: 500,
      background: palette.bg,
      color: palette.text,
      fontFamily: 'Inter, sans-serif',
    }}>
      {level ?? '—'}
    </span>
  )
}

export default function AnalysisPanel({ analysis }) {
  const structuredOutput = analysis ?? {}
  const confidence = analysis?.overall_confidence ?? null

  return (
    <div style={PANEL_SHELL}>
      <div style={{ ...PANEL_HEADER, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span>Analysis</span>
        {confidence && <ConfidenceBadge level={confidence} />}
      </div>

      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '1rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.75rem',
      }}>
        {!analysis ? (
          <p style={{
            fontFamily: 'Inter, sans-serif',
            fontSize: '13px',
            color: 'var(--color-text)',
            opacity: 0.35,
            fontStyle: 'italic',
            margin: 0,
            lineHeight: 1.7,
          }}>
            Analysis will build as you share more.
          </p>
        ) : (
          <>
            {/* 5W1H fields */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              {Object.entries(FIELD_LABELS).map(([key, label]) => {
                const val = structuredOutput[key]
                return (
                  <div key={key} style={{
                    padding: '0.5rem 0.75rem',
                    background: val ? 'var(--color-surface)' : 'var(--color-surface-alt)',
                    border: '1px solid var(--color-border)',
                    borderRadius: '8px',
                  }}>
                    <div style={{
                      fontFamily: 'Inter, sans-serif',
                      fontSize: '11px',
                      fontWeight: 600,
                      letterSpacing: '0.06em',
                      textTransform: 'uppercase',
                      color: 'var(--color-primary)',
                      marginBottom: '2px',
                    }}>
                      {label}
                    </div>
                    <div style={{
                      fontFamily: 'Inter, sans-serif',
                      fontSize: '13px',
                      color: 'var(--color-text)',
                      opacity: val ? 1 : 0.4,
                      lineHeight: 1.5,
                    }}>
                      {val || 'Not yet gathered'}
                    </div>
                  </div>
                )
              })}
            </div>

            {/* Emotional labels */}
            {structuredOutput.emotional_labels?.length > 0 && (
              <div>
                <div style={{
                  fontFamily: 'Inter, sans-serif',
                  fontSize: '11px',
                  fontWeight: 600,
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                  color: 'var(--color-primary)',
                  marginBottom: '0.4rem',
                }}>
                  Emotions
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
                  {structuredOutput.emotional_labels.map((label, i) => (
                    <span key={i} style={{
                      padding: '2px 8px',
                      borderRadius: '99px',
                      background: 'var(--color-message-bg)',
                      border: '1px solid var(--color-primary)',
                      fontFamily: 'Inter, sans-serif',
                      fontSize: '12px',
                      color: 'var(--color-primary)',
                    }}>
                      {label}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Needs */}
            {structuredOutput.needs?.length > 0 && (
              <div>
                <div style={{
                  fontFamily: 'Inter, sans-serif',
                  fontSize: '11px',
                  fontWeight: 600,
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                  color: 'var(--color-primary)',
                  marginBottom: '0.4rem',
                }}>
                  Underlying Needs
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
                  {structuredOutput.needs.map((need, i) => (
                    <span key={i} style={{
                      padding: '2px 8px',
                      borderRadius: '99px',
                      background: 'var(--color-surface-alt)',
                      border: '1px solid var(--color-border)',
                      fontFamily: 'Inter, sans-serif',
                      fontSize: '12px',
                      color: 'var(--color-text)',
                    }}>
                      {need}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
