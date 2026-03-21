import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Layout from './components/layout/Layout'
import { ToastContainer } from './components/common'
import DashboardPage from './pages/DashboardPage'
import ArchitectPage from './pages/ArchitectPage'
import ProjectPage from './pages/ProjectPage'

const queryClient = new QueryClient()

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/architect/:sessionId?" element={<ArchitectPage />} />
            <Route path="/projects/:projectId" element={<ProjectPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
      <ToastContainer />
    </QueryClientProvider>
  )
}

export default App
