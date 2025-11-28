import { FC } from "react"
import { useSelector } from "react-redux"
import { Link, useLocation } from "react-router-dom"

import { Box, styled, Typography } from "@mui/material"

import { selectCurrentUser } from "store/slice/User/UserSelector"

const PublicHeader: FC = () => {
  const user = useSelector(selectCurrentUser)
  const location = useLocation()
  const isLoginPage = location.pathname === "/login"
  const isRegisterPage = location.pathname === "/register"
  const isPublicPage = location.pathname === "/public"

  return (
    <HeaderContainer>
      {isPublicPage ? (
        <HeaderContent>
          <HeaderLogo src="/static/optinist_logo.png" alt="OptiNiSt" />
          <HeaderTitle>OptiNiSt</HeaderTitle>
        </HeaderContent>
      ) : (
        <HeaderLogoLink to="/public">
          <HeaderContent>
            <HeaderLogo src="/static/optinist_logo.png" alt="OptiNiSt" />
            <HeaderTitle>OptiNiSt</HeaderTitle>
          </HeaderContent>
        </HeaderLogoLink>
      )}
      <NavSection>
        {user ? (
          <DashboardButton to="/dashboard">Dashboard</DashboardButton>
        ) : (
          !isLoginPage &&
          !isRegisterPage && <LoginButton to="/login">Login</LoginButton>
        )}
      </NavSection>
    </HeaderContainer>
  )
}

const HeaderContainer = styled(Box)({
  width: "98%",
  height: 64,
  backgroundColor: "#E1DEDB",
  borderBottom: "1px solid #e5e7eb",
  boxShadow:
    "0px 2px 4px -1px rgba(0,0,0,0.2), 0px 4px 5px 0px rgba(0,0,0,0.14), 0px 1px 10px 0px rgba(0,0,0,0.12)",
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

const DashboardButton = styled(Link)({
  display: "inline-block",
  padding: "8px 16px",
  fontSize: 14,
  fontWeight: 500,
  color: "#ffffff",
  backgroundColor: "#000000c4",
  borderRadius: 6,
  textDecoration: "none",
  transition: "background-color 0.2s",
  ":hover": {
    backgroundColor: "#00000090",
  },
})

const LoginButton = styled(Link)({
  display: "inline-block",
  padding: "8px 16px",
  fontSize: 14,
  fontWeight: 500,
  color: "#ffffff",
  backgroundColor: "#000000c4",
  borderRadius: 6,
  textDecoration: "none",
  transition: "background-color 0.2s",
  ":hover": {
    backgroundColor: "#00000090",
  },
})

export default PublicHeader
