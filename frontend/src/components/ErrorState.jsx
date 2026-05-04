import { useNavigate } from 'react-router-dom'

export default function ErrorState({ message }) {
  const navigate = useNavigate()

  return (
    <div style={{
      minHeight: '100vh',
      background: 'var(--color-background)',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: '1.5rem',
      padding: '2rem',
    }}>
      <p style={{
        fontFamily: 'Inter, sans-serif',
        fontSize: '15px',
        color: 'var(--color-text)',
        opacity: 0.65,
        textAlign: 'center',
        maxWidth: '380px',
        lineHeight: 1.7,
        margin: 0,
      }}>
        {message}
      </p>
      <button
        onClick={() => navigate('/')}
        style={{
          padding: '0.65rem 1.5rem',
          background: 'var(--color-primary)',
          color: '#fff',
          border: 'none',
          borderRadius: '8px',
          fontFamily: 'Inter, sans-serif',
          fontWeight: 500,
          fontSize: '14px',
          cursor: 'pointer',
        }}
      >
        Start Over
      </button>
    </div>
  )
}
