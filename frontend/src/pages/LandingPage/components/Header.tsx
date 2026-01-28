import { useState } from "react"
import { useNavigate } from "react-router-dom"

import { Box, styled, Typography } from "@mui/material"

export const Header = () => {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const navigate = useNavigate()

  return (
    <HeaderWrapper>
      <HeaderContainer>
        <Logo>
          <LogoIcon>
            <img
              src="/static/optinist_logo.png"
              alt="OptiNiSt"
              style={{ height: 32, width: "auto" }}
            />
          </LogoIcon>
          <LogoText>OptiNiSt</LogoText>
        </Logo>
        <Nav>
          <NavLink href="#features">Features</NavLink>
          <NavLink href="#formats">Data Formats</NavLink>
          <NavLink href="#audience">Who It&apos;s For</NavLink>
          <NavLink href="#">Documentation</NavLink>
          <PrimaryButton onClick={() => navigate("/login")}>
            Get Started
          </PrimaryButton>
        </Nav>
        <MobileMenuButton onClick={() => setMobileMenuOpen(!mobileMenuOpen)}>
          <span className="material-symbols-outlined">
            {mobileMenuOpen ? "close" : "menu"}
          </span>
        </MobileMenuButton>
      </HeaderContainer>
      {mobileMenuOpen && (
        <MobileNav>
          <MobileNavLink href="#features">Features</MobileNavLink>
          <MobileNavLink href="#formats">Data Formats</MobileNavLink>
          <MobileNavLink href="#audience">Who It&apos;s For</MobileNavLink>
          <MobileNavLink href="#">Documentation</MobileNavLink>
          <PrimaryButton
            onClick={() => navigate("/login")}
            style={{ width: "100%" }}
          >
            Get Started
          </PrimaryButton>
        </MobileNav>
      )}
    </HeaderWrapper>
  )
}

const HeaderWrapper = styled("header")({
  position: "sticky",
  top: 0,
  zIndex: 50,
  width: "100%",
  borderBottom: "1px solid #e5e7eb",
  backgroundColor: "#E1DEDB",
  backdropFilter: "blur(12px)",
})

const HeaderContainer = styled(Box)({
  maxWidth: 1200,
  margin: "0 auto",
  padding: "0 1.5rem",
  height: 64,
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
})

const Logo = styled(Box)({
  display: "flex",
  alignItems: "center",
  gap: "0.5rem",
})

const LogoIcon = styled(Box)({
  width: 32,
  height: 32,
  borderRadius: 6,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  color: "white",
})

const LogoText = styled(Typography)({
  fontSize: "1.25rem",
  fontWeight: 700,
  letterSpacing: "-0.025em",
})

const Nav = styled("nav")({
  display: "none",
  alignItems: "center",
  gap: "2rem",
  "@media (min-width: 768px)": {
    display: "flex",
  },
})

const NavLink = styled("a")({
  fontSize: "0.875rem",
  fontWeight: 500,
  color: "#111827",
  textDecoration: "none",
  transition: "color 0.2s",
  "&:hover": {
    color: "#2563eb",
  },
})

const MobileMenuButton = styled("button")({
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "none",
  border: "none",
  cursor: "pointer",
  color: "#6b7280",
  "@media (min-width: 768px)": {
    display: "none",
  },
})

const MobileNav = styled(Box)({
  display: "flex",
  flexDirection: "column",
  gap: "1rem",
  padding: "1rem 1.5rem 1.5rem",
  borderTop: "1px solid #e5e7eb",
  background: "white",
})

const MobileNavLink = styled("a")({
  fontSize: "1rem",
  fontWeight: 500,
  color: "#111827",
  textDecoration: "none",
  padding: "0.5rem 0",
})

const PrimaryButton = styled("button")({
  backgroundColor: "#2563eb",
  color: "white",
  fontSize: "0.875rem",
  fontWeight: 700,
  height: 40,
  padding: "0 1.5rem",
  borderRadius: 8,
  border: "none",
  cursor: "pointer",
  transition: "background-color 0.2s",
  "&:hover": {
    backgroundColor: "#1d4ed8",
  },
})
