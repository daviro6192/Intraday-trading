import { Navigate, Route, Routes } from 'react-router-dom'
import './App.css'
import { NavBar } from './components/NavBar'
import { ProtectedRoute } from './components/ProtectedRoute'
import { AuthProvider, useAuth } from './context/AuthContext'
import { DashboardPage } from './pages/Dashboard'
import { LoginPage } from './pages/Login'
import { RegisterPage } from './pages/Register'
import { SettingsPage } from './pages/Settings'

function AppRoutes() {
  const { user } = useAuth()

  return (
    <>
      {user && <NavBar />}
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage />} />
        <Route path="/register" element={user ? <Navigate to="/" replace /> : <RegisterPage />} />
        <Route element={<ProtectedRoute />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </>
  )
}

function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  )
}

export default App
