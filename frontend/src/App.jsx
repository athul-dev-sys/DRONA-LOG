import { BrowserRouter, Route, Routes } from 'react-router-dom'
import CommandView from './views/CommandView.jsx'
import EdgeClient from './views/EdgeClient.jsx'
import './index.css'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<CommandView />} />
        <Route path="/edge" element={<EdgeClient />} />
      </Routes>
    </BrowserRouter>
  )
}
