import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import App from './App'
import './index.css'
import HistoryPage from './pages/HistoryPage'
import HomePage from './pages/HomePage'
import ModelsPage from './pages/ModelsPage'
import TranslatePage from './pages/TranslatePage'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<App />}>
          <Route index element={<HomePage />} />
          <Route path="translate" element={<TranslatePage />} />
          <Route path="translate/:docId" element={<TranslatePage />} />
          <Route path="models" element={<ModelsPage />} />
          <Route path="history" element={<HistoryPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
