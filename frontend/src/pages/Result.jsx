import { useState, useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import SynthesisCard from '../components/SynthesisCard'
import ErrorState from '../components/ErrorState'

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

const SECTION_TITLES = [
  'What Happened',
  'What Each Person Felt',
  'Where You Actually Agree',
  'The Core Tension',
  'A Path Forward',
  'A Message to Each Person',
]

function parseSections(text) {
  if (!text) return []

  const headerRegex = /^#{1,3}\s+(.+)$/gm
  const headerMatches = [...text.matchAll(headerRegex)]
  if (headerMatches.length >= 4) {
    const sections = []
    // If content exists before the first header, it's Section 1 ("What Happened")
    // Llama Scout sometimes omits the ## prefix on the first section
    const preamble = text.slice(0, headerMatches[0].index).trim()
    if (preamble) {
      sections.push({ title: 'What Happened', content: preamble })
    }
    headerMatches.forEach((match, i) => {
      const title = match[1].trim()
      const start = match.index + match[0].length
      const end = headerMatches[i + 1]?.index ?? text.length
      sections.push({ title, content: text.slice(start, end).trim() })
    })
    return sections
  }

  const sections = []
  let cursor = text
  for (let i = 0; i < SECTION_TITLES.length; i++) {
    const title = SECTION_TITLES[i]
    const idx = cursor.search(new RegExp(title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'))
    if (idx === -1) continue
    const afterTitle = cursor.slice(idx + title.length).replace(/^[\s:.\-–—]+/, '')
    const nextTitle = SECTION_TITLES[i + 1]
    const nextIdx = nextTitle
      ? afterTitle.search(new RegExp(nextTitle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'))
      : -1
    const content = nextIdx === -1 ? afterTitle.trim() : afterTitle.slice(0, nextIdx).trim()
    sections.push({ title, content })
    if (nextIdx !== -1) cursor = afterTitle.slice(nextIdx)
  }
  if (sections.length >= 4) return sections
  return [{ title: 'Synthesis', content: text }]
}

function isPersonalSection(title) {
  return /message/i.test(title) || /section 6/i.test(title)
}

// Extract only the subsection for this user from Section 6.
// The synthesis has "**To Person A:**" and "**To Person B:**" markers.
function personalizeSection6(section, userId) {
  if (!isPersonalSection(section.title)) return section

  const personLabel = userId === 'a' ? 'A' : 'B'
  const otherLabel  = userId === 'a' ? 'B' : 'A'
  const content = section.content

  // Match both "**To Person A:**" and plain "To Person A:"
  const myRe    = new RegExp(`(?:\\*\\*)?To Person ${personLabel}:(?:\\*\\*)?`, 'i')
  const otherRe = new RegExp(`(?:\\*\\*)?To Person ${otherLabel}:(?:\\*\\*)?`,  'i')

  const myMatch    = myRe.exec(content)
  const otherMatch = otherRe.exec(content)

  if (!myMatch) return { ...section, title: 'A Message to You' }

  const myStart = myMatch.index + myMatch[0].length
  // myEnd: use otherMatch only if it comes AFTER myMatch (not before)
  const myEnd = (otherMatch && otherMatch.index > myMatch.index)
    ? otherMatch.index
    : content.length

  const personalContent = content
    .slice(myStart, myEnd)
    .replace(/^\s+/, '')
    .trim()

  return { ...section, title: 'A Message to You', content: personalContent }
}

const DIVIDER = (
  <div style={{ borderTop: '1px solid var(--color-border)', margin: '2.5rem 0' }} />
)

export default function Result() {
  const location = useLocation()
  const navigate = useNavigate()
  const { sessionCode, userId } = location.state ?? {}

  const [sections, setSections] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!sessionCode || !userId) {
      setError('Session information is missing.')
      setLoading(false)
      return
    }

    let cancelled = false
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/result/${sessionCode}/${userId}`)
        if (!res.ok) {
          if (res.status === 404) throw new Error("The result isn't ready yet — the mediation may still be running.")
          throw new Error(`Something went wrong (${res.status}).`)
        }
        const data = await res.json()
        if (!cancelled) {
          const parsed = parseSections(data.synthesis)
          setSections(parsed.map(s => personalizeSection6(s, userId)))
        }
      } catch (err) {
        if (!cancelled) setError(err.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [sessionCode, userId])

  if (loading) {
    return (
      <div style={{
        minHeight: '100vh',
        background: 'var(--color-background)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}>
        <p style={{
          fontFamily: 'Inter, sans-serif',
          fontSize: '15px',
          color: 'var(--color-text)',
          opacity: 0.5,
          fontStyle: 'italic',
        }}>
          Loading your mediation result…
        </p>
      </div>
    )
  }

  if (error) {
    return <ErrorState message={error} />
  }

  return (
    <div style={{
      minHeight: '100vh',
      background: 'var(--color-background)',
      padding: '3rem 1.5rem 4rem',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
    }}>
      <div style={{ width: '100%', maxWidth: '680px' }}>
        <div style={{ marginBottom: '2.5rem', textAlign: 'center' }}>
          <h1 style={{
            fontFamily: 'Inter, sans-serif',
            fontWeight: 600,
            fontSize: '22px',
            color: 'var(--color-text)',
            marginBottom: '0.4rem',
            letterSpacing: '-0.01em',
          }}>
            다솜 <span style={{ color: 'var(--color-primary)' }}>Dasom</span>
          </h1>
          <p style={{
            fontFamily: 'Inter, sans-serif',
            fontSize: '13px',
            color: 'var(--color-text)',
            opacity: 0.45,
            margin: 0,
          }}>
            Mediation result
          </p>
        </div>

        {sections.map((section, i) => (
          <div key={i}>
            {i > 0 && DIVIDER}
            <SynthesisCard
              title={section.title}
              content={section.content}
              isPersonal={isPersonalSection(section.title)}
            />
          </div>
        ))}

        <div style={{ marginTop: '3.5rem', textAlign: 'center' }}>
          <button
            onClick={() => navigate('/')}
            style={{
              padding: '0.6rem 1.25rem',
              background: 'transparent',
              color: 'var(--color-text)',
              border: '1px solid var(--color-border)',
              borderRadius: '8px',
              fontFamily: 'Inter, sans-serif',
              fontSize: '13px',
              cursor: 'pointer',
              opacity: 0.6,
            }}
          >
            Start a new session
          </button>
        </div>
      </div>
    </div>
  )
}
