import { useState } from "react"
import { useNavigate } from "react-router-dom"

import { Box, styled, Typography } from "@mui/material"

import {
  FONT_SIZE,
  FONT_WEIGHT,
  LETTER_SPACING,
  TEXT_COLOR,
  BG_COLOR,
  BORDER_COLOR,
  BUTTON_BASE,
} from "pages/LandingPage/constants"

export const Header = () => {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const navigate = useNavigate()

  return (
    <HeaderWrapper>
      <TrustBar>
        Built on{" "}
        <TrustBarLink
          href="https://github.com/oist/optinist"
          target="_blank"
          rel="noopener noreferrer"
        >
          OptiNiSt
        </TrustBarLink>{" "}
        by ARAYA and OIST (Okinawa Institute of Science and Technology)
      </TrustBar>
      <HeaderContainer>
        <Logo>
          <LogoIcon>
            <img
              src="/static/optinist_logo.png"
              alt="OptiNiSt"
              style={{ height: 32, width: "auto" }}
            />
          </LogoIcon>
          <LogoText>ARAYA OptiNiSt</LogoText>
        </Logo>
        <Nav>
          <NavLink href="#features">Features</NavLink>
          <NavLink href="#audience">Who It&apos;s For</NavLink>
          <NavLink href="#public-repository">Public Repository</NavLink>
          <NavLink
            href="https://araya-optinist.readthedocs.io/en/latest/"
            target="_blank"
            rel="noopener noreferrer"
          >
            Documentation
          </NavLink>
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
          <MobileNavLink href="#audience">Who It&apos;s For</MobileNavLink>
          <MobileNavLink href="#public-repository">
            Public Repository
          </MobileNavLink>
          <MobileNavLink
            href="https://araya-optinist.readthedocs.io/en/latest/"
            target="_blank"
            rel="noopener noreferrer"
          >
            Documentation
          </MobileNavLink>
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
  borderBottom: `1px solid ${BORDER_COLOR.DEFAULT}`,
  backgroundColor: BG_COLOR.HEADER,
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
  color: TEXT_COLOR.WHITE,
})

const LogoText = styled(Typography)({
  fontSize: FONT_SIZE.CARD_TITLE,
  fontWeight: FONT_WEIGHT.BOLD,
  letterSpacing: LETTER_SPACING.TIGHT,
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
  fontSize: FONT_SIZE.BODY,
  fontWeight: FONT_WEIGHT.REGULAR,
  color: TEXT_COLOR.PRIMARY,
  textDecoration: "none",
  transition: "color 0.2s",
  "&:hover": {
    color: TEXT_COLOR.ACCENT,
  },
})

const MobileMenuButton = styled("button")({
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "none",
  border: "none",
  cursor: "pointer",
  color: TEXT_COLOR.SECONDARY,
  "@media (min-width: 768px)": {
    display: "none",
  },
})

const MobileNav = styled(Box)({
  display: "flex",
  flexDirection: "column",
  gap: "1rem",
  padding: "1rem 1.5rem 1.5rem",
  borderTop: `1px solid ${BORDER_COLOR.DEFAULT}`,
  background: BG_COLOR.WHITE,
})

const MobileNavLink = styled("a")({
  fontSize: FONT_SIZE.BODY,
  fontWeight: FONT_WEIGHT.REGULAR,
  color: TEXT_COLOR.PRIMARY,
  textDecoration: "none",
  padding: "0.5rem 0",
})

const PrimaryButton = styled("button")({
  ...BUTTON_BASE,
  height: 40,
  padding: "0 1.5rem",
})

const TrustBar = styled(Box)({
  backgroundColor: "#0f172a",
  color: TEXT_COLOR.WHITE_SOFT,
  fontSize: FONT_SIZE.SMALL,
  fontWeight: FONT_WEIGHT.REGULAR,
  letterSpacing: LETTER_SPACING.WIDE,
  textAlign: "center",
  padding: "0.4rem 1rem",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: "0.25rem",
  flexWrap: "wrap",
})

const TrustBarLink = styled("a")({
  color: TEXT_COLOR.WHITE,
  fontWeight: FONT_WEIGHT.BOLD,
  textDecoration: "underline",
  textUnderlineOffset: 2,
  "&:hover": {
    textDecoration: "none",
  },
})
