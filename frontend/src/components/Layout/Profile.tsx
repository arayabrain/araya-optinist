import { FC, useState, MouseEvent, useEffect, useCallback } from "react"
import { useDispatch, useSelector } from "react-redux"
import { useNavigate } from "react-router-dom"

import AccountCircleIcon from "@mui/icons-material/AccountCircle"
import Logout from "@mui/icons-material/Logout"
import PortraitIcon from "@mui/icons-material/Portrait"
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Tooltip,
} from "@mui/material"
import IconButton from "@mui/material/IconButton"

import Loading from "components/common/Loading"
import { usePremiumAssignment } from "contexts/PremiumAssignmentContext"
import { selectPipelineIsStartedSuccess } from "store/slice/Pipeline/PipelineSelectors"
import { logout } from "store/slice/User/UserSlice"
import { setLoggingOut } from "utils/axios"
import { tabSync } from "utils/crossTabSync"

const Profile: FC = () => {
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null)
  const [showJobWarning, setShowJobWarning] = useState(false)
  const [isSigningOut, setIsSigningOut] = useState(false)
  const navigate = useNavigate()
  const dispatch = useDispatch()
  const { autoReleaseOnLogout, isPremiumUser } = usePremiumAssignment()
  const hasRunningJob = useSelector(selectPipelineIsStartedSuccess)

  const handleMenu = (event: MouseEvent<HTMLElement>) => {
    setAnchorEl(event.currentTarget)
  }

  const handleCloseMenu = () => {
    setAnchorEl(null)
  }

  const performLogout = useCallback(
    async (broadcast: boolean = true) => {
      // Only release premium from the originating tab;
      // cross-tab receivers (broadcast=false) skip it.
      if (isPremiumUser && broadcast) {
        try {
          await autoReleaseOnLogout()
        } catch (error) {
          // eslint-disable-next-line no-console
          console.warn("Failed to release premium instance on logout:", error)
        }
      }

      if (broadcast) {
        tabSync.broadcastLogout()
      }

      dispatch(logout())
      navigate("/login")
      setLoggingOut(false)
    },
    [isPremiumUser, autoReleaseOnLogout, dispatch, navigate],
  )

  useEffect(() => {
    const unsubscribe = tabSync.on("LOGOUT", () => {
      performLogout(false)
    })
    return unsubscribe
  }, [performLogout])

  const onClickLogout = async () => {
    setAnchorEl(null)

    if (hasRunningJob) {
      setShowJobWarning(true)
      return
    }

    setIsSigningOut(true)
    await performLogout()
  }

  const handleCloseJobWarning = () => {
    setShowJobWarning(false)
  }

  const handleProceedLogout = async () => {
    setShowJobWarning(false)
    setIsSigningOut(true)
    await performLogout()
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
      <Dialog open={showJobWarning} onClose={handleCloseJobWarning}>
        <DialogTitle>Jobs Running</DialogTitle>
        <DialogContent>
          <Box>
            You have jobs currently running. They will continue processing in
            the background but you will not see the results until you log back
            in.
          </Box>
          <Box sx={{ mt: 2 }}>Do you want to sign out anyway?</Box>
        </DialogContent>
        <DialogActions>
          <Button variant="outlined" onClick={handleCloseJobWarning}>
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleProceedLogout}
            disabled={isSigningOut}
          >
            Sign Out Anyway
          </Button>
        </DialogActions>
      </Dialog>
      <Loading loading={isSigningOut} />
    </>
  )
}

export default Profile
