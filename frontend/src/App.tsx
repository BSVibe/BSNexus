import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Layout from './components/layout/Layout'
import { ToastContainer } from './components/common'
import AuthProvider from './components/auth/AuthProvider'
import ProtectedRoute from './components/auth/ProtectedRoute'
import LandingPage from './pages/LandingPage'
import DashboardPage from './pages/DashboardPage'
import ArchitectPage from './pages/ArchitectPage'
import ProjectPage from './pages/ProjectPage'
import MigratePage from './pages/MigratePage'
import AgentsPage from './pages/AgentsPage'
import BudgetPage from './pages/BudgetPage'
import SettingsPage from './pages/SettingsPage'

const queryClient = new QueryClient()

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<LandingPage />} />
            <Route element={<ProtectedRoute />}>
              <Route element={<Layout />}>
                <Route path="/dashboard" element={<DashboardPage />} />
                <Route path="/architect/:sessionId?" element={<ArchitectPage />} />
                <Route path="/migrate" element={<MigratePage />} />
                <Route path="/projects/:projectId?" element={<ProjectPage />} />
                <Route path="/agents" element={<AgentsPage />} />
                <Route path="/budget" element={<BudgetPage />} />
                <Route path="/settings" element={<SettingsPage />} />
              </Route>
            </Route>
          </Routes>
        </BrowserRouter>
        <ToastContainer />
      </AuthProvider>
    </QueryClientProvider>
  )
}

export default App
