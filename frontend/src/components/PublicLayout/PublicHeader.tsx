import { FC } from "react"
import { useSelector } from "react-redux"
import { useNavigate } from "react-router-dom"

import {
  AppBar,
  Button,
  Container,
  styled,
  Toolbar,
  Typography,
} from "@mui/material"
import { SxProps, Theme } from "@mui/material/styles"

import { selectCurrentUser } from "store/slice/User/UserSelector"

const PublicHeader: FC = () => {
  const currentUser = useSelector(selectCurrentUser)
  const isLoggedIn = !!currentUser

  return (
    <PublicAppBar>
      <Container maxWidth={false}>
        <Toolbar>
          <PublicNavMenu
            displayName="OptiNiSt Public Repository"
            navLink="/"
            sx={{ fontWeight: 600, fontSize: 22, mr: 2 }}
          />
          <PublicNavMenu
            displayName={isLoggedIn ? "DASHBOARD" : "LOGIN"}
            navLink={isLoggedIn ? "/dashboard" : "/login"}
          />
        </Toolbar>
      </Container>
    </PublicAppBar>
  )
}

const PublicAppBar = styled(AppBar)({
  position: "fixed",
})

const PublicNavMenu: FC<{
  displayName: string
  navLink: string
  sx?: SxProps<Theme>
}> = ({ displayName, navLink, sx }) => {
  const navigate = useNavigate()
  const handleMenuClick = () => {
    navigate(navLink)
  }

  return (
    <Button
      key={displayName}
      onClick={handleMenuClick}
      sx={{ textTransform: "none" }}
    >
      <Typography color="white" sx={sx}>
        {displayName}
      </Typography>
    </Button>
  )
}

export default PublicHeader
