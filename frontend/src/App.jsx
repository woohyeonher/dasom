import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Landing from './pages/Landing'
import Intake from './pages/Intake'
import Waiting from './pages/Waiting'
import Result from './pages/Result'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/"        element={<Landing />} />
        <Route path="/intake"  element={<Intake />} />
        <Route path="/waiting" element={<Waiting />} />
        <Route path="/result"  element={<Result />} />
        <Route path="*"        element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
