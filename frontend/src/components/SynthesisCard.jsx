// Render inline **bold** and *italic* markdown as React elements (no dangerouslySetInnerHTML)
function renderInline(text) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g)
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i}>{part.slice(2, -2)}</strong>
    }
    if (part.startsWith('*') && part.endsWith('*')) {
      return <em key={i}>{part.slice(1, -1)}</em>
    }
    return part
  })
}

export default function SynthesisCard({ title, content, isPersonal }) {
  const paragraphs = (content ?? '').split('\n\n').filter(p => p.trim())

  return (
    <div style={isPersonal ? {
      background: 'var(--color-message-bg)',
      border: '1px solid var(--color-primary)',
      borderRadius: '12px',
      padding: '1.75rem 2rem',
    } : {}}>
      <h2 style={{
        fontFamily: 'Inter, sans-serif',
        fontWeight: 600,
        fontSize: '13px',
        letterSpacing: '0.07em',
        textTransform: 'uppercase',
        color: 'var(--color-primary)',
        marginBottom: '0.85rem',
        marginTop: 0,
      }}>
        {title}
      </h2>

      {paragraphs.map((para, i) => (
        <p key={i} style={{
          fontFamily: 'Inter, sans-serif',
          fontSize: '15px',
          color: 'var(--color-text)',
          lineHeight: 1.75,
          margin: 0,
          marginBottom: i < paragraphs.length - 1 ? '1rem' : 0,
        }}>
          {renderInline(para.trim())}
        </p>
      ))}
    </div>
  )
}
