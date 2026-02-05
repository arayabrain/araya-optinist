import { FC, useState, MouseEvent } from "react"
import { useDispatch } from "react-redux"
import { useNavigate } from "react-router-dom"

import AccountCircleIcon from "@mui/icons-material/AccountCircle"
import Logout from "@mui/icons-material/Logout"
import PortraitIcon from "@mui/icons-material/Portrait"
import {
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Tooltip,
} from "@mui/material"
import IconButton from "@mui/material/IconButton"

import { usePremiumAssignment } from "contexts/PremiumAssignmentContext"
import { logout } from "store/slice/User/UserSlice"
import { setLoggingOut } from "utils/axios"

const Profile: FC = () => {
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null)
  const navigate = useNavigate()
  const dispatch = useDispatch()
  const { autoReleaseOnLogout, isPremiumUser } = usePremiumAssignment()

  const handleMenu = (event: MouseEvent<HTMLElement>) => {
    setAnchorEl(event.currentTarget)
  }

  const handleCloseMenu = () => {
    setAnchorEl(null)
  }

  const onClickLogout = async () => {
    setAnchorEl(null)

    // Release premium instance before logout if user is premium
    if (isPremiumUser) {
      try {
        await autoReleaseOnLogout()
      } catch (error) {
        // eslint-disable-next-line no-console
        console.warn("Failed to release premium instance on logout:", error)
        // Continue with logout even if release fails
      }
    }

    dispatch(logout())
    navigate("/login")

    // Reset isLoggingOut flag after logout and navigation are initiated
    // The flag protects token removal (in logout reducer) from race conditions
    // with 401 handlers. By this point tokens are already removed. (Case 7 fix)
    setLoggingOut(false)
  }

  const onClickAccount = () => {
    setAnchorEl(null)
    navigate("/account")
  }

  return (
    <>
      <Tooltip title="Profile">
        <IconButton
          aria-label="open profile menu"
          aria-haspopup="true"
          onClick={handleMenu}
        >
          <AccountCircleIcon />
        </IconButton>
      </Tooltip>
      <Menu
        id="profile-menu"
        anchorEl={anchorEl}
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "right",
        }}
        keepMounted
        transformOrigin={{
          vertical: "top",
          horizontal: "right",
        }}
        open={Boolean(anchorEl)}
        onClose={handleCloseMenu}
      >
        <MenuItem onClick={onClickAccount}>
          <ListItemIcon>
            <PortraitIcon />
          </ListItemIcon>
          <ListItemText>Account Profile</ListItemText>
        </MenuItem>
        <MenuItem onClick={onClickLogout}>
          <ListItemIcon>
            <Logout />
          </ListItemIcon>
          <ListItemText>Sign Out</ListItemText>
        </MenuItem>
      </Menu>
    </>
  )
}

export default Profile
