import dasomLogo from '../assets/dasom_logo.png'

export default function SessionNotice({ sessionCode }) {
  return (
    <div style={{
      background: 'var(--color-surface-alt)',
      borderBottom: '1px solid var(--color-border)',
      padding: '0.35rem 1.25rem',
      fontFamily: 'Inter, sans-serif',
      fontSize: '12px',
      color: 'var(--color-text)',
      opacity: 0.65,
      flexShrink: 0,
      display: 'flex',
      alignItems: 'center',
      position: 'relative',
    }}>
      <img
        src={dasomLogo}
        alt="Dasom logo"
        style={{ height: '40px', width: 'auto', flexShrink: 0, mixBlendMode: 'darken' }}
      />
      <span style={{
        position: 'absolute',
        left: 0,
        right: 0,
        textAlign: 'center',
        pointerEvents: 'none',
      }}>
        Please keep this tab open. Closing or refreshing will end your session.
      </span>
      {sessionCode && (
        <span style={{
          marginLeft: 'auto',
          fontFamily: 'Inter, sans-serif',
          fontSize: '11px',
          letterSpacing: '0.1em',
          color: 'var(--color-text)',
          opacity: 0.5,
          flexShrink: 0,
        }}>
          {sessionCode}
        </span>
      )}
    </div>
  )
}
