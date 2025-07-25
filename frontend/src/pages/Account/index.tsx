import {
  ChangeEvent,
  FocusEvent,
  KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react"
import { useSelector, useDispatch } from "react-redux"
import { useNavigate } from "react-router-dom"

import { useSnackbar, VariantType } from "notistack"

import Edit from "@mui/icons-material/Edit"
import {
  Box,
  Button,
  IconButton,
  Input,
  styled,
  Typography,
} from "@mui/material"
import { isRejectedWithValue } from "@reduxjs/toolkit"

import { ROLE } from "@types"
import ChangePasswordModal from "components/Account/ChangePasswordModal"
import DeleteConfirmModal from "components/common/DeleteConfirmModal"
import Loading from "components/common/Loading"
import { getUserSubscription } from "store/slice/Subscriptions/SubscriptionActions"
import {
  deleteMe,
  getMe,
  updateMe,
  updateMePassword,
} from "store/slice/User/UserActions"
import { selectCurrentUser, selectLoading } from "store/slice/User/UserSelector"
import { AppDispatch } from "store/store"
import { convertBytes } from "utils"

interface UserSubscription {
  id: number
  plan_id: number
  user_id: number
  expiration: string
  plan_name: string
  plan_price: number
  created_at: string
  updated_at: string
}

const Account = () => {
  const user = useSelector(selectCurrentUser)
  const loading = useSelector(selectLoading)
  const dispatch = useDispatch<AppDispatch>()
  const navigate = useNavigate()
  const [isDeleteConfirmModalOpen, setIsDeleteConfirmModalOpen] =
    useState(false)
  const [isChangePwModalOpen, setIsChangePwModalOpen] = useState(false)
  const [isEditName, setIsEditName] = useState(false)
  const [isName, setIsName] = useState<string>()

  // Add subscription state
  const [userSubscription, setUserSubscription] =
    useState<UserSubscription | null>(null)
  const [subscriptionLoading, setSubscriptionLoading] = useState(false)
  const [subscriptionError, setSubscriptionError] = useState<string | null>(
    null,
  )

  const ref = useRef<HTMLInputElement>(null)

  const { enqueueSnackbar } = useSnackbar()

  const handleClickVariant = (variant: VariantType, mess: string) => {
    enqueueSnackbar(mess, { variant })
  }

  useEffect(() => {
    dispatch(getMe())
  }, [dispatch])

  useEffect(() => {
    if (!user) return
    setIsName(user.name)

    // Fetch user subscription when user is loaded
    if (user.id) {
      loadUserSubscription()
    }
    //eslint-disable-next-line
  }, [user])

  const loadUserSubscription = async () => {
    if (!user?.id) return

    setSubscriptionLoading(true)
    setSubscriptionError(null)

    try {
      const response = await dispatch(getUserSubscription(user.id))

      if (isRejectedWithValue(response)) {
        // Handle error case
        console.error("Failed to fetch subscription:", response.payload)
        setSubscriptionError("Failed to load subscription data")
        setUserSubscription(null)
      } else {
        // Handle success case
        setUserSubscription(response.payload || null)
      }
    } catch (error) {
      console.error("Error fetching subscription:", error)
      setSubscriptionError("Failed to load subscription data")
      setUserSubscription(null)
    } finally {
      setSubscriptionLoading(false)
    }
  }

  const handleCloseDeleteComfirmModal = () => {
    setIsDeleteConfirmModalOpen(false)
  }

  const onDeleteAccountClick = () => {
    setIsDeleteConfirmModalOpen(true)
  }

  const onConfirmDelete = async () => {
    if (!user) return
    const data = await dispatch(deleteMe())
    if (isRejectedWithValue(data)) {
      handleClickVariant("error", "Account deleted failed!")
    } else {
      navigate("/login")
    }
    handleCloseDeleteComfirmModal()
  }

  const handleCloseChangePw = () => {
    setIsChangePwModalOpen(false)
  }

  const onChangePwClick = () => {
    setIsChangePwModalOpen(true)
  }

  const onConfirmChangePw = async (oldPass: string, newPass: string) => {
    const data = await dispatch(
      updateMePassword({ old_password: oldPass, new_password: newPass }),
    )
    if (isRejectedWithValue(data)) {
      handleClickVariant("error", "Failed to Change Password!")
    } else {
      handleClickVariant(
        "success",
        "Your password has been successfully changed!",
      )
    }
    handleCloseChangePw()
  }

  const onEditName = (e: ChangeEvent<HTMLInputElement>) => {
    setIsName(e.target.value)
  }

  const onBlur = async (event: FocusEvent) => {
    if (!user || !user.name || !user.email) return
    if (isName === user.name) {
      setIsEditName(false)
      return
    }
    const target = event.target as HTMLInputElement | HTMLTextAreaElement
    if (!target.value) {
      handleClickVariant("error", "Full name can't be empty!")
      setIsName(user?.name)
    } else {
      const data = await dispatch(
        updateMe({
          name: target.value,
          email: user.email,
        }),
      )
      if (isRejectedWithValue(data)) {
        handleClickVariant("error", "Full name edited failed!")
      } else {
        handleClickVariant("success", "Full name edited successfully!")
      }
    }
    setIsEditName(false)
  }

  const onClickUpgrade = () => {
    navigate("/console/subscription")
  }

  const getRole = (role?: number) => {
    if (!role) return
    let newRole = ""
    switch (role) {
      case ROLE.ADMIN:
        newRole = "Admin"
        break
      case ROLE.OPERATOR:
        newRole = "Operator"
        break
    }
    return newRole
  }

  const handleName = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      setIsName(user?.name)
      setIsEditName(false)
      return
    }
    if (event.key === "Enter") {
      if (ref.current) ref.current?.querySelector("input")?.blur?.()
      return
    }
  }

  // Helper function to get membership status
  const getMembershipStatus = () => {
    if (subscriptionLoading) {
      return "Loading..."
    }

    if (subscriptionError) {
      return "Error loading subscription"
    }

    if (!userSubscription) {
      return "FREE"
    }

    // Check if subscription is expired
    const now = new Date()
    const expirationDate = new Date(userSubscription.expiration)

    if (expirationDate <= now) {
      return "EXPIRED"
    }

    return userSubscription.plan_name.toUpperCase()
  }

  // Helper function to get membership button text and action
  const getMembershipButton = () => {
    const status = getMembershipStatus()

    if (status === "FREE" || status === "EXPIRED") {
      return {
        text: "Upgrade",
        action: onClickUpgrade,
        color: "primary" as const,
      }
    }

    return {
      text: "Manage",
      action: onClickUpgrade,
      color: "secondary" as const,
    }
  }

  // Helper function to format expiration date
  const getExpirationInfo = () => {
    if (!userSubscription) return null

    const expirationDate = new Date(userSubscription.expiration)
    const now = new Date()

    if (expirationDate <= now) {
      return (
        <Typography variant="caption" color="error" sx={{ ml: 1 }}>
          (Expired on {expirationDate.toLocaleDateString()})
        </Typography>
      )
    }

    return (
      <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
        (Expires on {expirationDate.toLocaleDateString()})
      </Typography>
    )
  }

  const membershipButton = getMembershipButton()

  return (
    <AccountWrapper>
      <DeleteConfirmModal
        titleSubmit="Delete My Account"
        onClose={handleCloseDeleteComfirmModal}
        open={isDeleteConfirmModalOpen}
        onSubmit={onConfirmDelete}
        description="Delete account will erase all of your data. "
        iconType="warning"
      />
      <ChangePasswordModal
        onSubmit={onConfirmChangePw}
        open={isChangePwModalOpen}
        onClose={handleCloseChangePw}
      />
      <Title>Account Profile</Title>
      <BoxFlex>
        <TitleData>Organization</TitleData>
        <BoxData>{user?.organization?.name}</BoxData>
      </BoxFlex>
      <BoxFlex>
        <TitleData>Name</TitleData>
        {isEditName ? (
          <Input
            sx={{ width: 400 }}
            autoFocus
            onBlur={onBlur}
            placeholder="Name"
            value={isName}
            onChange={onEditName}
            onKeyDown={handleName}
            ref={ref}
          />
        ) : (
          <>
            <Box>{isName ? isName : user?.name}</Box>
            <IconButton sx={{ ml: 1 }} onClick={() => setIsEditName(true)}>
              <Edit />
            </IconButton>
          </>
        )}
      </BoxFlex>
      <BoxFlex>
        <TitleData>Email</TitleData>
        <BoxData>{user?.email}</BoxData>
      </BoxFlex>
      <BoxFlex>
        <TitleData>Role</TitleData>
        <BoxData>{getRole(user?.role_id)}</BoxData>
      </BoxFlex>
      <BoxFlex>
        <TitleData>Data size</TitleData>
        <BoxData>{convertBytes(user?.data_usage || 0)}</BoxData>
      </BoxFlex>
      <BoxFlex>
        <TitleData>Bucket name</TitleData>
        <BoxData>{user?.attributes?.remote_bucket_name || "-"}</BoxData>
      </BoxFlex>
      <BoxFlex>
        <TitleData>Membership</TitleData>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
          }}
        >
          <BoxData>{getMembershipStatus()}</BoxData>
          {getExpirationInfo()}
        </Box>
        <Button
          variant="contained"
          color={membershipButton.color}
          sx={{ ml: 2 }}
          onClick={membershipButton.action}
        >
          {membershipButton.text}
        </Button>
      </BoxFlex>
      {/* TODO: Fix to be dynamic code */}
      <BoxFlex>
        <TitleData>Payment Method</TitleData>
        <>
          <Box>Credit Card</Box>
          <IconButton sx={{ ml: 1 }}>
            <Edit />
          </IconButton>
        </>
      </BoxFlex>
      <BoxFlex sx={{ justifyContent: "space-between", mt: 10, maxWidth: 600 }}>
        <Button variant="contained" color="primary" onClick={onChangePwClick}>
          Change Password
        </Button>
        <Button
          variant="contained"
          color="error"
          onClick={onDeleteAccountClick}
        >
          Delete Account
        </Button>
      </BoxFlex>
      <Loading loading={loading} />
    </AccountWrapper>
  )
}

const AccountWrapper = styled(Box)({
  padding: "0 20px",
})

const BoxFlex = styled(Box)({
  display: "flex",
  margin: "20px 0 10px 0",
  alignItems: "center",
  maxWidth: 1000,
})

const Title = styled("h2")({
  marginBottom: 40,
})

const BoxData = styled(Typography)({
  fontWeight: 700,
  minWidth: 272,
})

const TitleData = styled(Typography)({
  width: 250,
  minWidth: 250,
})

export default Account
