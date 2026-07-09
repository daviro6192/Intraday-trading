import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export function NavBar() {
  const { user, logout } = useAuth()

  return (
    <nav className="navbar">
      <Link to="/" className="navbar-brand">
        Intraday Trading
      </Link>
      <div className="navbar-links">
        {user && (
          <>
            <Link to="/settings" className="link-button">
              Impostazioni
            </Link>
            <button className="link-button" onClick={() => void logout()}>
              Esci ({user.username})
            </button>
          </>
        )}
      </div>
    </nav>
  )
}
