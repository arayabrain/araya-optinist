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
  selectUserSubscription,
  selectUserSubscriptionLoading,
  selectSubscriptionError,
} from "store/slice/Subscriptions/SubscriptionSelector"
import { UserSubscription } from "store/slice/Subscriptions/SubscriptionType"
import {
  deleteMe,
  getMe,
  updateMe,
  updateMePassword,
} from "store/slice/User/UserActions"
import { selectCurrentUser, selectLoading } from "store/slice/User/UserSelector"
import { AppDispatch } from "store/store"
import { convertBytes } from "utils"
import { getAccurateTimeUTC } from "utils/subscriptions/SubscriptionUtils"

const useSubscriptionExpiration = (
  userSubscription: UserSubscription | null,
) => {
  const [isExpired, setIsExpired] = useState(false)
  const [isValidating, setIsValidating] = useState(false)

  useEffect(() => {
    if (!userSubscription) {
      setIsExpired(false)
      return
    }

    const validateExpiration = async () => {
      setIsValidating(true)
      try {
        // Get current UTC time from server
        const accurateTime = await getAccurateTimeUTC()
        const expirationDate = new Date(userSubscription.expiration)

        // Ensure expiration is treated as UTC
        const expirationUTC = new Date(expirationDate.getTime())
        setIsExpired(expirationUTC <= accurateTime)
      } catch (error) {
        console.warn(
          "Failed to get accurate time, falling back to client UTC:",
          error,
        )
        const clientTimeUTC = new Date() // This is already UTC internally
        const expirationDate = new Date(userSubscription.expiration)
        setIsExpired(expirationDate <= clientTimeUTC)
      } finally {
        setIsValidating(false)
      }
    }

    validateExpiration()
  }, [userSubscription])

  return { isExpired, isValidating }
}

const Account = () => {
  const user = useSelector(selectCurrentUser)
  const loading = useSelector(selectLoading)
  const userSubscription = useSelector(selectUserSubscription)
  const subscriptionLoading = useSelector(selectUserSubscriptionLoading)
  const subscriptionError = useSelector(selectSubscriptionError)

  const {
    isExpired: isSubscriptionExpired,
    isValidating: isValidatingExpiration,
  } = useSubscriptionExpiration(userSubscription)

  const dispatch = useDispatch<AppDispatch>()
  const navigate = useNavigate()

  const [isDeleteConfirmModalOpen, setIsDeleteConfirmModalOpen] =
    useState(false)
  const [isChangePwModalOpen, setIsChangePwModalOpen] = useState(false)
  const [isEditName, setIsEditName] = useState(false)
  const [isName, setIsName] = useState<string>()

  const ref = useRef<HTMLInputElement>(null)

  const { enqueueSnackbar } = useSnackbar()

  const handleClickVariant = (variant: VariantType, mess: string) => {
    enqueueSnackbar(mess, { variant })
  }

  enum SUBSCRIPTION_STATUS {
    LOADING = "LOADING",
    ERROR = "ERROR",
    FREE = "FREE",
    EXPIRED = "EXPIRED",
    VALIDATING = "VALIDATING",
    NONE = "NONE",
  }

  enum SUBSCRIPTION_PLAN {
    FREE = "Free",
    PREMIUM = "Premium",
  }

  useEffect(() => {
    dispatch(getMe())
  }, [dispatch])

  useEffect(() => {
    if (!user) return
    setIsName(user.name)

    // Fetch user subscription when user is loaded
    if (user.id) {
      dispatch(getUserSubscription(user.id))
    }
  }, [user, dispatch])

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

  const getSubscriptionStatus = () => {
    if (subscriptionLoading) {
      return SUBSCRIPTION_STATUS.LOADING
    } else if (isValidatingExpiration) {
      return SUBSCRIPTION_STATUS.VALIDATING
    } else if (subscriptionError) {
      return SUBSCRIPTION_STATUS.ERROR
    } else if (!userSubscription) {
      return SUBSCRIPTION_STATUS.FREE
    } else if (isSubscriptionExpired) {
      return SUBSCRIPTION_STATUS.EXPIRED
    } else {
      return SUBSCRIPTION_STATUS.NONE
    }
  }

  const getSubscriptionButton = () => {
    const status = getSubscriptionStatus()

    if (
      status === SUBSCRIPTION_STATUS.FREE ||
      status === SUBSCRIPTION_STATUS.EXPIRED
    ) {
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

  // Helper function to format expiration date with server-validated expiration status
  const getExpirationInfo = () => {
    if (!userSubscription) return null

    const expirationDate = new Date(userSubscription.expiration)

    if (isValidatingExpiration) {
      return (
        <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
          (Validating expiration...)
        </Typography>
      )
    }

    if (isSubscriptionExpired) {
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

  const subscriptionButton = getSubscriptionButton()

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
        <TitleData>Subscription</TitleData>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
          }}
        >
          <BoxData>
            {userSubscription?.plan_name ?? SUBSCRIPTION_PLAN.FREE}
          </BoxData>
          {getExpirationInfo()}
        </Box>
        <Button
          variant="contained"
          color={subscriptionButton.color}
          sx={{ ml: 2 }}
          onClick={subscriptionButton.action}
          disabled={subscriptionLoading || isValidatingExpiration}
        >
          {subscriptionButton.text}
        </Button>
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
