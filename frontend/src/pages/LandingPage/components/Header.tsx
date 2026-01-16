import React, { useState } from "react"

export const Header: React.FC = () => {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)

  return (
    <header className="landing-header">
      <div className="landing-header-container">
        <div className="landing-logo">
          <div className="landing-logo-icon">
            <span className="material-symbols-outlined">biotech</span>
          </div>
          <h2 className="landing-logo-text">OptiNiSt</h2>
        </div>
        <nav className="landing-nav">
          <a className="landing-nav-link" href="#features">
            Features
          </a>
          <a className="landing-nav-link" href="#formats">
            Data Formats
          </a>
          <a className="landing-nav-link" href="#audience">
            Who It&apos;s For
          </a>
          <a className="landing-nav-link" href="#">
            Documentation
          </a>
          <button className="landing-btn-primary">Get Started</button>
        </nav>
        <button
          className="landing-mobile-menu-btn"
          onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
        >
          <span className="material-symbols-outlined">
            {mobileMenuOpen ? "close" : "menu"}
          </span>
        </button>
      </div>
      {mobileMenuOpen && (
        <div className="landing-mobile-nav">
          <a className="landing-mobile-nav-link" href="#features">
            Features
          </a>
          <a className="landing-mobile-nav-link" href="#formats">
            Data Formats
          </a>
          <a className="landing-mobile-nav-link" href="#audience">
            Who It&apos;s For
          </a>
          <a className="landing-mobile-nav-link" href="#">
            Documentation
          </a>
          <button className="landing-btn-primary landing-btn-full">
            Get Started
          </button>
        </div>
      )}
    </header>
  )
}
