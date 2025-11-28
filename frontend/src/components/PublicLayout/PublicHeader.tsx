import { FC } from "react"
import { useSelector } from "react-redux"
import { Link } from "react-router-dom"

import { Box, styled, Typography } from "@mui/material"

import { selectCurrentUser } from "store/slice/User/UserSelector"

const PublicHeader: FC = () => {
  const user = useSelector(selectCurrentUser)

  return (
    <HeaderContainer>
      <HeaderLogoLink to="/dataview">
        <HeaderContent>
          <HeaderLogo src="/static/optinist_logo.png" alt="OptiNiSt" />
          <HeaderTitle>OptiNiSt</HeaderTitle>
        </HeaderContent>
      </HeaderLogoLink>
      <NavSection>
        {user ? (
          <NavLink to="/console">Console</NavLink>
        ) : (
          <LoginButton to="/login">Login</LoginButton>
        )}
      </NavSection>
    </HeaderContainer>
  )
}

const HeaderContainer = styled(Box)({
  width: "98%",
  height: 64,
  backgroundColor: "#ffffff",
  borderBottom: "1px solid #e5e7eb",
  display: "flex",
  alignItems: "center",
  padding: "0 24px",
  position: "fixed",
  top: 0,
  left: 0,
  zIndex: 1000,
})

const HeaderLogoLink = styled(Link)({
  textDecoration: "none",
  display: "flex",
  alignItems: "center",
  transition: "opacity 0.2s",
  ":hover": {
    opacity: 0.8,
  },
})

const HeaderContent = styled(Box)({
  display: "flex",
  alignItems: "center",
  gap: 12,
})

const HeaderLogo = styled("img")({
  height: 40,
  width: "auto",
})

const HeaderTitle = styled(Typography)({
  fontSize: 20,
  fontWeight: 600,
  color: "#000000",
})

const NavSection = styled(Box)({
  marginLeft: "auto",
  display: "flex",
  alignItems: "center",
  gap: 16,
})

const NavLink = styled(Link)({
  fontSize: 14,
  fontWeight: 500,
  color: "#374151",
  textDecoration: "none",
  transition: "color 0.2s",
  ":hover": {
    color: "#000000",
  },
})

const LoginButton = styled(Link)({
  display: "inline-block",
  padding: "8px 16px",
  fontSize: 14,
  fontWeight: 500,
  color: "#ffffff",
  backgroundColor: "#000000",
  borderRadius: 6,
  textDecoration: "none",
  transition: "background-color 0.2s",
  ":hover": {
    backgroundColor: "#1f2937",
  },
})

export default PublicHeader
