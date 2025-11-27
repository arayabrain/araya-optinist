import { FC } from "react"
import { useSelector } from "react-redux"
import { useNavigate } from "react-router-dom"

import AnalyticsIcon from "@mui/icons-material/Analytics"
import DashboardIcon from "@mui/icons-material/Dashboard"
import ManageAccountsIcon from "@mui/icons-material/ManageAccounts"
import UpgradeIcon from "@mui/icons-material/Upgrade"
import ViewListIcon from "@mui/icons-material/ViewList"
import WebIcon from "@mui/icons-material/Web"
import { Box } from "@mui/material"
import Drawer from "@mui/material/Drawer"
import List from "@mui/material/List"
import ListItem from "@mui/material/ListItem"
import ListItemButton from "@mui/material/ListItemButton"
import ListItemIcon from "@mui/material/ListItemIcon"
import ListItemText from "@mui/material/ListItemText"

import { DRAWER_WIDTH } from "const/Layout"
import { isAdmin } from "store/slice/User/UserSelector"

const LeftMenu: FC<{ open: boolean; handleDrawerClose: () => void }> = ({
  open,
  handleDrawerClose,
}) => {
  const navigate = useNavigate()
  const admin = useSelector(isAdmin)

  const onClickDashboard = () => {
    handleDrawerClose()
    navigate("/dashboard")
  }

  const onClickDataview = () => {
    handleDrawerClose()
    navigate("/dataview")
  }

  const onClickWorkspaces = () => {
    handleDrawerClose()
    navigate("/workspaces")
  }

  const onClickOpenSite = () => {
    handleDrawerClose()
    navigate("/public")
  }

  const onClickAccountManager = () => {
    handleDrawerClose()
    navigate("/account-manager")
  }

  const onClickUpgrade = () => {
    handleDrawerClose()
    navigate("/subscription")
  }

  return (
    <Drawer anchor="left" open={open} onClose={handleDrawerClose}>
      <Box
        sx={{
          width: DRAWER_WIDTH,
          display: "flex",
          flexDirection: "column",
          height: "100%",
        }}
      >
        <Box sx={{ flex: 1 }}>
          <List>
            <ListItem key="dashboard" disablePadding>
              <ListItemButton onClick={onClickDashboard}>
                <ListItemIcon>
                  <DashboardIcon />
                </ListItemIcon>
                <ListItemText primary="Dashboard" />
              </ListItemButton>
            </ListItem>
            <ListItem key="dataview" disablePadding>
              <ListItemButton onClick={onClickDataview}>
                <ListItemIcon>
                  <ViewListIcon />
                </ListItemIcon>
                <ListItemText primary="Dataview" />
              </ListItemButton>
            </ListItem>
            <ListItem key="workspaces" disablePadding>
              <ListItemButton onClick={onClickWorkspaces}>
                <ListItemIcon>
                  <AnalyticsIcon />
                </ListItemIcon>
                <ListItemText primary="Workspaces" />
              </ListItemButton>
            </ListItem>
            {admin && (
              <ListItem key="account-manager" disablePadding>
                <ListItemButton onClick={onClickAccountManager}>
                  <ListItemIcon>
                    <ManageAccountsIcon />
                  </ListItemIcon>
                  <ListItemText primary="Account Manager" />
                </ListItemButton>
              </ListItem>
            )}
            <ListItem key="site" disablePadding>
              <ListItemButton onClick={onClickOpenSite}>
                <ListItemIcon>
                  <WebIcon />
                </ListItemIcon>
                <ListItemText primary="Public Repository" />
              </ListItemButton>
            </ListItem>
          </List>
        </Box>
        <Box>
          <List>
            <ListItem key="subscription" disablePadding>
              <ListItemButton onClick={onClickUpgrade}>
                <ListItemIcon>
                  <UpgradeIcon />
                </ListItemIcon>
                <Box sx={{ width: "100%" }}>
                  <ListItemText primary="Upgrade Plan" />
                  <ListItemText secondary="More Access to Optinist" />
                </Box>
              </ListItemButton>
            </ListItem>
          </List>
        </Box>
      </Box>
    </Drawer>
  )
}

export default LeftMenu
