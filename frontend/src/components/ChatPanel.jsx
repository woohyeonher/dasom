import { useState, useRef, useEffect } from 'react'

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

const SUGGESTION_CHIPS = [
  "I don't know where to start",
  "We had a fight and I need help",
  "I want to share my side of things",
]

/**
 * @param {{ messages, streamingText, onSend, disabled, onStreamTimeout }} props
 * onSend(text: string) — called with trimmed text; parent handles the API call.
 * disabled — true while the parent is awaiting a response.
 * onStreamTimeout(text: string) — called if streaming has been active for 30s
 *   with no completion event; parent should commit the accumulated text.
 */
export default function ChatPanel({ messages, streamingText, onSend, disabled, onStreamTimeout }) {
  const [inputText, setInputText] = useState('')
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingText])

  // Safety: if streaming is active but no completion event arrives within 30s,
  // auto-commit whatever text has accumulated and stop the blinking cursor.
  useEffect(() => {
    if (!streamingText) return
    const timeout = setTimeout(() => {
      onStreamTimeout?.(streamingText)
    }, 30000)
    return () => clearTimeout(timeout)
  }, [streamingText, onStreamTimeout])

  const handleSend = () => {
    if (!inputText.trim() || disabled) return
    const text = inputText.trim()
    setInputText('')
    onSend(text)
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleChipClick = (chip) => {
    setInputText(chip)
    inputRef.current?.focus()
  }

  // Show suggestion chips only before the user has sent any message
  const showChips = !messages.some(m => m.role === 'user')

  return (
    <div style={PANEL_SHELL}>
      <div style={PANEL_HEADER}>Conversation</div>

      {/* Message list */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '1rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.75rem',
      }}>
        {messages.map((msg, i) => (
          <div key={i} style={{
            display: 'flex',
            justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
          }}>
            <div style={{
              maxWidth: '80%',
              padding: '0.65rem 1rem',
              borderRadius: '16px',
              fontFamily: 'Inter, sans-serif',
              fontSize: '14px',
              lineHeight: 1.6,
              background: msg.role === 'user'
                ? 'var(--color-primary)'
                : msg.role === 'error'
                  ? '#FAE8E5'
                  : 'var(--color-surface)',
              color: msg.role === 'user'
                ? '#fff'
                : msg.role === 'error'
                  ? '#C0574A'
                  : 'var(--color-text)',
              border: msg.role === 'agent' ? '1px solid var(--color-border)' : 'none',
              boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
              whiteSpace: 'pre-wrap',
            }}>
              {msg.text}
            </div>
          </div>
        ))}

        {/* Live streaming bubble */}
        {streamingText && (
          <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
            <div style={{
              maxWidth: '80%',
              padding: '0.65rem 1rem',
              borderRadius: '16px',
              fontFamily: 'Inter, sans-serif',
              fontSize: '14px',
              lineHeight: 1.6,
              background: 'var(--color-surface)',
              color: 'var(--color-text)',
              border: '1px solid var(--color-border)',
              boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
              whiteSpace: 'pre-wrap',
            }}>
              {streamingText}
              <span style={{
                display: 'inline-block',
                width: '6px',
                height: '14px',
                background: 'var(--color-primary)',
                borderRadius: '2px',
                marginLeft: '2px',
                verticalAlign: 'text-bottom',
                animation: 'cursor-blink 1s ease-in-out infinite',
              }} />
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div style={{
        borderTop: '1px solid var(--color-border)',
        display: 'flex',
        flexDirection: 'column',
        flexShrink: 0,
      }}>
        {/* Suggestion chips — visible before first user message */}
        {showChips && (
          <div style={{
            padding: '0.6rem 0.75rem 0',
            display: 'flex',
            flexWrap: 'wrap',
            gap: '0.4rem',
          }}>
            {SUGGESTION_CHIPS.map((chip, i) => (
              <button
                key={i}
                onClick={() => handleChipClick(chip)}
                style={{
                  padding: '0.3rem 0.8rem',
                  background: 'var(--color-background)',
                  border: '1px solid var(--color-border)',
                  borderRadius: '99px',
                  fontFamily: 'Inter, sans-serif',
                  fontSize: '12px',
                  color: 'var(--color-text)',
                  opacity: 0.75,
                  cursor: 'pointer',
                  lineHeight: 1.4,
                  transition: 'border-color 0.15s, opacity 0.15s',
                }}
                onMouseEnter={e => {
                  e.currentTarget.style.borderColor = 'var(--color-primary)'
                  e.currentTarget.style.opacity = '1'
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.borderColor = 'var(--color-border)'
                  e.currentTarget.style.opacity = '0.75'
                }}
              >
                {chip}
              </button>
            ))}
          </div>
        )}

        {/* Textarea + send button */}
        <div style={{
          padding: '0.6rem 0.75rem 0.75rem',
          display: 'flex',
          gap: '0.5rem',
        }}>
          <textarea
            ref={inputRef}
            value={inputText}
            onChange={e => setInputText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Share what happened…"
            rows={2}
            style={{
              flex: 1,
              padding: '0.6rem 0.85rem',
              border: '1px solid var(--color-border)',
              borderRadius: '8px',
              fontFamily: 'Inter, sans-serif',
              fontSize: '14px',
              color: 'var(--color-text)',
              background: 'var(--color-background)',
              resize: 'none',
              outline: 'none',
              lineHeight: 1.5,
            }}
          />
          <button
            onClick={handleSend}
            disabled={disabled || !inputText.trim()}
            style={{
              padding: '0 1rem',
              background: 'var(--color-primary)',
              color: '#fff',
              border: 'none',
              borderRadius: '8px',
              fontFamily: 'Inter, sans-serif',
              fontWeight: 500,
              fontSize: '13px',
              cursor: disabled || !inputText.trim() ? 'not-allowed' : 'pointer',
              opacity: disabled || !inputText.trim() ? 0.6 : 1,
              flexShrink: 0,
              alignSelf: 'flex-end',
              height: '38px',
            }}
          >
            {disabled ? '…' : 'Send'}
          </button>
        </div>
      </div>
    </div>
  )
}
